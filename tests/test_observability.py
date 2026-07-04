"""Unit tests for kinozal_scraper.observability.init_sentry (#278)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pytest

from kinozal_scraper.observability import init_sentry


def _record_initializer(calls: list[dict]) -> Callable[..., None]:
    def _init(**kwargs: object) -> None:
        calls.append(kwargs)

    return _init


class TestInitSentry:
    def test_no_dsn_is_noop_and_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        calls: list[dict] = []
        with caplog.at_level(logging.INFO):
            result = init_sentry(env={}, initializer=_record_initializer(calls))
        assert result is False
        assert calls == []
        assert any("disabled" in r.message.lower() for r in caplog.records)

    def test_blank_dsn_treated_as_absent(self) -> None:
        calls: list[dict] = []
        result = init_sentry(env={"SENTRY_DSN": "   "}, initializer=_record_initializer(calls))
        assert result is False
        assert calls == []

    def test_dsn_present_initializes_once(self) -> None:
        calls: list[dict] = []
        result = init_sentry(
            env={"SENTRY_DSN": "http://k@example.invalid/1", "SENTRY_ENVIRONMENT": "staging"},
            initializer=_record_initializer(calls),
        )
        assert result is True
        assert len(calls) == 1
        assert calls[0]["dsn"] == "http://k@example.invalid/1"
        assert calls[0]["environment"] == "staging"

    def test_environment_defaults_to_production(self) -> None:
        calls: list[dict] = []
        init_sentry(
            env={"SENTRY_DSN": "http://k@example.invalid/1"},
            initializer=_record_initializer(calls),
        )
        assert calls[0]["environment"] == "production"

    def test_init_failure_does_not_raise_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """S1: a bad SENTRY_DSN (BadDsn) must NOT crash the pipeline — monitor is not
        load-bearing for product delivery."""

        def _boom(**kwargs: object) -> None:
            raise ValueError("bad dsn")

        with caplog.at_level(logging.ERROR):
            result = init_sentry(
                env={"SENTRY_DSN": "http://k@example.invalid/1"}, initializer=_boom
            )
        assert result is False
        assert any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.fixture
def _sentry_teardown() -> Iterator[None]:
    yield
    import sentry_sdk

    try:
        sentry_sdk.get_global_scope().set_client(None)
    except Exception:  # noqa: BLE001 — best-effort teardown across sentry-sdk versions
        sentry_sdk.init()  # inert no-DSN client


class TestCaptureMechanism:
    """B1: pins the load-bearing sentry-sdk default — default LoggingIntegration turns
    ``logger.exception(...)`` into an *event with a stacktrace*. If a sentry-sdk bump flips
    that default, this reddens instead of alerts silently dying."""

    def test_logger_exception_becomes_event_with_stacktrace(self, _sentry_teardown: None) -> None:
        import sentry_sdk
        from sentry_sdk.types import Event, Hint

        captured: list[Event] = []

        def _before_send(event: Event, _hint: Hint) -> Event | None:
            captured.append(event)
            return None  # drop -> no network I/O

        sentry_sdk.init(dsn="http://k@example.invalid/1", before_send=_before_send)
        logger = logging.getLogger("kinozal_scraper.tests.mechanism")
        try:
            raise ValueError("boom-503")
        except ValueError:
            logger.exception("source failed")
        sentry_sdk.flush()

        assert len(captured) == 1
        assert "exception" in captured[0]  # stacktrace attached, not just the message
