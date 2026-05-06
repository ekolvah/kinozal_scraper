from __future__ import annotations

import logging
from typing import Any

from curl_cffi import requests

from generic_pipeline import (
    ROW_HEADERS,
    build_notification,
    extract_from_html,
)
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

logger = logging.getLogger(__name__)


def _fetch_html(url: str) -> str:
    resp = requests.get(url, impersonate="chrome120", timeout=30)
    resp.raise_for_status()
    return resp.text


def run_events_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> None:
    config = sources_config or load_sources_config()
    event_sources = [
        s for s in config["sources"] if s.get("enabled") and s["id"].startswith("soldout_")
    ]
    if not event_sources:
        logger.info("no enabled soldout sources found")
        return

    for source in event_sources:
        url: str = source.get("url", "")
        if not url:
            logger.warning("[%s] no URL configured (set SOLDOUT_URL)", source["id"])
            continue

        try:
            html_text = _fetch_html(url)
        except Exception as exc:
            logger.error("[%s] fetch failed: %s", source["id"], exc)
            continue

        result = extract_from_html(html_text, source)
        if not result.ok:
            logger.error("[%s] extraction errors: %s", source["id"], result.errors)
            continue

        sheet_tab: str = source["sheet_tab"]
        existing = storage.get_existing_keys(sheet_tab)
        new_items = [i for i in result.items if i.dedupe_key not in existing]
        if not new_items:
            logger.info("[%s] no new items", source["id"])
            continue

        storage.append_rows(sheet_tab, ROW_HEADERS, [i.to_row() for i in new_items])

        template: str = source["message_template"]
        notifications = [build_notification(item, template) for item in new_items]
        sent, failed = notifier.send_items(notifications)
        if failed:
            logger.warning("[%s] %d notification(s) failed", source["id"], len(failed))
        logger.info("[%s] sent %d notification(s)", source["id"], len(sent))


if __name__ == "__main__":
    import json
    import os
    import sys

    import gspread

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from sheets_storage import SheetsStorage
    from telegram_notifier import TelegramNotifier

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([bot_token, chat_id]):
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        sys.exit(1)

    gc = gspread.service_account_from_dict(json.loads(os.environ["CREDENTIALS"]))
    prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
    prod_notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)
    run_events_pipeline(prod_storage, prod_notifier)
