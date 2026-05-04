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


def _fetch_json(url: str, params: dict[str, str], headers: dict[str, str]) -> Any:
    clean_headers = {k: v for k, v in headers.items() if v and not v.endswith(" ")}
    resp = requests.get(url, params=params, headers=clean_headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _unwrap_records(data: Any, json_path: str | None) -> list[dict[str, Any]]:
    """Navigate into the response to find the records array."""
    if json_path is None:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values()) if all(isinstance(v, dict) for v in data.values()) else []
        return []
    obj: Any = data
    for key in json_path.split("."):
        obj = obj.get(key, []) if isinstance(obj, dict) else []
    return obj if isinstance(obj, list) else []


def run_json_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> None:
    config = sources_config or load_sources_config()
    json_sources = [s for s in config["sources"] if s.get("enabled") and s["type"] == "json"]
    if not json_sources:
        logger.info("no enabled json sources found")
        return

    for source in json_sources:
        try:
            _run_single_source(source, storage, notifier)
        except Exception as exc:
            logger.error("[%s] unhandled error: %s", source["id"], exc)
            continue


def _run_single_source(source: dict[str, Any], storage: Storage, notifier: Notifier) -> None:
    source_id = source["id"]

    data = _fetch_json(
        source["url"],
        source.get("params", {}),
        source.get("headers", {}),
    )

    records = _unwrap_records(data, source.get("json_path"))

    sort_key = source.get("sort_by")
    if sort_key:
        records.sort(
            key=lambda r: int(r.get(sort_key) or 0), reverse=source.get("sort_reverse", False)
        )

    result = extract_from_json(records, source)
    if not result.ok:
        logger.error("[%s] extraction errors: %s", source_id, result.errors)
        return

    tab = source["sheet_tab"]
    existing = storage.get_existing_keys(tab)
    new_items = [i for i in result.items if i.dedupe_key not in existing]
    if not new_items:
        logger.info("[%s] no new items", source_id)
        return

    template = source["message_template"]
    notifications = [build_notification(item, template) for item in new_items]
    sent, failed = notifier.send_items(notifications)

    if sent:
        sent_ids = {n.id for n in sent}
        items_to_store = [i for i in new_items if i.dedupe_key in sent_ids]
        storage.append_rows(tab, ROW_HEADERS, [i.to_row() for i in items_to_store])

    if failed:
        logger.warning(
            "[%s] %d notification(s) failed, will retry next run", source_id, len(failed)
        )


if __name__ == "__main__":
    import json
    import os

    import gspread

    from sheets_storage import SheetsStorage
    from telegram_notifier import TelegramNotifier

    gc = gspread.service_account_from_dict(json.loads(os.environ["CREDENTIALS"]))
    prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
    prod_notifier = TelegramNotifier(
        bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
    )
    run_json_pipeline(prod_storage, prod_notifier)
