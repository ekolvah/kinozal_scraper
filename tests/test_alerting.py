"""Tests for alerting.py — operator-facing per-source failure alerts (#310).

`report_failures` surfaces `PipelineResult` failures to Telegram with source_id +
error class (§IV: visible AND actionable), reusing the technical-alert marker that
gates the workflow-level curl fallback. The marker is job-global — it means "at
least one rich alert delivered this run"; on a *second* send-failure the backstop
is the red run + logs (§III), NOT the curl step (architect-review B1, issue #310).
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from kinozal_scraper import alerting
from kinozal_scraper.alerting import format_pipeline_failures, report_failures
from kinozal_scraper.generic_pipeline import PipelineResult
from kinozal_scraper.telegram_notifier import InMemoryNotifier


def _ok(source_id: str) -> PipelineResult:
    return PipelineResult(source_id=source_id)


def _failed(source_id: str, error: str) -> PipelineResult:
    result = PipelineResult(source_id=source_id)
    result.errors.append(error)
    return result


class TestFormatPipelineFailures:
    def test_lists_source_id_and_first_error(self) -> None:
        text = format_pipeline_failures(
            [_ok("steam"), _failed("soldout", "fetch failed: HTTP Error 403")]
        )
        assert "soldout" in text
        assert "fetch failed: HTTP Error 403" in text
        assert "steam" not in text  # ok source is not listed

    def test_truncates_beyond_ten(self) -> None:
        results = [_failed(f"src{i}", f"err{i}") for i in range(12)]
        text = format_pipeline_failures(results)
        assert "src11" not in text  # 12th failure not individually listed
        assert "ещё 2" in text  # 12 - 10

    def test_escapes_html_in_error(self) -> None:
        text = format_pipeline_failures([_failed("s", "<b> & </b>")])
        assert "&lt;b&gt;" in text
        assert "&amp;" in text
        assert "<b>" not in text


class TestReportFailures:
    def test_all_ok_no_send_no_mark_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker = tmp_path / "technical_alert_sent"
        monkeypatch.setenv("TECH_ALERT_MARKER", str(marker))
        notifier = InMemoryNotifier()

        assert report_failures(notifier, [_ok("a"), _ok("b")]) is False
        assert notifier.texts == []
        assert not marker.exists()

    def test_failure_sends_alert_marks_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker = tmp_path / "technical_alert_sent"
        monkeypatch.setenv("TECH_ALERT_MARKER", str(marker))
        notifier = InMemoryNotifier()

        assert report_failures(notifier, [_ok("a"), _failed("soldout", "boom")]) is True
        assert len(notifier.texts) == 1
        assert "soldout" in notifier.texts[0]
        assert marker.exists()

    def test_send_failure_does_not_mark_returns_true(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        marker = tmp_path / "technical_alert_sent"
        monkeypatch.setenv("TECH_ALERT_MARKER", str(marker))
        notifier = InMemoryNotifier(fail_text=True)

        with caplog.at_level(logging.ERROR):
            assert report_failures(notifier, [_failed("soldout", "boom")]) is True

        # Delivery failed → marker NOT written, so the curl fallback stays as the
        # net for this first failure; the failure is still surfaced (ERROR log).
        assert not marker.exists()
        assert any(record.levelno >= logging.ERROR for record in caplog.records)

    def test_marker_write_failure_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Parent of the marker path is a regular file → mkdir/write raises; the
        # alert path must swallow it (logged), never propagate.
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("TECH_ALERT_MARKER", str(blocker / "marker"))
        notifier = InMemoryNotifier()

        assert report_failures(notifier, [_failed("soldout", "boom")]) is True


class TestConfigRejectionAlert:
    """#340: a rotator that accumulated `config_rejected_models` (a systematic
    Gemini 400 INVALID_ARGUMENT — our request is malformed) must reach the
    operator: `alert_config_rejections` sends a Telegram alert + marks the
    technical marker, and the caller reds the job (sys.exit 1)."""

    def test_alert_sent_and_marked_when_models_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker = tmp_path / "technical_alert_sent"
        monkeypatch.setenv("TECH_ALERT_MARKER", str(marker))
        notifier = InMemoryNotifier()
        enricher = SimpleNamespace(config_rejected_models=frozenset({"models/gemini-3.6-flash"}))

        assert alerting.alert_config_rejections(notifier, enricher) is True
        assert len(notifier.texts) == 1
        assert "gemini-3.6-flash" in notifier.texts[0]
        assert marker.exists()

    def test_no_alert_when_none_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker = tmp_path / "technical_alert_sent"
        monkeypatch.setenv("TECH_ALERT_MARKER", str(marker))
        notifier = InMemoryNotifier()
        # A non-rotator enricher (e.g. NullEnricher) lacks the attribute entirely.
        assert alerting.alert_config_rejections(notifier, object()) is False
        assert notifier.texts == []
        assert not marker.exists()
