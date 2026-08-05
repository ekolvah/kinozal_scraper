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

# How many dedupe_keys a drift WARNING names before collapsing into a count. The
# warning is per page, not per row (see `_warn_on_drift`), so this only bounds how
# much of the evidence is quoted inline.
_DRIFT_KEYS_IN_WARNING = 5


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
    """Rows the page offered, before extraction dropped any — the `fetched` metric.

    Counted here rather than threaded out of `extract_from_html` so the extractor
    keeps one job; one extra parse of a page we already have in memory costs
    milliseconds once per run."""
    if not row_selector:
        return 0
    return len(BeautifulSoup(html, "html.parser").select(row_selector))


def _warn_on_drift(source_id: str, items: list[NormalizedItem]) -> None:
    """Surface empty metric/description so page-layout drift reaches the operator
    instead of silently shipping blank fields (§IV).

    Runs over **every extracted row, before deduplication**. Scoping it to the
    items we deliver would go silent exactly in the steady state where the whole
    page is already known — selector rot would then be invisible on precisely the
    quiet days #459 exists to make readable.

    Aggregated rather than one line per row: drift is a property of the page, not
    of an item, so `all rows have an empty metric` is both a stronger signal and a
    bounded one now that the full page is searched instead of the first `limit`
    rows. A few keys are named so the operator can open one and look."""
    if not items:
        return
    for field_name in ("metric", "description"):
        blank = [i.dedupe_key for i in items if not getattr(i, field_name)]
        if not blank:
            continue
        scope = "all" if len(blank) == len(items) else f"{len(blank)}/{len(items)}"
        shown = ", ".join(blank[:_DRIFT_KEYS_IN_WARNING])
        if len(blank) > _DRIFT_KEYS_IN_WARNING:
            shown += f", … (+{len(blank) - _DRIFT_KEYS_IN_WARNING} more)"
        logger.warning(
            "[%s] %s rows have an empty %s — page layout may have drifted: %s",
            source_id,
            scope,
            field_name,
            shown,
        )


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
) -> PipelineResult | None:
    """Run one source end-to-end. Returns its PipelineResult, or None for the
    no-url silent-skip (the source produces no result entry — behaviour
    preserved byte-for-byte from the pre-split inline `continue`)."""
    url: str = source.get("url", "")
    if not url:
        logger.warning("[%s] no URL configured", source["id"])
        return None  # None = no-url silent-skip, preserved from pre-refactor

    result = PipelineResult(source_id=source["id"])
    metrics = SourceMetrics()
    result.metrics = metrics
    try:
        html_text = fetch_html(url)
    except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
        logger.exception("[%s] fetch failed: %s", source["id"], exc)
        result.errors.append(f"fetch failed: {exc}")
        return result

    metrics.fetched = _count_rows(html_text, source.get("row_selector", ""))
    # limit=0: the trending page has no upstream pagination, so the page IS the
    # whole candidate set. Truncating it to `limit` before dedup was #459's root
    # cause on this side — a new repo below position `limit` stayed invisible
    # forever, because dedup can only ever shrink what extraction handed it.
    extracted = extract_from_html(html_text, source, limit=0)
    if not extracted.items and extracted.errors:
        logger.error("[%s] extraction errors: %s", source["id"], extracted.errors)
        result.errors.extend(extracted.errors)
        return result

    items = _normalize_items(extracted.items)
    _enrich_with_stars_today(html_text, items)
    _warn_on_drift(source["id"], items)

    result.items = items
    metrics.extracted = len(items)

    sheet_tab: str = source["sheet_tab"]
    existing = storage.get_existing_keys(sheet_tab)
    new_items, metrics.existing, metrics.new = select_new_items(
        items, existing, int(source["limit"])
    )
    if not new_items:
        logger.info("[%s] no new items", source["id"])
        return result

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
    return result


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
        result = _process_trending_source(source, storage, notifier, enricher)
        if result is not None:  # None = no-url silent-skip, preserved from pre-refactor
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
