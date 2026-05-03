from __future__ import annotations

import logging
from typing import Any

import requests

from generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    Notification,
    build_notification,
    extract_from_html,
)
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

logger = logging.getLogger(__name__)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 6.1; WOW64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/46.0.2490.80 Safari/537.36"
    ),
    "Content-Type": "text/html",
}


def _fetch_html(url: str) -> str:
    resp = requests.get(url, headers=_FETCH_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def enrich_with_trailer(item: NormalizedItem, youtube: Any) -> str:
    """Clean title and look up a YouTube trailer URL. Returns '' on any failure."""
    try:
        clean = item.title.split("/")[0].strip().split("(")[0].strip()
        return youtube.get_trailer_url(clean) or ""
    except Exception as exc:
        logger.error("trailer lookup failed for %r: %s", item.title, exc)
        return ""


def run_kinozal_pipeline(
    storage: Storage,
    notifier: Notifier,
    youtube: Any,
    sources_config: dict[str, Any] | None = None,
) -> None:
    config = sources_config or load_sources_config()
    kinozal_sources = [
        s for s in config["sources"] if s.get("enabled") and s["id"].startswith("kinozal_")
    ]
    if not kinozal_sources:
        logger.info("no enabled kinozal sources found")
        return

    source_map = {s["id"]: s for s in kinozal_sources}

    all_items: list[NormalizedItem] = []
    for source in kinozal_sources:
        try:
            html_text = _fetch_html(source["url"])
        except Exception as exc:
            logger.error("[%s] fetch failed: %s", source["id"], exc)
            continue
        result = extract_from_html(html_text, source)
        if not result.ok:
            logger.error("[%s] extraction errors: %s", source["id"], result.errors)
            continue
        all_items.extend(result.items)

    if not all_items:
        logger.info("kinozal pipeline: no items extracted")
        return

    existing = storage.get_existing_keys("movies")
    new_items = [i for i in all_items if i.dedupe_key not in existing]
    if not new_items:
        logger.info("kinozal pipeline: no new items")
        return

    # Write to storage BEFORE sending notifications to prevent duplicates on crash
    storage.append_rows("movies", ROW_HEADERS, [i.to_row() for i in new_items])

    notifications: list[Notification] = []
    for item in new_items:
        item.trailer_url = enrich_with_trailer(item, youtube)
        template = source_map[item.source_id]["message_template"]
        notifications.append(build_notification(item, template))

    sent, failed = notifier.send_items(notifications)
    if failed:
        logger.warning("kinozal pipeline: %d notification(s) failed", len(failed))
