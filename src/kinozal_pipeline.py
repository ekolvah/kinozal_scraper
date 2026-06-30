"""Извлечение/нормализация топа kinozal.tv + обогащение трейлером (run_kinozal_pipeline)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from curl_cffi.requests import Session as _MirrorSession

from generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    Notification,
    PipelineResult,
    build_notification,
    extract_from_html,
)
from http_fetch import fetch_html
from kinozal_auth import fetch_authenticated, login
from pipeline_config import load_sources_config
from sheets_storage import Storage
from telegram_notifier import Notifier

logger = logging.getLogger(__name__)


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


_MIRROR_HOST = "kinozal.guru"


def _mirror_url(url: str) -> str:
    """Map a kinozal.tv page URL to its kinozal.guru mirror — host swap, the
    path and query (top.php filters) preserved."""
    return urlunsplit(urlsplit(url)._replace(netloc=_MIRROR_HOST))


class _KinozalFetcher:
    """Anonymous kinozal.tv primary with a lazy authenticated kinozal.guru
    mirror fallback.

    The mirror is hit only when a primary fetch raises (e.g. kinozal.tv 522),
    and login happens at most once per run, on the first fallback — so a healthy
    .tv run pays no login cost and needs no credentials. When credentials are
    absent or partial the mirror is disabled and the primary failure propagates
    as before, surfacing visibly (§IV)."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._mirror_enabled = bool(username) and bool(password)
        self._session: _MirrorSession | None = None
        self._login_error: str | None = None

    def fetch(self, url: str) -> str:
        try:
            return fetch_html(url)
        except Exception as primary_exc:  # noqa: BLE001 — any primary-fetch failure falls back to the mirror
            return self._from_mirror(url, primary_exc)

    def _from_mirror(self, url: str, primary_exc: Exception) -> str:
        if not self._mirror_enabled:
            raise RuntimeError(f"{primary_exc} (mirror fallback disabled — credentials not set)")
        session = self._ensure_login()
        mirror_url = _mirror_url(url)
        try:
            html = fetch_authenticated(session, mirror_url)
        except Exception as mirror_exc:
            raise RuntimeError(
                f"primary failed ({primary_exc}); mirror {mirror_url} also failed ({mirror_exc})"
            ) from mirror_exc
        logger.info(
            "[kinozal] primary %s failed (%s) — served from mirror %s",
            url,
            primary_exc,
            mirror_url,
        )
        return html

    def _ensure_login(self) -> _MirrorSession:
        if self._session is not None:
            return self._session
        if self._login_error is not None:
            raise RuntimeError(f"mirror login failed earlier: {self._login_error}")
        try:
            self._session = login(self._username, self._password)
        except Exception as exc:
            # Cache ANY login failure (bad creds → KinozalLoginError, but also
            # transport errors like a timeout if kinozal.guru is itself under
            # Cloudflare distress) so the "login at most once per run" guarantee
            # holds — otherwise every subsequent URL retries a dead login,
            # costing N×timeout seconds.
            self._login_error = str(exc)
            logger.error("kinozal mirror login failed: %s", exc)  # noqa: TRY400 — re-raised as RuntimeError with `from exc`; traceback surfaces at the isolation boundary
            raise RuntimeError(f"mirror login failed: {exc}") from exc
        return self._session


def _kinozal_title(raw: str) -> str:
    """Drop ' / original / year / format' suffix from raw kinozal anchor title."""
    return raw.split(" / ")[0].strip()


def _extract_kinozal_items(html: str, source: dict[str, Any]) -> PipelineResult:
    """Parse kinozal HTML and return PipelineResult with clean titles and raw dedupe_keys.

    Returns the underlying `extract_from_html` result (errors included) so the
    runner can propagate failures to its own PipelineResult. Earlier revision
    swallowed `extract_from_html` errors and returned `[]`, hiding HTML drift
    from `__main__`'s exit-code surface.

    Items with an empty `url` after extraction still go through — the user sees
    a notification without a link, reports it, and we fix the drift. Silently
    dropping them would just look like "no new films" to the user. The WARNING
    is the dev-side tripwire for the same situation in logs.
    """
    result = extract_from_html(html, source)
    if not result.ok:
        logger.error("[%s] extraction errors: %s", source["id"], result.errors)
        return result
    for item in result.items:
        if not item.url:
            logger.warning(
                "[%s] item %r has empty url field, check sources.json fields.url",
                source["id"],
                item.title,
            )
        item.raw["kinozal_raw_title"] = item.dedupe_key
        item.title = _kinozal_title(item.title)
    return result


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
    except Exception as exc:  # noqa: BLE001 — trailer lookup degrades to no-trailer, item still notified
        logger.exception("trailer lookup failed for %r: %s", item.title, exc)
        return ""


def run_kinozal_pipeline(
    storage: Storage,
    notifier: Notifier,
    youtube: Any,
    sources_config: dict[str, Any] | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []
    config = sources_config or load_sources_config()
    kinozal_sources = [
        s for s in config["sources"] if s.get("enabled") and s["id"].startswith("kinozal_")
    ]
    if not kinozal_sources:
        logger.info("no enabled kinozal sources found")
        return results

    source_map = {s["id"]: s for s in kinozal_sources}

    # URLs come from the existing URLS env variable (same format as legacy scraper).
    # sources.json url field is only a schema placeholder / local fallback.
    urls = _kinozal_urls()
    if not urls:
        logger.error("kinozal pipeline: no URLs configured (set URLS or KINOZAL_TOP_URL)")
        for source in kinozal_sources:
            result = PipelineResult(source_id=source["id"])
            result.errors.append("no URLs configured (set URLS or KINOZAL_TOP_URL)")
            results.append(result)
        return results

    # Primary transport is anonymous kinozal.tv; the authenticated kinozal.guru
    # mirror is a lazy fallback used only when a primary fetch fails (e.g. 522).
    # A healthy .tv run needs no credentials and pays no login cost. Partial
    # credentials disable the fallback with a visible WARNING rather than redden
    # an otherwise-healthy run (§IV/§VI).
    username = os.environ.get("KINOZAL_USERNAME", "")
    password = os.environ.get("KINOZAL_PASSWORD", "")
    if bool(username) != bool(password):
        logger.warning(
            "kinozal: partial credentials — mirror fallback disabled "
            "(set BOTH KINOZAL_USERNAME and KINOZAL_PASSWORD)"
        )
    fetcher = _KinozalFetcher(username, password)

    # Fetch HTML for every (source × url) pair, recording per-source fetch and
    # extraction errors. Items keep their source_id from extract_from_html so
    # the per-source result below picks them up correctly.
    all_items: list[NormalizedItem] = []
    for source in kinozal_sources:
        result = PipelineResult(source_id=source["id"])
        for url in urls:
            try:
                html_text = fetcher.fetch(url)
            except Exception as exc:  # noqa: BLE001 — per-URL isolation: logged + surfaced via result.errors
                logger.exception("[%s] fetch failed for %s: %s", source["id"], url, exc)
                result.errors.append(f"fetch failed for {url}: {exc}")
                continue
            extracted = _extract_kinozal_items(html_text, source)
            if not extracted.ok:
                result.errors.extend(extracted.errors)
                continue
            all_items.extend(extracted.items)
        results.append(result)

    if not all_items:
        logger.info("kinozal pipeline: no items extracted")
        return results

    raw_count = len(all_items)
    all_items = _normalize_items(all_items)
    # Re-attach items to their per-source result so callers can inspect coverage.
    items_by_source: dict[str, list[NormalizedItem]] = {}
    for item in all_items:
        items_by_source.setdefault(item.source_id, []).append(item)
    for result in results:
        result.items = items_by_source.get(result.source_id, [])

    existing = storage.get_existing_keys("movies")
    new_items = [i for i in all_items if i.dedupe_key not in existing]
    # Visibility (§IV): log coverage on every run — including the common "0 new"
    # path below — so a vanished film reads in the Actions log instead of looking
    # like "no new films". raw_count is pre-normalize, exposing dedup-collapse.
    logger.info(
        "kinozal pipeline: %d extracted (%d after dedup-collapse), %d new, %d already-seen",
        raw_count,
        len(all_items),
        len(new_items),
        len(all_items) - len(new_items),
    )
    if not new_items:
        logger.info("kinozal pipeline: no new items")
        return results

    notifications: list[Notification] = []
    for item in new_items:
        item.trailer_url = enrich_with_trailer(item, youtube)
        template = source_map[item.source_id]["message_template"]
        notifications.append(build_notification(item, template))

    # Persist only confirmed-delivered items (Principle III). Failed deliveries
    # stay unstored so the next run retries them, and surface as a visible
    # anomaly via result.errors + non-zero exit (Principle IV).
    sent, failed = notifier.send_items(notifications)

    if sent:
        sent_ids = {n.id for n in sent}
        items_to_store = [i for i in new_items if i.dedupe_key in sent_ids]
        storage.append_rows("movies", ROW_HEADERS, [i.to_row() for i in items_to_store])

    if failed:
        result_by_source = {r.source_id: r for r in results}
        item_by_key = {i.dedupe_key: i for i in new_items}
        for notif in failed:
            # notif.id is always a new_item dedupe_key whose source has a result,
            # so both lookups must succeed — a KeyError here is a real bug.
            source_id = item_by_key[notif.id].source_id
            message = f"notification delivery failed for {notif.id!r}, will retry next run"
            logger.error("[%s] %s", source_id, message)
            result_by_source[source_id].errors.append(message)

    return results


if __name__ == "__main__":
    import json
    import sys

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
    prod_results = run_kinozal_pipeline(storage, notifier, youtube)

    if any(not r.ok for r in prod_results):
        sys.exit(1)
