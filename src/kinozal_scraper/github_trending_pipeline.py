"""GitHub Trending + stars-today (run_github_trending_pipeline)."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from kinozal_scraper.gemini_enricher import FALLBACK_MARKER, Enricher, QuotaExhausted
from kinozal_scraper.generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    PipelineResult,
    SourceMetrics,
    bounded_evidence,
    build_notification,
    extract_from_html,
    select_new_items,
)
from kinozal_scraper.http_fetch import fetch_html
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import Storage
from kinozal_scraper.telegram_notifier import Notifier

# Match the longest sequence of digits-with-optional-commas in a string.
# Used to turn "14,113" → "14113" and "1,690 stars today" → "1690".
_DIGITS_RE = re.compile(r"[\d,]+")

logger = logging.getLogger(__name__)


_SOURCE_ID = "github_trending"
_SHEET_TAB = "github_projects"

# Fields whose blank value is anomalous on its own, so *any* blank earns the drift
# verdict. Every trending row carries an `a[href$="/stargazers"]` link, so a missing
# `metric` is never routine — 24 blanks out of 25 is a page-wide drift, and requiring
# a 100%-blank column would let it through at INFO. `description` is the opposite:
# repos without one are ordinary, so only an entirely blank column is evidence.
_DRIFT_ON_ANY_BLANK = frozenset({"metric"})


def _digits_only(text: str) -> str:
    """Extract first run of digits (commas stripped). Returns "" if none."""
    if not text:
        return ""
    match = _DIGITS_RE.search(text)
    return match.group(0).replace(",", "") if match else ""


def _normalize_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    """Strip the leading `/` from `dedupe_key` (and mirror into `title`), and
    normalise `metric` to a digit-only string.

    The trending page exposes `h2 a@href` as `/owner/repo`; we drop the slash
    so the stored key matches `github_new_popular`'s `full_name` shape and the
    shared `github_projects` tab can dedupe cross-source. The `metric` field
    is extracted from `a[href$="/stargazers"]` and arrives as a
    locale-formatted number ("14,113") which we strip to digits only so the
    shared `github_projects.metric` column matches `github_new_popular`'s
    integer-string shape (see docs/architecture/storage.md).
    """
    for item in items:
        item.dedupe_key = item.dedupe_key.lstrip("/")
        item.title = item.dedupe_key
        item.metric = _digits_only(item.metric)
    return items


def _enrich_with_stars_today(html: str, items: list[NormalizedItem]) -> None:
    """Populate `item.raw["stars_today"]` for each item from the trending HTML.

    The daily-delta is shown on the trending page in
    `span.d-inline-block.float-sm-right` as text like "1,690 stars today".
    It is NOT a column on the shared `github_projects` Sheets tab (where
    `metric` means total stars — invariant from #86). We surface the daily
    value only through the notification template, by stashing it in `raw`
    keyed by `stars_today` so the template can reference `{stars_today}`.

    Missing or unparseable element → empty string (notification template
    will render "(+ today)" which the operator can still spot as drift).
    """
    soup = BeautifulSoup(html, "html.parser")
    by_href: dict[str, str] = {}
    for row in soup.select("article.Box-row"):
        link = row.select_one("h2 a")
        if not link or not link.get("href"):
            continue
        delta_el = row.select_one("span.d-inline-block.float-sm-right")
        by_href[str(link["href"]).strip()] = _digits_only(
            delta_el.get_text(strip=True) if delta_el else ""
        )
    for item in items:
        # item.dedupe_key was already normalised to "owner/repo" — restore
        # the leading slash to match the original href used as map key.
        key = "/" + item.dedupe_key if not item.dedupe_key.startswith("/") else item.dedupe_key
        item.raw["stars_today"] = by_href.get(key, "")


def _count_rows(html: str, row_selector: str) -> int:
    """How many rows the page offered, before the source's `limit` and extraction.

    Counted here rather than threaded out of `extract_from_html` so the extractor
    keeps one job; one extra parse of a page we already have in memory costs
    milliseconds once per run."""
    if not row_selector:
        return 0
    return len(BeautifulSoup(html, "html.parser").select(row_selector))


def _warn_on_drift(result: PipelineResult, items: list[NormalizedItem]) -> None:
    """Surface empty metric/description so page-layout drift reaches the operator
    instead of silently shipping blank fields (§IV).

    Runs over every extracted row **before deduplication**. Scoping it to the items
    we deliver would go silent exactly in the steady state where the whole top-N is
    already known — selector rot would then be invisible on precisely the quiet days
    this visibility work exists for (#459).

    Aggregated rather than one line per row: drift is a property of the page, not of
    an item, so `all rows have an empty metric` is both a stronger signal and a
    bounded one. A few keys are named so the operator can open one and look.

    **The drift verdict has a per-field threshold** (`_DRIFT_ON_ANY_BLANK`): one
    blank `metric` is already anomalous, while a blank `description` is routine and
    only an entirely blank column is evidence. One shared threshold cannot serve
    both — "any blank" would cry drift on nearly every run and train the operator
    to ignore the channel; "all blank" would let 24 of 25 missing metrics through
    at INFO. Below the threshold the count is still recorded, just without the
    verdict.
    """
    if not items:
        return
    for field_name in ("metric", "description"):
        blank = [i.dedupe_key for i in items if not getattr(i, field_name)]
        if not blank:
            continue
        detail = f"{len(blank)} of {len(items)} rows have an empty {field_name}"
        if field_name in _DRIFT_ON_ANY_BLANK or len(blank) == len(items):
            message = f"{detail} — page layout may have drifted: {bounded_evidence(blank)}"
            # Also on `result.warnings`: drift leaves no gap between `fetched` and
            # `extracted`, so a summary reader has no cue to go looking for it. The
            # log alone is the "only trace was a line in the job log" problem the
            # run summary exists to remove (#459).
            logger.warning("[%s] %s", result.source_id, message)
            result.warnings.append(message)
        else:
            logger.info("[%s] %s: %s", result.source_id, detail, bounded_evidence(blank))


def _enrich_new_items(
    enricher: Enricher,
    new_items: list[NormalizedItem],
    enrich_config: dict[str, Any],
    source_id: str,
) -> None:
    """Enrich each new item in place; on QuotaExhausted stamp the fallback on
    the current item and every remaining one, then stop (mutates the shared
    NormalizedItem objects, so the caller sees the results)."""
    field: str = enrich_config["field"]
    # Empty `on_error` would blank the summary line in Telegram
    # instead of surfacing the failure — use the visible marker
    # so the operator sees a tripwire (#128, Principle IV).
    fallback: str = enrich_config.get("on_error") or FALLBACK_MARKER
    enriched, skipped = 0, 0
    for item in new_items:
        try:
            item.raw[field] = enricher.enrich(item, enrich_config)
            enriched += 1
        except QuotaExhausted:
            item.raw[field] = fallback
            skipped += 1
            for remaining in new_items[new_items.index(item) + 1 :]:
                remaining.raw[field] = fallback
                skipped += 1
            break
    if skipped:
        logger.warning(
            "[%s] enrichment quota exhausted: %d/%d enriched, %d skipped",
            source_id,
            enriched,
            enriched + skipped,
            skipped,
        )
    elif enriched:
        logger.info("[%s] enriched %d items", source_id, enriched)


def _process_trending_source(
    source: dict[str, Any],
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None,
    result: PipelineResult,
    metrics: SourceMetrics,
) -> None:
    """Run one source end-to-end, recording everything on the caller's
    `result`/`metrics`.

    Owns no result of its own on purpose — mirror of `github_popular_pipeline`: the
    caller's per-source catch-all has to be able to publish whatever was counted
    (and whatever warnings were collected) before an exception escaped."""
    sheet_tab: str = source["sheet_tab"]
    # Read BEFORE fetching, mirror of `github_popular_pipeline`: nothing may fail
    # between `extracted` being counted and the `existing`/`new` split being written
    # from it, or the published line breaks its own `extracted == existing + new`
    # invariant (`get_existing_keys` raises `SchemaError` on a tab with missing
    # columns). One Sheets read on an already-red run is the whole cost.
    existing = storage.get_existing_keys(sheet_tab)

    url: str = source["url"]
    try:
        html_text = fetch_html(url)
    except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
        logger.exception("[%s] fetch failed: %s", source["id"], exc)
        result.errors.append(f"fetch failed: {exc}")
        return

    # Rows the extractor was handed: the page truncated to the source's own
    # `limit`, which is the top-N of today's trending list — the product intent.
    # Reading the whole page instead was tried and reverted: it turns "top 10
    # trending" into "any 10 rows we have not seen", i.e. positions 11..25.
    metrics.fetched = min(
        _count_rows(html_text, source.get("row_selector", "")), int(source["limit"])
    )
    extracted = extract_from_html(html_text, source)
    if not extracted.items and extracted.errors:
        logger.error("[%s] extraction errors: %s", source["id"], extracted.errors)
        result.errors.extend(extracted.errors)
        return
    if extracted.errors:
        # Partial extraction stays green (the rows that parsed are worth delivering),
        # but it must not stay *silent*: these errors used to be dropped on the
        # floor, so `fetched=25 extracted=23` looked like a clean run. Warnings,
        # not errors — reddening the run for two malformed rows out of 25 would
        # make the whole source unavailable over a cosmetic page change.
        logger.warning(
            "[%s] %d of %d row(s) failed extraction: %s",
            source["id"],
            len(extracted.errors),
            metrics.fetched,
            bounded_evidence(extracted.errors),
        )
        result.warnings.extend(extracted.errors)

    items = _normalize_items(extracted.items)
    _enrich_with_stars_today(html_text, items)
    _warn_on_drift(result, items)

    result.items = items
    metrics.extracted = len(items)
    new_items, metrics.existing, metrics.new = select_new_items(items, existing)
    if not new_items:
        logger.info("[%s] no new items", source["id"])
        return

    enrich_config = source.get("enrich")
    if enrich_config and enricher is not None:
        _enrich_new_items(enricher, new_items, enrich_config, source["id"])

    template: str = source["message_template"]
    notifications = [build_notification(item, template) for item in new_items]
    sent, failed = notifier.send_items(notifications)
    metrics.sent = len(sent)

    if sent:
        sent_ids = {n.id for n in sent}
        items_to_store = [i for i in new_items if i.dedupe_key in sent_ids]
        storage.append_rows(sheet_tab, ROW_HEADERS, [i.to_row() for i in items_to_store])
        metrics.stored = len(items_to_store)

    if failed:
        message = f"{len(failed)} notification(s) failed, will retry next run"
        logger.error("[%s] %s", source["id"], message)
        result.errors.append(message)
    logger.info("[%s] sent %d notification(s)", source["id"], len(sent))


def run_github_trending_pipeline(
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None = None,
    sources_config: dict[str, Any] | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []
    config = sources_config or load_sources_config()
    trending_sources = [s for s in config["sources"] if s.get("enabled") and s["id"] == _SOURCE_ID]
    if not trending_sources:
        logger.info("no enabled '%s' source found", _SOURCE_ID)
        return results

    for source in trending_sources:
        if not source.get("url"):
            # No-url skip: no result entry at all, so nothing reaches the run summary
            # either — preserved byte-for-byte from the pre-split inline `continue`.
            #
            # Unreachable today only because this source's `url` is a literal in
            # `sources.json`. Config validation is NOT the guard: it checks key
            # *presence* (`_REQUIRED_SOURCE_FIELDS - source.keys()`), so an empty
            # string passes. `soldout` proves the path is live — its url is
            # `{{SOLDOUT_URL}}`, which `build_macro_context` defaults to `""`, and it
            # skips green the same way. (`kinozal` is not a case: it never reads the
            # config url, and a missing one gives it a red result with a reason.)
            #
            # So: parameterising this url would reintroduce a silent green skip, the
            # exact shape #459 removes. Give it a `PipelineResult` with the reason on
            # `warnings` if that ever happens.
            logger.warning("[%s] no URL configured", source["id"])
            continue

        # Built HERE, so the catch-all cannot discard what was already counted —
        # including the drift and partial-extraction warnings collected for the
        # summary. Mirror of `github_popular_pipeline`; the failure surface is the
        # same (`send_items`/`append_rows`/enricher raising), and it is the path
        # where delivery may already have happened (§IV).
        result = PipelineResult(source_id=source["id"])
        metrics = SourceMetrics()
        result.metrics = metrics
        try:
            _process_trending_source(source, storage, notifier, enricher, result, metrics)
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] unhandled error: %s", source["id"], exc)
            result.errors.append(f"unhandled error: {exc}")
        results.append(result)

    return results


if __name__ == "__main__":
    import json
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    dry_run = os.environ.get("GITHUB_TRENDING_DRY_RUN") == "1"
    sources_path = os.environ.get("GITHUB_TRENDING_SOURCES_PATH")

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not dry_run and not all([bot_token, chat_id]):
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        sys.exit(1)

    if sources_path:
        sources_config: dict[str, Any] | None = load_sources_config(sources_path)
    else:
        sources_config = None

    if dry_run:
        from kinozal_scraper.sheets_storage import InMemoryStorage
        from kinozal_scraper.telegram_notifier import InMemoryNotifier

        prod_storage: Storage = InMemoryStorage()
        prod_notifier: Notifier = InMemoryNotifier()
    else:
        import gspread

        from kinozal_scraper.sheets_storage import SheetsStorage
        from kinozal_scraper.telegram_notifier import TelegramNotifier

        gc = gspread.service_account_from_dict(json.loads(os.environ["CREDENTIALS"]))
        prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
        prod_notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    from kinozal_scraper.gemini_enricher import build_default_enricher

    prod_enricher = build_default_enricher(os.environ.get("GOOGLE_API_KEY", ""), logger)

    prod_results = run_github_trending_pipeline(
        prod_storage,
        prod_notifier,
        enricher=prod_enricher,
        sources_config=sources_config,
    )

    from kinozal_scraper.alerting import (
        alert_config_rejections,
        publish_run_summary,
        report_failures,
    )

    # Before the exit-code branch below: a failed run is exactly when the counters
    # are worth reading, and publishing after `sys.exit(1)` would drop them (#459).
    publish_run_summary(prod_results)

    # Evaluate both (no short-circuit) so a config-reject alert fires even when
    # sources all succeeded; either reddens the job (#340).
    rejected = alert_config_rejections(prod_notifier, prod_enricher)
    failures = report_failures(prod_notifier, prod_results)
    if rejected or failures:
        sys.exit(1)
