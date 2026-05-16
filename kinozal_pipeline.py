from __future__ import annotations

import logging
import os
import re
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


def _kinozal_urls() -> list[str]:
    """Read Kinozal URLs from the existing URLS env variable (format: 'label|url;...').

    Falls back to KINOZAL_TOP_URL if URLS is not set, so the runner works both
    in production (URLS already configured) and in local testing.
    """
    urls_env = os.environ.get("URLS", "")
    if urls_env:
        return [pair.split("|")[1] for pair in urls_env.split(";") if "|" in pair]
    fallback = os.environ.get("KINOZAL_TOP_URL", "")
    return [fallback] if fallback else []


def _kinozal_title(raw: str) -> str:
    """Drop ' / original / year / format' suffix from raw kinozal anchor title."""
    return raw.split(" / ")[0].strip()


def _extract_kinozal_items(html: str, source: dict[str, Any]) -> list[NormalizedItem]:
    """Parse kinozal HTML and return items with clean titles and raw dedupe_keys."""
    result = extract_from_html(html, source)
    if not result.ok:
        logger.error("[%s] extraction errors: %s", source["id"], result.errors)
        return []
    for item in result.items:
        item.raw["kinozal_raw_title"] = item.dedupe_key
        item.title = _kinozal_title(item.title)
    return result.items


def _normalize_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    """Deduplicate by clean title and normalize dedupe_key to match.

    Multiple repacks of the same title (Portable, FitGirl, etc.) share
    the same item.title after _extract_kinozal_items. This collapses them
    to one item and stores the clean title as the dedup key so future runs
    also skip all repacks of an already-notified title.
    """
    seen: set[str] = set()
    result: list[NormalizedItem] = []
    for item in items:
        if item.title in seen:
            logger.debug("[kinozal] duplicate title collapsed: %r", item.title)
            continue
        seen.add(item.title)
        item.dedupe_key = item.title
        result.append(item)
    return result


def enrich_with_trailer(item: NormalizedItem, youtube: Any) -> str:
    """Look up a YouTube trailer URL. Returns '' on any failure.

    Expects item.title to already be cleaned (no ' / ' separators).
    Year is read from item.raw['kinozal_raw_title'] (the original @title
    attribute) because the clean title may have had the year stripped.
    """
    try:
        clean = item.title.split("(")[0].strip()
        raw_for_year = item.raw.get("kinozal_raw_title", item.dedupe_key)
        year_match = re.search(r"\b(20\d{2})\b", raw_for_year)
        year = int(year_match.group(1)) if year_match else None
        return youtube.get_trailer_url(clean, year=year) or ""
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

    # URLs come from the existing URLS env variable (same format as legacy scraper).
    # sources.json url field is only a schema placeholder / local fallback.
    urls = _kinozal_urls()
    if not urls:
        logger.error("kinozal pipeline: no URLs configured (set URLS or KINOZAL_TOP_URL)")
        return

    all_items: list[NormalizedItem] = []
    for source in kinozal_sources:
        for url in urls:
            try:
                html_text = _fetch_html(url)
            except Exception as exc:
                logger.error("[%s] fetch failed for %s: %s", source["id"], url, exc)
                continue
            all_items.extend(_extract_kinozal_items(html_text, source))

    if not all_items:
        logger.info("kinozal pipeline: no items extracted")
        return

    all_items = _normalize_items(all_items)

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


if __name__ == "__main__":
    import json

    import gspread

    from sheets_storage import SheetsStorage
    from telegram_notifier import TelegramNotifier
    from youtube import Youtube

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    credentials = json.loads(os.environ["CREDENTIALS"])
    gc = gspread.service_account_from_dict(credentials)

    storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
    notifier = TelegramNotifier(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
    )
    youtube = Youtube()
    run_kinozal_pipeline(storage, notifier, youtube)
