"""Steam charts + appdetails + перевод (run_steam_pipeline)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from kinozal_scraper.gemini_enricher import FALLBACK_MARKER, Enricher, QuotaExhausted
from kinozal_scraper.generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    PipelineResult,
    build_notification,
    extract_from_json,
)
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import Storage
from kinozal_scraper.telegram_notifier import Notifier

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "steam_charts"
_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

# `last_week_rank: -1` is the API's sentinel for new entries; we surface a
# human-friendly token via the template (see test_new_entry_last_week_normalised).
_NEW_ENTRY_TOKEN = "new"


def _fetch_charts(url: str) -> dict[str, Any]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _fetch_appdetails(appid: int) -> dict[str, Any] | None:
    """Return the `data` block of Steam Store appdetails (basic filter), or None
    if the appid is unknown / returns `success: false`."""
    resp = requests.get(
        _APPDETAILS_URL,
        params={"appids": str(appid), "filters": "basic"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    entry = payload.get(str(appid)) or {}
    if not entry.get("success"):
        return None
    data: dict[str, Any] = entry.get("data") or {}
    return data


def _resolve_name(appid: int, source_id: str) -> tuple[str, str]:
    """Resolve `(name, short_description)` for an appid.

    1. `appdetails?filters=basic` — full payload (name + short_description).
    2. `f"⚠️ Game #{appid}"` — last-resort placeholder. The ⚠️ marker (same
       convention as `FALLBACK_MARKER`) makes the degradation a *visible*
       anomaly in Telegram rather than a silent-looking title, so the operator
       notices instead of mistaking it for a real game (Principle IV,
       see [[feedback_visibility_over_silence]]).

    The former `GetAppList` second level was dropped: `ISteamApps/GetAppList/v2`
    was deprecated 2025-11-25 and returns 404 permanently, so it could only ever
    yield an empty index (see #146).

    A WARNING is logged on placeholder so cron logs flag drift, but the item is
    never dropped from the notification stream.
    """
    try:
        details = _fetch_appdetails(appid)
    except Exception as exc:  # noqa: BLE001 — appdetails failure degrades to None, item still notified
        logger.warning("[%s] appdetails fetch failed for %s: %s", source_id, appid, exc)
        details = None
    if details and details.get("name"):
        name: str = details["name"]
        return name, details.get("short_description", "")

    placeholder = f"⚠️ Game #{appid}"
    logger.warning("[%s] no name for %s — sending as '%s'", source_id, appid, placeholder)
    return placeholder, ""


def _enrich_with_appdetails(
    source_id: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Populate `name`/`short_description` on every record; nothing is dropped.

    Earlier revision dropped items whose `appdetails` returned `success: false`
    — that produced silent gaps in Telegram for entire chart positions during
    Steam flaps. Now each record reaches the notifier with at minimum a
    placeholder name (see `_resolve_name` for the fallback chain).
    """
    enriched: list[dict[str, Any]] = []
    for rec in records:
        appid = rec.get("appid")
        if appid is None:
            logger.warning("[%s] record without appid: %s", source_id, rec)
            continue
        name, description = _resolve_name(int(appid), source_id)
        rec["name"] = name
        rec["short_description"] = description
        rec["store_url"] = f"https://store.steampowered.com/app/{appid}"
        if rec.get("last_week_rank") == -1:
            rec["last_week_rank"] = _NEW_ENTRY_TOKEN
        enriched.append(rec)
    return enriched


def _apply_translation(
    source: dict[str, Any],
    items: list[NormalizedItem],
    enricher: Enricher | None,
    source_id: str,
) -> None:
    """Populate `item.raw[field]` with translation (or English fallback).

    Steam-specific fallback policy: unlike GitHub sources where missing
    `summary_ru` becomes `FALLBACK_MARKER`, here the original English
    `short_description` IS itself informative — degrading silently to the
    marker would hide useful text. So any failure (no enricher, empty
    result, FALLBACK_MARKER from TruncatedResponse, QuotaExhausted) falls
    back to `item.description`. Notification still ships (Principle IV).
    """
    enrich_config = source.get("enrich")
    if not enrich_config:
        return
    field = enrich_config["field"]
    if enricher is None:
        for item in items:
            item.raw[field] = item.description
        return

    enriched = 0
    quota_dead = False
    for item in items:
        if quota_dead:
            item.raw[field] = item.description
            continue
        try:
            result = enricher.enrich(item, enrich_config)
        except QuotaExhausted:
            logger.warning(
                "[%s] enrichment quota exhausted at %s — remaining items "
                "fall back to original description",
                source_id,
                item.dedupe_key,
            )
            item.raw[field] = item.description
            quota_dead = True
            continue
        if not result or result == FALLBACK_MARKER:
            logger.warning(
                "[%s] item %s fell back to English (enricher returned empty/marker)",
                source_id,
                item.dedupe_key,
            )
            item.raw[field] = item.description
        else:
            item.raw[field] = result
            enriched += 1
    if enriched:
        logger.info("[%s] translated %d/%d items", source_id, enriched, len(items))


def run_steam_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
    enricher: Enricher | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []

    config = sources_config or load_sources_config()
    steam_sources = [
        s for s in config["sources"] if s.get("enabled") and s.get("type") == _SOURCE_TYPE
    ]
    if not steam_sources:
        logger.info("no enabled '%s' source found", _SOURCE_TYPE)
        return results

    # Thin loop: each source is isolated so an *unhandled* error in one (e.g. a
    # malformed rank, a Sheets API hiccup mid-pipeline) becomes a not-ok result
    # instead of aborting the whole run. Mirrors `run_github_popular_pipeline`.
    for source in steam_sources:
        try:
            result = _run_single_source(source, storage, notifier, enricher)
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] unhandled error: %s", source["id"], exc)
            result = PipelineResult(source_id=source["id"])
            result.errors.append(f"unhandled error: {exc}")
        results.append(result)

    return results


def _run_single_source(
    source: dict[str, Any],
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None,
) -> PipelineResult:
    """Process one steam_charts source through the 5 stages, returning its
    `PipelineResult`. Stage guards short-circuit with `return result` (not
    `continue`) so the caller's loop stays the isolation boundary."""
    source_id: str = source["id"]
    url: str = source["url"]
    result = PipelineResult(source_id=source_id)

    # 1. Fetch the Most Played charts payload.
    try:
        data = _fetch_charts(url)
    except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
        logger.exception("[%s] charts fetch failed: %s", source_id, exc)
        result.errors.append(f"charts fetch failed: {exc}")
        return result

    # 2. Validate the rank list before slicing.
    ranks = data.get("response", {}).get("ranks", [])
    if not isinstance(ranks, list) or not ranks:
        logger.error("[%s] empty 'response.ranks' in charts payload", source_id)
        result.errors.append("empty 'response.ranks' in charts payload")
        return result

    limit = int(source.get("limit", len(ranks)))
    top_n = ranks[:limit]

    # 3. Resolve names/descriptions via appdetails (visible ⚠️ marker on miss).
    enriched = _enrich_with_appdetails(source_id, top_n)
    if not enriched:
        logger.error("[%s] no usable records after enrichment", source_id)
        result.errors.append("no usable records after enrichment")
        return result

    # 4. Normalise records into NormalizedItem rows.
    extracted = extract_from_json(enriched, source)
    if not extracted.items:
        logger.error("[%s] extraction errors: %s", source_id, extracted.errors)
        result.errors.extend(extracted.errors or ["extraction yielded no items"])
        return result
    result.items = extracted.items

    # 5. Dedup against the sheet, translate, notify, then persist only delivered.
    sheet_tab: str = source["sheet_tab"]
    existing = storage.get_existing_keys(sheet_tab)
    new_items = [i for i in result.items if i.dedupe_key not in existing]
    if not new_items:
        logger.info("[%s] no new items", source_id)
        return result

    _apply_translation(source, new_items, enricher, source_id)

    template: str = source["message_template"]
    notifications = [build_notification(item, template) for item in new_items]

    # Persist only confirmed-delivered items (Principle III); failed sends
    # stay unstored to retry next run and surface as result.errors.
    sent, failed = notifier.send_items(notifications)

    if sent:
        sent_ids = {n.id for n in sent}
        items_to_store = [i for i in new_items if i.dedupe_key in sent_ids]
        storage.append_rows(sheet_tab, ROW_HEADERS, [i.to_row() for i in items_to_store])

    if failed:
        message = f"{len(failed)} notification(s) failed, will retry next run"
        logger.error("[%s] %s", source_id, message)
        result.errors.append(message)
    logger.info("[%s] sent %d notification(s)", source_id, len(sent))
    return result


if __name__ == "__main__":
    import json as _json
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    dry_run = os.environ.get("STEAM_DRY_RUN") == "1"
    sources_path = os.environ.get("STEAM_SOURCES_PATH")

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not dry_run and not all([bot_token, chat_id]):
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        sys.exit(1)

    if sources_path:
        prod_config: dict[str, Any] | None = load_sources_config(sources_path)
    else:
        prod_config = None

    if dry_run:
        from kinozal_scraper.sheets_storage import InMemoryStorage
        from kinozal_scraper.telegram_notifier import InMemoryNotifier

        prod_storage: Storage = InMemoryStorage()
        prod_notifier: Notifier = InMemoryNotifier()
    else:
        import gspread

        from kinozal_scraper.sheets_storage import SheetsStorage
        from kinozal_scraper.telegram_notifier import TelegramNotifier

        gc = gspread.service_account_from_dict(_json.loads(os.environ["CREDENTIALS"]))
        prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
        prod_notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    from kinozal_scraper.gemini_enricher import build_default_enricher

    prod_enricher = build_default_enricher(os.environ.get("GOOGLE_API_KEY", ""), logger)

    prod_results = run_steam_pipeline(
        prod_storage, prod_notifier, sources_config=prod_config, enricher=prod_enricher
    )

    from kinozal_scraper.alerting import alert_config_rejections, report_failures

    # Evaluate both (no short-circuit) so a config-reject alert fires even when
    # sources all succeeded; either reddens the job (#340).
    rejected = alert_config_rejections(prod_notifier, prod_enricher)
    failures = report_failures(prod_notifier, prod_results)
    if rejected or failures:
        sys.exit(1)
