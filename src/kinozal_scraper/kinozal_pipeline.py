"""Извлечение/нормализация топа kinozal.tv + обогащение трейлером (run_kinozal_pipeline)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from curl_cffi.requests import Session as _MirrorSession

from kinozal_scraper.generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    Notification,
    PipelineResult,
    build_notification,
    extract_from_html,
)
from kinozal_scraper.http_fetch import fetch_bytes, fetch_html
from kinozal_scraper.kinozal_auth import fetch_authenticated, login
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import Storage
from kinozal_scraper.telegram_notifier import Notifier, TelegramNotifier

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


_ORIGIN_HOST = "kinozal.tv"
_MIRROR_HOST = "kinozal.guru"
_KINOZAL_HOSTS = frozenset({_ORIGIN_HOST, _MIRROR_HOST})


def _mirror_url(url: str) -> str:
    """Map a kinozal.tv page URL to its kinozal.guru mirror — host swap, the
    path and query (top.php filters) preserved."""
    return urlunsplit(urlsplit(url)._replace(netloc=_MIRROR_HOST))


def _origin(url: str) -> str:
    """scheme://host of a URL — the base against which relative links/posters in
    that listing must resolve (#247). Derived from the URL actually fetched, so
    it follows origin→mirror failover instead of a hardcoded canonical host."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class Kinozal:
    """Facade for all kinozal IO: anonymous kinozal.tv primary with a lazy
    kinozal.guru mirror fallback. One object owns the origin-vs-mirror decision
    so consumers (the pipeline, the notifier's poster download) stay host-agnostic
    — no split where the listing comes from the mirror but the poster keeps
    hitting the dead origin (#241).

    HTML listings use the authenticated mirror (login at most once per run, on
    the first fallback) — so a healthy .tv run pays no login cost and needs no
    credentials. Posters use the mirror *anonymously* (kinozal.guru serves
    /i/poster/ 200 without login, verified). When credentials are absent or
    partial the HTML mirror is disabled and the primary failure propagates,
    surfacing visibly (§IV)."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._mirror_enabled = bool(username) and bool(password)
        self._session: _MirrorSession | None = None
        self._login_error: str | None = None

    @classmethod
    def from_env(cls) -> Kinozal:
        """Build from KINOZAL_USERNAME/PASSWORD, warning on partial credentials.

        Single home for the credential read + partial-creds WARNING so both the
        default `run_kinozal_pipeline` path and `__main__` share it (the WARNING
        used to live inline in the runner)."""
        username = os.environ.get("KINOZAL_USERNAME", "")
        password = os.environ.get("KINOZAL_PASSWORD", "")
        if bool(username) != bool(password):
            logger.warning(
                "kinozal: partial credentials — mirror fallback disabled "
                "(set BOTH KINOZAL_USERNAME and KINOZAL_PASSWORD)"
            )
        return cls(username, password)

    def fetch_listing(self, url: str) -> tuple[str, str]:
        """Return (html, effective_base_url): the HTML plus the origin that
        actually served it (#247). Primary success → the requested origin
        (kinozal.tv); mirror fallback → kinozal.guru. The pipeline resolves the
        listing's relative links/posters against this base, so a mirror-served
        page yields .guru links (live for the logged-in user) instead of dead
        .tv ones — reversing #227/#241's fixed canonical-origin choice."""
        try:
            return fetch_html(url), _origin(url)
        except Exception as primary_exc:  # noqa: BLE001 — any primary-fetch failure falls back to the mirror
            return self._from_mirror(url, primary_exc), _origin(_mirror_url(url))

    def fetch_poster(self, url: str) -> bytes:
        """Download a poster, sharing the listing's origin→mirror failover (#241).

        Try the URL as-is; on failure retry the kinozal.guru mirror ONLY when the
        URL is a kinozal host that is not already the mirror. A third-party host
        (e.g. an uploader's fastpic image) has no kinozal mirror, so its failure
        propagates and the notifier degrades to text + WARNING (§IV). A
        primary-on-.guru failure isn't re-swapped to the same host. The mirror
        poster fetch is anonymous — no _ensure_login, so one dead-origin poster
        on an otherwise-healthy run pays no login cost."""
        try:
            return fetch_bytes(url)
        except Exception as primary_exc:  # noqa: BLE001 — mirror-retry for kinozal hosts, else propagate to §IV degrade
            host = urlsplit(url).netloc
            if host not in _KINOZAL_HOSTS or host == _MIRROR_HOST:
                raise
            mirror_url = _mirror_url(url)
            logger.warning(
                "[kinozal] poster primary %s failed (%s) — retrying mirror %s",
                url,
                primary_exc,
                mirror_url,
            )
            return fetch_bytes(mirror_url)

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


def _build_notifier(bot_token: str, chat_id: str, kinozal: Kinozal) -> TelegramNotifier:
    """`__main__` factory: wire the kinozal mirror-aware poster fetcher into the
    notifier so posters share the listing's origin→mirror failover (#241).

    Extracted from `__main__` so the wiring itself is testable — a test that
    re-built the notifier by hand would only prove the seam, not that prod
    actually routes posters through `kinozal.fetch_poster` (the bug was a
    `__main__` that built the notifier *without* `image_fetcher`)."""
    return TelegramNotifier(bot_token, chat_id, image_fetcher=kinozal.fetch_poster)


def _kinozal_title(raw: str) -> str:
    """Drop ' / original / year / format' suffix from raw kinozal anchor title."""
    return raw.split(" / ")[0].strip()


def _extract_kinozal_items(
    html: str, source: dict[str, Any], base_url: str | None = None
) -> PipelineResult:
    """Parse kinozal HTML and return PipelineResult with clean titles and raw dedupe_keys.

    `base_url`, when given, overrides `source["base_url"]` for this one fetch so
    relative links AND posters resolve against the host that actually served the
    HTML (#247). `extract_from_html` resolves both `url` and `image_url` through
    the same base, so mirror-served posters follow to .guru for free. The source
    dict is shallow-copied, never mutated (it is shared across the run).

    Returns the underlying `extract_from_html` result (errors included) so the
    runner can propagate failures to its own PipelineResult. Earlier revision
    swallowed `extract_from_html` errors and returned `[]`, hiding HTML drift
    from `__main__`'s exit-code surface.

    Items with an empty `url` after extraction still go through — the user sees
    a notification without a link, reports it, and we fix the drift. Silently
    dropping them would just look like "no new films" to the user. The WARNING
    is the dev-side tripwire for the same situation in logs.
    """
    if base_url is not None:
        source = {**source, "base_url": base_url}
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
    # Covers listing fetches only. Poster mirror-routing lives in the notifier's
    # `image_fetcher`, so a caller passing `kinozal=` MUST also build the notifier
    # via `_build_notifier(bot_token, chat_id, kinozal)` — otherwise posters keep
    # hitting the dead origin (the #241 bug). `__main__` does both.
    kinozal: Kinozal | None = None,
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
    # an otherwise-healthy run (§IV/§VI) — see `Kinozal.from_env`. `__main__`
    # injects the same object it wires into the notifier, so the listing and its
    # posters share one origin-vs-mirror decision (#241).
    fetcher = kinozal or Kinozal.from_env()

    # Fetch HTML for every (source × url) pair, recording per-source fetch and
    # extraction errors. Items keep their source_id from extract_from_html so
    # the per-source result below picks them up correctly.
    all_items: list[NormalizedItem] = []
    for source in kinozal_sources:
        result = PipelineResult(source_id=source["id"])
        for url in urls:
            try:
                html_text, effective_base_url = fetcher.fetch_listing(url)
            except Exception as exc:  # noqa: BLE001 — per-URL isolation: logged + surfaced via result.errors
                logger.exception("[%s] fetch failed for %s: %s", source["id"], url, exc)
                result.errors.append(f"fetch failed for {url}: {exc}")
                continue
            # Resolve this listing's links/posters against the origin that served
            # it (.tv on primary, .guru on mirror fallback) — not a fixed host (#247).
            extracted = _extract_kinozal_items(html_text, source, base_url=effective_base_url)
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

    from kinozal_scraper.sheets_storage import SheetsStorage
    from kinozal_scraper.youtube import Youtube

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    credentials = json.loads(os.environ["CREDENTIALS"])
    gc = gspread.service_account_from_dict(credentials)

    storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
    # One Kinozal object wired into both the notifier (posters) and the pipeline
    # (listings) — single origin-vs-mirror decision for all kinozal IO (#241).
    kinozal = Kinozal.from_env()
    notifier = _build_notifier(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        kinozal,
    )
    youtube = Youtube()
    prod_results = run_kinozal_pipeline(storage, notifier, youtube, kinozal=kinozal)

    if any(not r.ok for r in prod_results):
        sys.exit(1)
