"""Operator-facing reporting — canonical domain (#310, expanded in #459).

Two channels answer what an operator learns about a run: failure **alerts**
(Telegram) and a run **summary** (log + GitHub Actions Step Summary). They have
different transports but one consumer, so they share a home; a separate summary
module would add a project-map entry, test file, and documentation section with no gain.

The alert half consolidates what previously lived only in `telegram_summarizer`:
the `.run/technical_alert_sent` marker (which gates generic curl fallback in
`run-script.yml`), alert-text delivery, and readable per-source scraping-pipeline
alerts (`source_id: <error>` rather than silent “run failed + link”).

**Marker topology is job-global.** All scrapers and the summarizer run as sequential
steps in one GH job with a shared workspace; the only consumer is the curl-step guard
`hashFiles(...) == ''`. The marker therefore means “≥1 rich alert was delivered in this
run,” not “this step delivered one.” If delivery of a second or later alert fails, the
backstop is a red run and logs (§III), not curl (architect-review B1). Per-step marker
infrastructure is deliberately absent.
"""

from __future__ import annotations

import html as _html
import logging
import os
from pathlib import Path
from typing import Any

from kinozal_scraper.generic_pipeline import (
    EVIDENCE_BOUND,
    PipelineResult,
    SourceMetrics,
    without_source_prefix,
)

logger = logging.getLogger(__name__)

_TECH_ALERT_MARKER = ".run/technical_alert_sent"


def mark_technical_alert_sent(path: str | None = None) -> None:
    marker_value = path if path is not None else os.getenv("TECH_ALERT_MARKER")
    marker = Path(marker_value or _TECH_ALERT_MARKER)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")


def send_required_text(notifier: Any, text: str) -> bool:
    ok = bool(notifier.send_text(text))
    if not ok:
        logger.error("Telegram delivery failed")
    return ok


def format_metrics_line(source_id: str, metrics: SourceMetrics) -> str:
    """One operator-readable line per source (#459).

    `new=0` used to be indistinguishable from "the source broke and returned
    nothing" — the only trace was an INFO line in the job log. The counts make
    the reason readable at a glance: `existing=93 new=0` says we looked at 93
    candidates and knew every one of them, which is a normal green run.
    """
    return (
        f"{source_id}: fetched={metrics.fetched} extracted={metrics.extracted} "
        f"existing={metrics.existing} new={metrics.new} "
        f"sent={metrics.sent} stored={metrics.stored}"
    )


def _annotations(source_id: str, label: str, messages: list[str]) -> list[str]:
    """Bounded `label: <message>` lines under a source's counters.

    Each line already opens with the source id, so a `[source_id]` prefix baked into
    the message itself (every `extract_from_*` error carries one) is stripped rather
    than printed twice."""
    shown = [without_source_prefix(source_id, m) for m in messages[:EVIDENCE_BOUND]]
    lines = [f"{source_id}:   {label}: {message}" for message in shown]
    if len(messages) > EVIDENCE_BOUND:
        lines.append(f"{source_id}:   {label}: ... and {len(messages) - EVIDENCE_BOUND} more")
    return lines


def publish_run_summary(results: list[PipelineResult]) -> None:
    """Log the metrics lines and append them to the GitHub Actions Step Summary.

    Counters are omitted rather than zeroed when `metrics is None` ("this pipeline
    does not measure"), because printing `fetched=0` would recreate the very
    ambiguity #459 removes (§IV). Warnings and errors are reported either way — the
    guard is about the counters, and coupling a source's *messages* to whether it
    happens to instrument counters would silence the channel by accident.

    Errors travel here as well as through `report_failures`: the summary is
    published before the exit code precisely so a failed run's numbers survive, and
    six counters with no stated reason is not a report an operator can act on.

    A summary that cannot be written degrades to a WARNING — it is a report
    channel, and losing it must not redden a run that otherwise succeeded.
    """
    lines: list[str] = []
    for result in results:
        if result.metrics is not None:
            lines.append(format_metrics_line(result.source_id, result.metrics))
        lines.extend(_annotations(result.source_id, "error", result.errors))
        lines.extend(_annotations(result.source_id, "warning", result.warnings))
    if not lines:
        return
    for line in lines:
        logger.info("%s", line)

    target = os.getenv("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write("```text\n" + "\n".join(lines) + "\n```\n")
    except OSError as exc:
        logger.warning("could not write run summary to %s: %s", target, exc)


def format_pipeline_failures(results: list[PipelineResult]) -> str:
    """Readable per-source alert: `source_id: <first error>` for each failure.

    Sibling of `telegram_summarizer.format_technical_alert`, but operates on
    `PipelineResult` (`source_id` + `errors`) rather than `ChannelProcessResult`.
    HTML escaping is required for Telegram `parse_mode=HTML` so `<`/`&` in errors
    cannot break its parser.
    """
    failed = [r for r in results if not r.ok]
    lines = [
        "⚠️ Ошибка пайплайна",
        "Источник упал — часть данных не собрана / не доставлена.",
        "",
    ]
    for result in failed[:10]:
        first = result.errors[0] if result.errors else "unknown error"
        # Same de-duplication of the source id as the run summary: the line already
        # names the source, so `- github_trending: [github_trending] …` reads twice.
        first = without_source_prefix(result.source_id, first)
        lines.append(f"- {_html.escape(result.source_id)}: {_html.escape(first)}")
    if len(failed) > 10:
        lines.append(f"... и ещё {len(failed) - 10} failure(s)")
    return "\n".join(lines)


def format_config_rejection_alert(models: frozenset[str]) -> str:
    """Readable alert for systematic Gemini config rejection (#340): models rejected
    our request with `400 INVALID_ARGUMENT`, a request bug rather than quota. HTML
    escaping supports Telegram `parse_mode=HTML`. Sibling of `format_pipeline_failures`."""
    lines = [
        "⚠️ Gemini config-reject",
        "Модель(и) отвергли запрос (400 INVALID_ARGUMENT) — баг запроса, не quota. "
        "Уведомления доставлены через ротацию, но это нужно чинить:",
        "",
    ]
    lines.extend(f"- {_html.escape(m)}" for m in sorted(models))
    return "\n".join(lines)


def alert_config_rejections(notifier: Any, enricher: Any) -> bool:
    """If the enricher (rotator) accumulated `config_rejected_models`, deliver an
    operator alert, mark the technical marker, and report whether it was sent.

    Caller uses `if alert_config_rejections(...) | report_failures(...): sys.exit(1)`:
    §IV makes systematic config rejection visible to the operator and redens the job
    although rotation has delivered notifications (#340). `getattr` supports a
    non-rotator (`NullEnricher`/`GeminiEnricher` lack the property → empty → False)."""
    models: frozenset[str] = getattr(enricher, "config_rejected_models", frozenset())
    if not models:
        return False
    if send_required_text(notifier, format_config_rejection_alert(models)):
        try:
            mark_technical_alert_sent()
        except Exception as exc:  # noqa: BLE001 — marker write failure must not crash the alert path
            logger.exception("Could not write technical alert marker: %s", exc)
    return True


def report_failures(notifier: Any, results: list[PipelineResult]) -> bool:
    """Send a readable alert for failed results; return whether failures occurred.

    Caller uses `if report_failures(...): sys.exit(1)`; §IV preserves the exit code.
    Mark only successful delivery (mirroring `deliver_results`): after `send_text`
    fails, do not write the marker, leave curl fallback available for that first
    undelivered alert, and expose the failure in the ERROR log.
    """
    failed = [r for r in results if not r.ok]
    if not failed:
        return False
    if send_required_text(notifier, format_pipeline_failures(results)):
        try:
            mark_technical_alert_sent()
        except Exception as exc:  # noqa: BLE001 — marker write failure must not crash the alert path
            logger.exception("Could not write technical alert marker: %s", exc)
    return True
