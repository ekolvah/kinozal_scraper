"""Пайплайн событий / sold-out (run_events_pipeline)."""

from __future__ import annotations

import logging
from typing import Any

from generic_pipeline import (
    ROW_HEADERS,
    PipelineResult,
    build_notification,
    extract_from_html,
)
from http_fetch import fetch_html
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

logger = logging.getLogger(__name__)


def run_events_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []
    config = sources_config or load_sources_config()
    event_sources = [
        s for s in config["sources"] if s.get("enabled") and s["id"].startswith("soldout_")
    ]
    if not event_sources:
        logger.info("no enabled soldout sources found")
        return results

    for source in event_sources:
        url: str = source.get("url", "")
        if not url:
            logger.warning("[%s] no URL configured (set SOLDOUT_URL)", source["id"])
            continue

        result = PipelineResult(source_id=source["id"])
        try:
            html_text = fetch_html(url)
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] fetch failed: %s", source["id"], exc)
            result.errors.append(f"fetch failed: {exc}")
            results.append(result)
            continue

        extracted = extract_from_html(html_text, source)
        if not extracted.ok:
            logger.error("[%s] extraction errors: %s", source["id"], extracted.errors)
            result.errors.extend(extracted.errors)
            results.append(result)
            continue
        result.items = extracted.items

        sheet_tab: str = source["sheet_tab"]
        existing = storage.get_existing_keys(sheet_tab)
        new_items = [i for i in result.items if i.dedupe_key not in existing]
        if not new_items:
            logger.info("[%s] no new items", source["id"])
            results.append(result)
            continue

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
            logger.error("[%s] %s", source["id"], message)
            result.errors.append(message)
        logger.info("[%s] sent %d notification(s)", source["id"], len(sent))
        results.append(result)

    return results


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
    prod_results = run_events_pipeline(prod_storage, prod_notifier)

    if any(not r.ok for r in prod_results):
        sys.exit(1)
