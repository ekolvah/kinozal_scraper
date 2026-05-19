from __future__ import annotations

import logging
from typing import Any

import requests

from generic_pipeline import (
    ROW_HEADERS,
    build_notification,
    extract_from_json,
)
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "steam_charts"
_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

# `last_week_rank: -1` is the API's sentinel for new entries; we surface a
# human-friendly token via the template (see test_new_entry_last_week_normalised).
_NEW_ENTRY_TOKEN = "new"

# Module-level failure flag — flipped True when any selected source produces
# zero items or fails to fetch. The __main__ block translates this to
# sys.exit(1) so the GitHub Actions step turns red (Principle IV, mirrors
# github_trending_pipeline._FAILED).
_FAILED = False


def _did_fail() -> bool:
    return _FAILED


def _reset_failure() -> None:
    """Test helper — clear the module-level failure flag between runs."""
    global _FAILED
    _FAILED = False


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


def _enrich_with_appdetails(source_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mutate records with `name`/`short_description`, drop those without a name.

    The Steam Charts API returns only appid + numeric stats — it does not
    carry game names, and `appdetails` doesn't support batch (verified 400
    on comma-separated appids). One HTTP call per item is the only path.
    Records that 404 or come back with `success: false` are dropped with a
    warning rather than skipped silently (see [[feedback_visibility_over_silence]]).
    """
    enriched: list[dict[str, Any]] = []
    for rec in records:
        appid = rec.get("appid")
        if appid is None:
            logger.warning("[%s] record without appid: %s", source_id, rec)
            continue
        try:
            details = _fetch_appdetails(int(appid))
        except Exception as exc:
            logger.warning("[%s] appdetails fetch failed for %s: %s", source_id, appid, exc)
            continue
        if not details or not details.get("name"):
            logger.warning("[%s] no name for appid %s", source_id, appid)
            continue
        rec["name"] = details["name"]
        rec["short_description"] = details.get("short_description", "")
        if rec.get("last_week_rank") == -1:
            rec["last_week_rank"] = _NEW_ENTRY_TOKEN
        enriched.append(rec)
    return enriched


def run_steam_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> None:
    global _FAILED
    _reset_failure()

    config = sources_config or load_sources_config()
    steam_sources = [
        s for s in config["sources"] if s.get("enabled") and s.get("type") == _SOURCE_TYPE
    ]
    if not steam_sources:
        logger.info("no enabled '%s' source found", _SOURCE_TYPE)
        return

    for source in steam_sources:
        source_id: str = source["id"]
        url: str = source["url"]

        try:
            data = _fetch_charts(url)
        except Exception as exc:
            logger.error("[%s] charts fetch failed: %s", source_id, exc)
            _FAILED = True
            continue

        ranks = data.get("response", {}).get("ranks", [])
        if not isinstance(ranks, list) or not ranks:
            logger.error("[%s] empty 'response.ranks' in charts payload", source_id)
            _FAILED = True
            continue

        limit = int(source.get("limit", len(ranks)))
        top_n = ranks[:limit]

        enriched = _enrich_with_appdetails(source_id, top_n)
        if not enriched:
            logger.error("[%s] all appdetails lookups failed", source_id)
            _FAILED = True
            continue

        result = extract_from_json(enriched, source)
        if not result.items:
            logger.error("[%s] extraction errors: %s", source_id, result.errors)
            _FAILED = True
            continue

        sheet_tab: str = source["sheet_tab"]
        existing = storage.get_existing_keys(sheet_tab)
        new_items = [i for i in result.items if i.dedupe_key not in existing]
        if not new_items:
            logger.info("[%s] no new items", source_id)
            continue

        storage.append_rows(sheet_tab, ROW_HEADERS, [i.to_row() for i in new_items])

        template: str = source["message_template"]
        notifications = [build_notification(item, template) for item in new_items]
        sent, failed = notifier.send_items(notifications)
        if failed:
            logger.warning("[%s] %d notification(s) failed", source_id, len(failed))
        logger.info("[%s] sent %d notification(s)", source_id, len(sent))


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

    run_steam_pipeline(prod_storage, prod_notifier, sources_config=prod_config)

    if _did_fail():
        sys.exit(1)
