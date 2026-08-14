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
from kinozal_scraper.alerting import (
    format_metrics_line,
    format_pipeline_failures,
    publish_run_summary,
    report_failures,
)
from kinozal_scraper.generic_pipeline import PipelineResult, SourceMetrics
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

    def test_source_id_is_not_printed_twice(self) -> None:
        # Every `extract_from_*` message opens with `[source_id]`, and the alert line
        # names the source itself — the Telegram surface strips it like the summary.
        text = format_pipeline_failures(
            [_failed("github_new_popular", "[github_new_popular] extraction produced zero items")]
        )
        assert "- github_new_popular: extraction produced zero items" in text
        assert "[github_new_popular]" not in text

    def test_message_without_the_prefix_is_untouched(self) -> None:
        # What makes the strip safe for `fetch failed:` / `unhandled error:` messages.
        text = format_pipeline_failures([_failed("soldout", "fetch failed: HTTP Error 403")])
        assert "- soldout: fetch failed: HTTP Error 403" in text

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
        assert "Уведомления доставлены через ротацию" not in notifier.texts[0]
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


def _measured(source_id: str, **counts: int) -> PipelineResult:
    result = PipelineResult(source_id=source_id)
    result.metrics = SourceMetrics(**counts)
    return result


class TestFormatMetricsLine:
    """#459: `new=0` used to be indistinguishable from "the source broke and
    returned nothing" — the only trace was an INFO line. The metrics line makes
    the reason readable without opening the job log."""

    def test_line_matches_operator_format(self) -> None:
        line = format_metrics_line(
            "github_new_popular",
            SourceMetrics(fetched=100, extracted=100, existing=93, new=7, sent=7, stored=7),
        )
        assert line == (
            "github_new_popular: fetched=100 extracted=100 existing=93 new=7 sent=7 stored=7"
        )

    def test_zero_new_still_reports_how_deep_we_looked(self) -> None:
        line = format_metrics_line(
            "github_trending", SourceMetrics(fetched=25, extracted=25, existing=25)
        )
        assert "fetched=25" in line
        assert "new=0" in line


class TestPublishRunSummary:
    def test_appends_line_when_env_var_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        publish_run_summary(
            [_measured("github_new_popular", fetched=3, extracted=3, new=3, sent=3)]
        )
        assert "github_new_popular" in target.read_text(encoding="utf-8")

    def test_no_env_var_logs_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        with caplog.at_level(logging.INFO, logger="kinozal_scraper.alerting"):
            publish_run_summary(
                [_measured("github_trending", fetched=25, extracted=25, existing=25)]
            )
        assert any("github_trending" in record.getMessage() for record in caplog.records)

    def test_unwritable_target_warns_and_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(blocker / "summary.md"))
        with caplog.at_level(logging.WARNING, logger="kinozal_scraper.alerting"):
            publish_run_summary([_measured("github_new_popular", fetched=1)])
        assert any(record.levelno >= logging.WARNING for record in caplog.records)

    def test_zero_new_still_publishes_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        publish_run_summary(
            [_measured("github_new_popular", fetched=100, extracted=100, existing=100)]
        )
        assert "new=0" in target.read_text(encoding="utf-8")

    def test_warnings_ride_along_so_a_metrics_gap_explains_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        result = _measured("github_trending", fetched=25, extracted=23, existing=23)
        result.warnings.append("[github_trending] row missing required field(s)")
        publish_run_summary([result])
        written = target.read_text(encoding="utf-8")
        assert "extracted=23" in written
        assert "row missing required field(s)" in written

    def test_warning_list_is_bounded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        result = _measured("github_trending", fetched=25, extracted=5)
        result.warnings.extend(f"row {i} broke" for i in range(20))
        publish_run_summary([result])
        written = target.read_text(encoding="utf-8")
        assert "and 15 more" in written
        assert "row 19 broke" not in written

    def test_errors_are_reported_beside_the_counters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The summary is published before the exit code precisely so a failed run's
        # numbers survive; six counters with no stated reason is not actionable.
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        result = _measured("github_new_popular", fetched=3, extracted=3)
        result.errors.append("fetch failed: network down")
        publish_run_summary([result])
        assert "fetch failed: network down" in target.read_text(encoding="utf-8")

    def test_warnings_survive_a_result_without_counters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The `metrics is None` guard is about the counters. Coupling a source's
        # messages to whether it instruments counters would silence the channel by
        # accident for the first pipeline that reports one without measuring.
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        result = _ok("soldout")
        result.warnings.append("something degraded")
        publish_run_summary([result])
        written = target.read_text(encoding="utf-8")
        assert "something degraded" in written
        assert "fetched=" not in written

    def test_source_id_is_not_printed_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every `extract_from_*` message already opens with `[source_id]`, and the
        # annotation line prefixes the source id itself.
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        result = _measured("github_trending", fetched=2, extracted=1)
        result.warnings.append("[github_trending] row missing required field(s)")
        publish_run_summary([result])
        written = target.read_text(encoding="utf-8")
        assert "warning: row missing required field(s)" in written
        assert "[github_trending]" not in written

    def test_uninstrumented_result_is_skipped_not_zeroed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # steam/kinozal/soldout do not measure; reporting them as all-zeros would
        # recreate the very "silence looks like an empty source" ambiguity #459 fixes.
        target = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
        publish_run_summary([_ok("steam"), _measured("github_trending", fetched=25)])
        written = target.read_text(encoding="utf-8")
        assert "steam" not in written
        assert "github_trending" in written
