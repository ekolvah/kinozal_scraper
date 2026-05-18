from __future__ import annotations

import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from gemini_enricher import Enricher, QuotaExhausted
from generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    build_notification,
    extract_from_html,
)
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

# Match the longest sequence of digits-with-optional-commas in a string.
# Used to turn "14,113" → "14113" and "1,690 stars today" → "1690".
_DIGITS_RE = re.compile(r"[\d,]+")

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


def run_github_trending_pipeline(
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None = None,
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
        _enrich_with_stars_today(html_text, items)
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

        enrich_config = source.get("enrich")
        if enrich_config and enricher is not None:
            field: str = enrich_config["field"]
            fallback: str = enrich_config.get("on_error", "")
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
                    source["id"],
                    enriched,
                    enriched + skipped,
                    skipped,
                )
            elif enriched:
                logger.info("[%s] enriched %d items", source["id"], enriched)

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

    # Mirror json_pipeline.__main__: build a RotatingGeminiEnricher when
    # GOOGLE_API_KEY is set so both GitHub sources get the same Russian
    # who/pain enrichment in one cron run. NullEnricher otherwise.
    from gemini_enricher import NullEnricher

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    prod_enricher: Enricher
    if api_key:
        import google.generativeai as genai

        from gemini_enricher import RotatingGeminiEnricher, get_generation_models

        genai.configure(api_key=api_key)
        available_models = get_generation_models()
        logger.info("available generation models: %s", available_models)
        if available_models:
            prod_enricher = RotatingGeminiEnricher(available_models)
        else:
            logger.warning("no generation models found, enrichment disabled")
            prod_enricher = NullEnricher()
    else:
        prod_enricher = NullEnricher()

    run_github_trending_pipeline(
        prod_storage,
        prod_notifier,
        enricher=prod_enricher,
        sources_config=sources_config,
    )

    if _did_fail():
        sys.exit(1)
