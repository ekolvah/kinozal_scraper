from __future__ import annotations

import logging
from typing import Any

import requests

from generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
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
}

_SOURCE_ID = "github_trending"
_SHEET_TAB = "github_projects"


# Module-level failure flag — flipped True when any selected source yields
# zero items with non-empty errors. The __main__ block translates this to
# sys.exit(1) so the GitHub Actions step turns red (Principle IV).
_FAILED = False


def _did_fail() -> bool:
    return _FAILED


def _reset_failure() -> None:
    """Test helper — clear the module-level failure flag between runs."""
    global _FAILED
    _FAILED = False


def _fetch_html(url: str) -> str:
    resp = requests.get(url, headers=_FETCH_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def _normalize_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    """Strip the leading `/` from `dedupe_key` (and mirror into `title`).

    The trending page exposes `h2 a@href` as `/owner/repo`; we drop the slash
    so the stored key matches `github_new_popular`'s `full_name` shape and the
    shared `github_projects` tab can dedupe cross-source.
    """
    for item in items:
        item.dedupe_key = item.dedupe_key.lstrip("/")
        item.title = item.dedupe_key
    return items


def run_github_trending_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> None:
    global _FAILED
    _reset_failure()

    config = sources_config or load_sources_config()
    trending_sources = [s for s in config["sources"] if s.get("enabled") and s["id"] == _SOURCE_ID]
    if not trending_sources:
        logger.info("no enabled '%s' source found", _SOURCE_ID)
        return

    for source in trending_sources:
        url: str = source.get("url", "")
        if not url:
            logger.warning("[%s] no URL configured", source["id"])
            continue

        try:
            html_text = _fetch_html(url)
        except Exception as exc:
            logger.error("[%s] fetch failed: %s", source["id"], exc)
            _FAILED = True
            continue

        result = extract_from_html(html_text, source)
        if not result.items and result.errors:
            logger.error("[%s] extraction errors: %s", source["id"], result.errors)
            _FAILED = True
            continue

        items = _normalize_items(result.items)
        for item in items:
            if not item.metric:
                logger.warning(
                    "[%s] item '%s' has empty metric — page layout may have drifted",
                    source["id"],
                    item.dedupe_key,
                )
            if not item.description:
                logger.warning(
                    "[%s] item '%s' has empty description",
                    source["id"],
                    item.dedupe_key,
                )

        sheet_tab: str = source["sheet_tab"]
        existing = storage.get_existing_keys(sheet_tab)
        new_items = [i for i in items if i.dedupe_key not in existing]
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
        from sheets_storage import InMemoryStorage
        from telegram_notifier import InMemoryNotifier

        prod_storage: Storage = InMemoryStorage()
        prod_notifier: Notifier = InMemoryNotifier()
    else:
        import gspread

        from sheets_storage import SheetsStorage
        from telegram_notifier import TelegramNotifier

        gc = gspread.service_account_from_dict(json.loads(os.environ["CREDENTIALS"]))
        prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
        prod_notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    run_github_trending_pipeline(prod_storage, prod_notifier, sources_config=sources_config)

    if _did_fail():
        sys.exit(1)
