from __future__ import annotations

import logging
from typing import Any

import requests

from gemini_enricher import FALLBACK_MARKER, Enricher, QuotaExhausted
from generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    PipelineResult,
    build_notification,
    extract_from_json,
)
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "steam_charts"
_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
_APPLIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"

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


def _fetch_app_name_index() -> dict[str, str]:
    """One-shot fallback name dictionary for the whole Steam catalogue.

    `ISteamApps/GetAppList/v2/` returns ~10MB JSON with every published app
    as `{appid, name}`. We hit it once per run and use it as a fallback when
    `appdetails` 500s / rate-limits / returns `success: false` for an item
    that's still legitimately in the charts. Keeps the operator from losing
    notifications during Steam flaps (see [[feedback_visibility_over_silence]]).
    """
    resp = requests.get(_APPLIST_URL, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    apps = payload.get("applist", {}).get("apps", [])
    return {str(a["appid"]): a["name"] for a in apps if "appid" in a and "name" in a}


def _resolve_name(appid: int, source_id: str, name_index: dict[str, str]) -> tuple[str, str]:
    """Resolve `(name, short_description)` for an appid via 2-level fallback.

    1. `appdetails?filters=basic` — full payload (name + short_description).
    2. `GetAppList` index — name only, no description.
    3. `f"Game #{appid}"` — last-resort placeholder so the item still reaches
       Telegram as a visible anomaly rather than a silent drop.

    A WARNING is logged at every fallback so cron logs flag drift, but the
    item is never dropped from the notification stream.
    """
    try:
        details = _fetch_appdetails(appid)
    except Exception as exc:
        logger.warning("[%s] appdetails fetch failed for %s: %s", source_id, appid, exc)
        details = None
    if details and details.get("name"):
        name: str = details["name"]
        return name, details.get("short_description", "")

    fallback_name = name_index.get(str(appid))
    if fallback_name:
        logger.warning(
            "[%s] appdetails missing for %s — using GetAppList name '%s'",
            source_id,
            appid,
            fallback_name,
        )
        return fallback_name, ""

    placeholder = f"Game #{appid}"
    logger.warning("[%s] no name anywhere for %s — sending as '%s'", source_id, appid, placeholder)
    return placeholder, ""


def _enrich_with_appdetails(
    source_id: str,
    records: list[dict[str, Any]],
    name_index: dict[str, str],
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
        name, description = _resolve_name(int(appid), source_id, name_index)
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

    for source in steam_sources:
        source_id: str = source["id"]
        url: str = source["url"]
        result = PipelineResult(source_id=source_id)

        try:
            data = _fetch_charts(url)
        except Exception as exc:
            logger.error("[%s] charts fetch failed: %s", source_id, exc)
            result.errors.append(f"charts fetch failed: {exc}")
            results.append(result)
            continue

        ranks = data.get("response", {}).get("ranks", [])
        if not isinstance(ranks, list) or not ranks:
            logger.error("[%s] empty 'response.ranks' in charts payload", source_id)
            result.errors.append("empty 'response.ranks' in charts payload")
            results.append(result)
            continue

        limit = int(source.get("limit", len(ranks)))
        top_n = ranks[:limit]

        try:
            name_index = _fetch_app_name_index()
        except Exception as exc:
            logger.warning(
                "[%s] GetAppList fetch failed: %s — appdetails-only mode", source_id, exc
            )
            name_index = {}

        enriched = _enrich_with_appdetails(source_id, top_n, name_index)
        if not enriched:
            logger.error("[%s] no usable records after enrichment", source_id)
            result.errors.append("no usable records after enrichment")
            results.append(result)
            continue

        extracted = extract_from_json(enriched, source)
        if not extracted.items:
            logger.error("[%s] extraction errors: %s", source_id, extracted.errors)
            result.errors.extend(extracted.errors or ["extraction yielded no items"])
            results.append(result)
            continue
        result.items = extracted.items

        sheet_tab: str = source["sheet_tab"]
        existing = storage.get_existing_keys(sheet_tab)
        new_items = [i for i in result.items if i.dedupe_key not in existing]
        if not new_items:
            logger.info("[%s] no new items", source_id)
            results.append(result)
            continue

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
        results.append(result)

    return results


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
        from sheets_storage import InMemoryStorage
        from telegram_notifier import InMemoryNotifier

        prod_storage: Storage = InMemoryStorage()
        prod_notifier: Notifier = InMemoryNotifier()
    else:
        import gspread

        from sheets_storage import SheetsStorage
        from telegram_notifier import TelegramNotifier

        gc = gspread.service_account_from_dict(_json.loads(os.environ["CREDENTIALS"]))
        prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
        prod_notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    from gemini_enricher import build_default_enricher

    prod_enricher = build_default_enricher(os.environ.get("GOOGLE_API_KEY", ""), logger)

    prod_results = run_steam_pipeline(
        prod_storage, prod_notifier, sources_config=prod_config, enricher=prod_enricher
    )

    if any(not r.ok for r in prod_results):
        sys.exit(1)
