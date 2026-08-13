"""kinozal.tv top extraction/normalization and trailer enrichment (run_kinozal_pipeline)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests import Session as _MirrorSession

from kinozal_scraper.generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    Notification,
    PipelineResult,
    build_notification,
    extract_from_html,
)
from kinozal_scraper.http_fetch import NotAnImageError, fetch_bytes, fetch_html
from kinozal_scraper.kinozal_auth import fetch_authenticated, login
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import Storage
from kinozal_scraper.telegram_notifier import Notifier, TelegramNotifier
from kinozal_scraper.text_utils import YEAR_SEGMENT_RE, original_title
from kinozal_scraper.trailer_strategy import FilmProfile, HeuristicStrategy
from kinozal_scraper.youtube import YoutubeQuotaExhausted

logger = logging.getLogger(__name__)

# Kinozal's browse.php?c= taxonomy, read from the authenticated mirror on
# 2026-08-13. The numeric marker on details.php is authoritative; this table
# only supplies operator-readable names. It is taxonomy, never delivery policy.
_KINOZAL_ITEM_CATEGORIES = {
    1: "Другое - Видеоклипы",
    2: "Другое - АудиоКниги",
    3: "Музыка - Буржуйская",
    4: "Музыка - Русская",
    5: "Музыка - Сборники",
    6: "Кино - Боевик / Военный",
    7: "Кино - Классика",
    8: "Кино - Комедия",
    9: "Кино - Исторический",
    10: "Кино - Наше Кино",
    11: "Кино - Приключения",
    12: "Кино - Детский / Семейный",
    13: "Кино - Фантастика",
    14: "Кино - Фэнтези",
    15: "Кино - Триллер / Детектив",
    16: "Кино - Эротика",
    17: "Кино - Драма",
    18: "Кино - Документальный",
    20: "Мульт - Аниме",
    21: "Мульт - Буржуйский",
    22: "Мульт - Русский",
    23: "Другое - Игры",
    24: "Кино - Ужас / Мистика",
    32: "Другое - Программы",
    35: "Кино - Мелодрама",
    37: "Кино - Спорт",
    38: "Кино - Театр, Опера, Балет",
    39: "Кино - Индийское",
    40: "Другое - Дизайн / Графика",
    41: "Другое - Библиотека",
    42: "Музыка - Классическая",
    45: "Сериал - Русский",
    46: "Сериал - Буржуйский",
    47: "Кино - Азиатский",
    48: "Кино - Концерт",
    49: "Кино - Передачи / ТВ-шоу",
    50: "Кино - ТВ-шоу Мир",
}


def _kinozal_urls() -> list[str]:
    """Read Kinozal URLs from the KINOZAL_URLS env variable (format: 'label|url;...').

    Falls back to KINOZAL_TOP_URL (a single plain URL) for local testing. The
    legacy name `URLS` is NOT read (clean rename, #263): a stale `URLS` no longer
    silently masks a missing `KINOZAL_URLS`.
    """
    urls_env = os.environ.get("KINOZAL_URLS", "")
    if urls_env:
        return [pair.split("|")[1] for pair in urls_env.split(";") if "|" in pair]
    fallback = os.environ.get("KINOZAL_TOP_URL", "")
    return [fallback] if fallback else []


def _excluded_genres() -> set[str]:
    """Denylist of genres to suppress from notifications (#263).

    Read from KINOZAL_EXCLUDED_GENRES (`;`-separated), normalized to lower/trim.
    Empty/unset → empty set → the genre filter is off (no details fetch at all).
    """
    raw = os.environ.get("KINOZAL_EXCLUDED_GENRES", "")
    return {g.strip().lower() for g in raw.split(";") if g.strip()}


def _normalize_item_category_name(value: str) -> str:
    """Normalize readable item-category configuration for matching (#506)."""
    return " ".join(value.split()).casefold()


def _excluded_item_categories() -> set[str]:
    """Read the operator-owned item-category denylist from the environment."""
    raw = os.environ.get("KINOZAL_EXCLUDED_ITEM_CATEGORIES", "")
    return {
        normalized
        for value in raw.split(";")
        if (normalized := _normalize_item_category_name(value))
    }


def _item_category_name(category_id: int) -> str | None:
    """Map an authoritative details.php category id to the 2026-08-13 taxonomy.

    The table is committed instead of fetched at runtime: browse.php requires an
    authenticated request whose failure could otherwise disable filtering. A new
    id remains unknown and therefore fail-open until the taxonomy is refreshed.
    """
    return _KINOZAL_ITEM_CATEGORIES.get(category_id)


def _known_item_category_config_names() -> set[str]:
    """Readable taxonomy names plus group prefixes accepted in configuration."""
    known: set[str] = set()
    for name in _KINOZAL_ITEM_CATEGORIES.values():
        normalized = _normalize_item_category_name(name)
        known.add(normalized)
        if " - " in normalized:
            known.add(normalized.split(" - ", 1)[0])
    return known


def _validate_item_category_config(
    excluded_categories: set[str], results: list[PipelineResult]
) -> None:
    """Surface disabled or stale operator configuration once per pipeline run."""
    if not excluded_categories:
        logger.info("kinozal item category filter disabled: denylist is empty")
        return

    unknown_config = sorted(excluded_categories - _known_item_category_config_names())
    if not unknown_config:
        return
    message = (
        "item category configuration contains names absent from the committed "
        f"Kinozal taxonomy: {', '.join(unknown_config)}; delivery unchanged"
    )
    logger.error("kinozal pipeline: %s", message)
    for result in results:
        result.errors.append(message)


def _parse_item_category(details_html: str) -> int | None:
    """Return one details-page category id, or ``None`` for ambiguous evidence.

    Kinozal exposes the id on ``img.cat_img_r``. The onclick ``cat(N)`` value is
    primary; the image path ``/pic/cat/N.gif`` is the markup fallback (#506).
    """
    soup = BeautifulSoup(details_html, "html.parser")
    markers = soup.select("img.cat_img_r")
    if len(markers) != 1:
        return None
    marker = markers[0]
    onclick = marker.get("onclick")
    if isinstance(onclick, str):
        match = re.fullmatch(r"\s*cat\(([0-9]+)\);?\s*", onclick)
        if match:
            return int(match.group(1))
    src = marker.get("src")
    if isinstance(src, str):
        match = re.search(r"(?:^|/)pic/cat/([0-9]+)\.gif(?:[?#].*)?$", src)
        if match:
            return int(match.group(1))
    return None


def _item_category_excluded(name: str, excluded: set[str]) -> bool:
    """Match a readable category exactly or through a configured group prefix."""
    normalized = _normalize_item_category_name(name)
    return any(
        normalized == configured or normalized.startswith(configured + " - ")
        for configured in excluded
    )


def _parse_labeled_field(details_html: str, label: str) -> str:
    """Read a `<b>{label}:</b> … <br>` field's visible text off a kinozal details
    page (#263 for `Genre:`, generalized in #140 for cast/director/description).

    Real markup (verified against the live page): the value follows the `<b>`
    label as tag-wrapped links/spans (`<span class="lnks_tobrs">…</span>`) or a
    bare text node, sits after a whitespace node, and is terminated by the next
    `<br>` or `<b>`. We collect the *visible text* of the siblings up to that
    terminator — `str(sibling)` would serialize raw HTML for a tag-wrapped value,
    and `next_sibling` alone is just the whitespace text node. `label` is matched
    by prefix (`startswith`), so pass it without the trailing colon (`"Genre"`).
    Returns '' if the field is absent (caller decides what '' means)."""
    soup = BeautifulSoup(details_html, "html.parser")
    for b in soup.find_all("b"):
        if not b.get_text(strip=True).startswith(label):
            continue
        parts: list[str] = []
        for sib in b.next_siblings:
            if getattr(sib, "name", None) in ("br", "b"):
                break
            text = sib.get_text(" ", strip=True) if isinstance(sib, Tag) else str(sib).strip()
            if text:
                parts.append(text)
        return " ".join(parts).strip()
    return ""


def _parse_genre(details_html: str) -> str:
    """`Genre:` value off a details page — thin wrapper over the shared
    `_parse_labeled_field` (§II — one sibling-walk, not four copies). Caller
    treats '' as unknown → keep (#263)."""
    return _parse_labeled_field(details_html, "Жанр")


def _parse_details_metadata(details_html: str) -> dict[str, Any]:
    """Assemble trailer-selection metadata from a kinozal details page (#140):
    cast (`Cast:`) / director (`Director:`) / genre (`Genre:`) / description
    (`About the film:`), all via shared `_parse_labeled_field`. `cast` is split on
    commas like a multi-valued genre; a missing field yields ''/[] (not an error)."""
    cast_raw = _parse_labeled_field(details_html, "В ролях")
    return {
        "cast": [c.strip() for c in cast_raw.split(",") if c.strip()],
        "director": _parse_labeled_field(details_html, "Режиссер"),
        "genre": _parse_genre(details_html),
        "description": _parse_labeled_field(details_html, "О фильме"),
    }


def build_film_profile(item: NormalizedItem, fetcher: Any) -> FilmProfile:
    """Best-effort `FilmProfile` construction from details.php for trailer selection (#140).

    `ru_title` is the clean title; `original_title` is the second ` / ` segment (or
    clean where no separate original exists, so retrieval collapses union to one query);
    year follows `enrich_with_trailer`. Metadata (cast/director/genre/description) comes
    through `fetcher.fetch_details` and inherits origin→mirror failover.

    §IV degradation: fetch/parse failure → a title+year profile with empty metadata and
    WARNING; the pipeline does NOT fail. A successful fetch with no parsed cast, director,
    or description triggers a separate WARNING because that is selector drift, not an empty
    film. This richer builder supports harness/#140 eval; production `enrich_with_trailer`
    (#144) uses a light title+year profile without cast. RU priority (#315) comes from title
    language; per-item fetching for cast ties is deferred (#144 Out of scope)."""
    clean = item.title.split("(")[0].strip()
    raw_for_year = item.raw.get("kinozal_raw_title", item.dedupe_key)
    year_match = re.search(r"\b(20\d{2})\b", raw_for_year)
    year = int(year_match.group(1)) if year_match else None
    orig = original_title(raw_for_year) or clean
    try:
        meta = _parse_details_metadata(fetcher.fetch_details(item.url))
    except Exception as exc:  # noqa: BLE001 — best-effort: details-fetch/parse degrades to title+year + WARNING (§IV), never crashes the pipeline
        logger.warning(
            "film-profile details fetch failed for %r: %s — degrading to title+year",
            item.title,
            exc,
            exc_info=True,
        )
        return FilmProfile(ru_title=clean, original_title=orig, year=year)
    if not (meta["cast"] or meta["director"] or meta["description"]):
        logger.warning(
            "film-profile details for %r fetched OK but parsed 0 metadata fields "
            "(selector drift?) — profile on title+year only",
            item.title,
        )
    return FilmProfile(
        ru_title=clean,
        original_title=orig,
        year=year,
        cast=meta["cast"],
        director=meta["director"],
        genre=meta["genre"],
        description=meta["description"],
    )


def _genre_excluded(genre_raw: str, excluded: set[str]) -> bool:
    """True if any comma-separated genre in `genre_raw` is in `excluded`.

    Matching is case-insensitive and trimmed (both sides normalized). Empty
    `excluded` → False."""
    genres = {g.strip().lower() for g in genre_raw.split(",") if g.strip()}
    return bool(genres & excluded)


_ORIGIN_HOST = "kinozal.tv"
_MIRROR_HOST = "kinozal.guru"
_KINOZAL_HOSTS = frozenset({_ORIGIN_HOST, _MIRROR_HOST})
_FASTPIC_HOST = "fastpic.org"


def _is_fastpic(host: str) -> bool:
    """True for the fastpic anti-hotlink host and its numbered CDN subdomains
    (e.g. `i126.fastpic.org`) — the hosts that serve a viewer page for a bare
    image URL (#265)."""
    return host == _FASTPIC_HOST or host.endswith("." + _FASTPIC_HOST)


def _extract_direct_image_url(viewer_html: str, requested_url: str) -> str:
    """From a fastpic anti-hotlink viewer page, return the signed full-size
    `<img src>` — the one whose base path (URL sans query) equals `requested_url`
    (#265). The real image sits behind a signed query (`?md5=&expires=`) on the
    SAME path we requested; `og:image` on the page points at a *thumbnail* on a
    different path, so we match on the base path, never just "the first <img>".
    Returns '' when no `<img>` matches (unresolvable → caller degrades visibly)."""
    requested_base = urlunsplit(urlsplit(requested_url)._replace(query="", fragment=""))
    soup = BeautifulSoup(viewer_html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        # bs4 types `.get` as str | AttributeValueList | None; a real src="" attr
        # is a single string. Skip the missing/multi-valued cases outright.
        if not isinstance(src, str) or not src:
            continue
        base = urlunsplit(urlsplit(src)._replace(query="", fragment=""))
        if base == requested_base:
            return src
    return ""


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
        actually served it (#247) — anonymous primary, authenticated mirror on
        any primary failure. Primary success → the requested origin
        (kinozal.tv); mirror fallback → kinozal.guru. The pipeline resolves the
        listing's relative links/posters against this base, so a mirror-served
        page yields .guru links (live for the logged-in user) instead of dead
        .tv ones — reversing #227/#241's fixed canonical-origin choice.

        `fetch_details` reuses this origin→mirror decision (#263)."""
        try:
            return fetch_html(url), _origin(url)
        except Exception as primary_exc:  # noqa: BLE001 — any primary-fetch failure falls back to the mirror
            return self._from_mirror(url, primary_exc), _origin(_mirror_url(url))

    def fetch_details(self, url: str) -> str:
        """Fetch a details.php page for genre filtering (#263), returning just the
        HTML — the `Genre:` field is read from it, so no base_url resolution is needed.

        A healthy run serves the listing from the anonymous kinozal.tv primary, so
        `url` is a .tv link whose details page shows `Genre:` anonymously — reuse
        `fetch_listing`'s anonymous-primary / authenticated-mirror-on-error path.

        But when kinozal.tv is down the listing falls back to the authenticated
        kinozal.guru mirror (#247), so `url` is a *mirror* link. kinozal.guru gates
        all HTML behind login (302 → login.php, `docs/architecture/pipeline.md`
        § Kinozal mirror fallback), so an anonymous GET of a mirror details page
        returns HTTP 200 — a login page with no `Genre:` block. That is a *false
        success*: it raises no exception, so
        `fetch_listing`'s except-triggered mirror failover never fires, and the
        genre filter silently goes blind (every item parses to "" → fail-open →
        notified, #317). So any mirror-host URL is fetched AUTHENTICATED here.
        (`_ensure_login` bypasses `_from_mirror`'s `_mirror_enabled` guard, but a
        mirror-host URL can only exist once the listing was mirror-served — i.e.
        the mirror is already enabled and logged in — so the guarded case is
        unreachable in prod; a login failure still degrades visibly via §IV.)"""
        if urlsplit(url).netloc == _MIRROR_HOST:
            return fetch_authenticated(self._ensure_login(), url)
        return self.fetch_listing(url)[0]

    def fetch_poster(self, url: str) -> bytes:
        """Download a poster, sharing the listing's origin→mirror failover (#241).

        Try the URL as-is; on failure retry the kinozal.guru mirror ONLY when the
        URL is a kinozal host that is not already the mirror. A third-party host
        (e.g. an uploader's fastpic image) has no kinozal mirror, so its failure
        propagates and the notifier degrades to text + WARNING (§IV). A
        primary-on-.guru failure isn't re-swapped to the same host. The mirror
        poster fetch is anonymous — no _ensure_login, so one dead-origin poster
        on an otherwise-healthy run pays no login cost.

        For a fastpic anti-hotlink viewer page (`NotAnImageError`, #265) the
        signed full-size link is resolved from the exception body (the already-
        downloaded viewer HTML — no second GET) and fetched. **Invariant:** this
        returns the BYTES downloaded within this call, never the signed URL — a
        future refactor must not hoist resolve out and revive `expires` staleness
        (the window is milliseconds today: the notifier downloads the poster once,
        before its retry loop)."""
        try:
            return fetch_bytes(url)
        except NotAnImageError as viewer_exc:
            host = urlsplit(url).netloc
            if not _is_fastpic(host):
                raise  # only fastpic serves the viewer-page trap we can resolve
            direct = _extract_direct_image_url(viewer_exc.body.decode("utf-8", "replace"), url)
            if not direct:
                raise  # unresolvable → propagate so the notifier degrades visibly (§IV)
            logger.info("[kinozal] fastpic viewer resolved to signed image for %s", url)
            return fetch_bytes(direct)
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


def _dedupe_key(raw: str) -> str:
    """Dedupe key from a raw kinozal `@title` = `RU / Original / Year / Format` (#363).

    The key is the title-identity prefix `RU / Original / Year` — everything up to
    and *including* the release-year segment — with the trailing `Format` segment(s)
    dropped. This distinguishes namesakes/sequels that share the RU first segment but
    differ in original title or year (`Dune / Dune / 2021` vs
    `Dune / Dune: Part Two / 2024`), while still collapsing repacks of one film that
    differ ONLY in the format tail (`… / 2025 / Portable` vs `… / 2025 / FitGirl`).

    The year is located by scanning for the first ` / `-segment that is a bare year
    (`YEAR_SEGMENT_RE.fullmatch`) — positional slicing would break on the no-original
    form `Title / Year / Format`, and a substring match would false-hit a year baked
    into a format token (`… / BDRip 2160p`). When no year segment exists (e.g. `Dune`
    with no separators) the boundary is unknowable, so we fall back to the clean first
    segment — today's behaviour, keeping yearless repacks collapsed.
    """
    parts = [p.strip() for p in raw.split(" / ")]
    for i, part in enumerate(parts):
        if YEAR_SEGMENT_RE.fullmatch(part):
            return " / ".join(parts[: i + 1])
    return parts[0]


def _extract_kinozal_items(
    html: str,
    source: dict[str, Any],
    base_url: str | None = None,
    listing_url: str | None = None,
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
        item.raw["kinozal_listing_url"] = listing_url
        item.title = _kinozal_title(item.title)
    return result


def _normalize_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    """Deduplicate by title-identity key (`RU / Original / Year`) and normalize
    dedupe_key to match (#363).

    The key comes from `_dedupe_key(raw @title)`, dropping only the trailing format
    segment. Repacks of one film (Portable, FitGirl, …) differ ONLY in that tail, so
    they still share a key and collapse to one item — while namesakes/sequels that
    differ in original title or year no longer collapse (the #363 bug: `Dune / Dune /
    2021` and `Dune / Dune: Part Two / 2024` both keyed on the clean RU `Dune`, silently
    dropping the second). The key becomes the stored dedupe_key so future runs also skip
    all repacks of an already-notified film. `item.title` (display) stays the clean RU
    segment — display ≠ key.

    Reads the raw title straight from `item.raw["kinozal_raw_title"]` (always set by
    `_extract_kinozal_items`); a KeyError here is a real upstream drift and must surface,
    not be masked by a `.get` fallback (§IV/§VI).
    """
    seen: set[str] = set()
    result: list[NormalizedItem] = []
    for item in items:
        key = _dedupe_key(item.raw["kinozal_raw_title"])
        if key in seen:
            logger.debug("[kinozal] duplicate title collapsed: %r", key)
            continue
        seen.add(key)
        item.dedupe_key = key
        result.append(item)
    return result


# §IV visible markers (#138): a trailer miss/failure must reach the user as a
# legible line, never a silent empty `{trailer_link}`. Distinct text + log level
# let the user tell "no trailer exists" from "lookup broke", and keep the WARNING
# dev-tripwire firing only on real anomalies (a clean miss is expected).
_TRAILER_MISS_MARKER = "🎬 трейлер не найден"
_TRAILER_ERROR_MARKER = "⚠️ трейлер: ошибка поиска"
# The third cause (#384) is neither miss nor failure, but exhausted daily quota.
# It has a separate marker because the operator must reduce run volume or change
# the trailer source, not fix search.
_TRAILER_QUOTA_MARKER = "⚠️ трейлер: дневная квота YouTube"


def select_trailer(profile: FilmProfile, youtube: Any) -> str:
    """Retrieval, selection, and §IV markers: everything between profile and user.

    The trailer-epic retrieval → selection split builds a candidate pool through
    `youtube.search_candidates` (RU plus original-title query union, #140) and selects
    one language-aware `HeuristicStrategy` result (#141): RU trailer first, EN fallback,
    closing RU regression #138→#315 from original-title-only `get_trailer_url`.

    `HeuristicStrategy` is instantiated directly (pure internal logic, not an external
    boundary—§II) and matches eval `default_strategy()` (`scripts/eval_trailers.py`), so
    production and measurement use one strategy; update this on eval-strategy escalation.

    Empty pick (`video_id=None`) → `_TRAILER_MISS_MARKER` plus pool-size INFO; retrieval
    exception → `_TRAILER_ERROR_MARKER` plus WARNING traceback. A successful pick logs an
    INFO breadcrumb with `video_id`/`reason`/`confidence`. Every path still notifies the
    item; never a silent empty string.

    **Why the breadcrumb carries `video_id` (#359).** Without it, a report of a wrong
    link cannot be investigated: the log says `ambiguous` but not which video reached the
    user, and live YouTube results change daily. Pool size distinguishes no YouTube result
    from N results that all failed relevance; those are different bugs.

    **Do NOT filter by `confidence` here.** #359 tried converting low-confidence picks
    (`< 0.5`) to misses and reverted it: the 28-case golden set changed 26 hit → 16 hit,
    2 miss → 12 miss, wrong 0 → 0. All ten suppressed picks were hits: `confidence=0.3`
    means equally good trailers for one film, not potentially the wrong film.

    **Why this is a separate function (#379).** It is the half that reaches users and
    that #359 broke without changing `HeuristicStrategy.pick`. Eval
    (`scripts/eval_trailers.py::evaluate_delivery`) enters HERE, not `pick`; its baseline
    is pinned by `tests/fixtures/trailer_baseline.json`, so post-pick policy changes fail
    `tests/test_eval_baseline.py`. The seam is `FilmProfile`, the golden-set's native form;
    `NormalizedItem` would make fixtures duplicate Kinozal title grammar (§II).
    """
    try:
        candidates = youtube.search_candidates(profile)
    except YoutubeQuotaExhausted:
        # The sole exception that does NOT degrade to this film's marker: daily quota is
        # shared, so the next film will certainly hit it. The run loop, not this function,
        # decides to stop enrichment (§IV: the signal must reach its responder).
        raise
    except Exception as exc:  # noqa: BLE001 — retrieval failure degrades to a visible marker, item still notified
        logger.warning("trailer lookup failed for %r: %s", profile.ru_title, exc, exc_info=True)
        return _TRAILER_ERROR_MARKER
    pick = HeuristicStrategy().pick(profile, candidates)
    if pick.video_id is None:
        logger.info(
            "no trailer found for %r (pool=%d candidates)", profile.ru_title, len(candidates)
        )
        return _TRAILER_MISS_MARKER
    logger.info(
        "trailer pick for %r: %s (conf=%.1f, video_id=%s)",
        profile.ru_title,
        pick.reason,
        pick.confidence,
        pick.video_id,
    )
    return f"https://www.youtube.com/watch?v={pick.video_id}"


def enrich_with_trailer(item: NormalizedItem, youtube: Any) -> str:
    """Pick a YouTube trailer URL, or return a visible §IV marker (#144/#315).

    Production entry: constructs `FilmProfile` from the Kinozal title and delegates to
    `select_trailer`. It uses title+year (`ru_title` clean, `original_title` second ` / `
    segment or "", year from kinozal_raw_title); do not fetch cast/metadata because RU
    priority uses title language, while per-item fetching for within-language ties is a
    separate unit (#144 Out of scope), not #315.

    **Gate boundary (#379).** The baseline pins only the second half (`select_trailer`).
    Profile derivation below is not measured and relies on `TestEnrichWithTrailer` unit
    tests; #385 (game grammar) and #393 occurred here, so `trailer_baseline.json` will
    not see a change in this half.
    """
    clean = item.title.split("(")[0].strip()
    raw_for_year = item.raw.get("kinozal_raw_title", item.dedupe_key)
    year_match = re.search(r"\b(20\d{2})\b", raw_for_year)
    year = int(year_match.group(1)) if year_match else None
    # `original_title` itself suppresses a service second segment (`x64`, `RU`); the
    # guard belongs in title grammar, not here (#412). The former `kinozal_is_game`
    # branch (#385) suppressed originals for ALL game-URL listings, breaking localized
    # games that have an original in the standard position.
    orig = original_title(raw_for_year)
    return select_trailer(FilmProfile(ru_title=clean, original_title=orig, year=year), youtube)


def _matched_excluded_genre(genre_raw: str, excluded: set[str]) -> str | None:
    """Return the normalized excluded genre that matched, if any."""
    for genre in genre_raw.split(","):
        normalized = genre.strip().lower()
        if normalized and normalized in excluded:
            return normalized
    return None


def _log_item_filter_outcome(
    item: NormalizedItem,
    outcome: str,
) -> None:
    """Log one post-filter breadcrumb with item-level provenance (#506)."""
    logger.info(
        "kinozal new item %r from %s: %s (category=%r, id=%r)",
        item.title,
        item.raw.get("kinozal_listing_url"),
        outcome,
        item.raw.get("kinozal_item_category_name"),
        item.raw.get("kinozal_item_category"),
    )


@dataclass(frozen=True)
class _ItemFilterOutcome:
    denied_by: str | None = None
    category_resolved: bool = False
    genre_unparsed: bool = False


def _filter_item(
    item: NormalizedItem,
    fetcher: Kinozal,
    excluded_categories: set[str],
    excluded_genres: set[str],
) -> _ItemFilterOutcome:
    """Classify one new item from at most one details-page response."""
    item.raw["kinozal_item_category"] = None
    item.raw["kinozal_item_category_name"] = None
    if not (excluded_categories or excluded_genres):
        _log_item_filter_outcome(item, "delivered")
        return _ItemFilterOutcome()

    try:
        details_html = fetcher.fetch_details(item.url)
    except Exception as exc:  # noqa: BLE001 — unknown type/genre stays visible and fail-open (§IV)
        logger.warning(
            "[%s] item category/genre details lookup failed for %r (%s) — keeping item (fail-open)",
            item.source_id,
            item.title,
            exc,
        )
        _log_item_filter_outcome(item, "delivered")
        return _ItemFilterOutcome()

    category_id = _parse_item_category(details_html)
    item.raw["kinozal_item_category"] = category_id
    category_name = _item_category_name(category_id) if category_id is not None else None
    item.raw["kinozal_item_category_name"] = category_name
    category_resolved = False
    if excluded_categories:
        if category_id is None:
            logger.warning(
                "[%s] item category marker missing, ambiguous, or unparseable for %r — "
                "keeping item (fail-open)",
                item.source_id,
                item.title,
            )
        elif category_name is None:
            logger.warning(
                "[%s] item category id %d is absent from the committed taxonomy for %r — "
                "keeping item (fail-open)",
                item.source_id,
                category_id,
                item.title,
            )
        else:
            category_resolved = True
            if _item_category_excluded(category_name, excluded_categories):
                _log_item_filter_outcome(item, f"denied by category {category_name!r}")
                return _ItemFilterOutcome(denied_by="category", category_resolved=category_resolved)

    genre_unparsed = False
    if excluded_genres:
        genre = _parse_genre(details_html)
        if not genre:
            genre_unparsed = True
        elif matched_genre := _matched_excluded_genre(genre, excluded_genres):
            _log_item_filter_outcome(item, f"denied by genre {matched_genre!r}")
            return _ItemFilterOutcome(denied_by="genre", category_resolved=category_resolved)

    _log_item_filter_outcome(item, "delivered")
    return _ItemFilterOutcome(
        category_resolved=category_resolved,
        genre_unparsed=genre_unparsed,
    )


def _log_item_filter_summaries(
    category_filtered: list[NormalizedItem],
    genre_filtered: list[NormalizedItem],
    unparsed_genre: list[NormalizedItem],
) -> None:
    """Log aggregate breadcrumbs without conflating category and genre policy."""
    if unparsed_genre:
        logger.info(
            "kinozal pipeline: %d item(s) fetched with no parseable genre (kept) — %s",
            len(unparsed_genre),
            ", ".join(sorted(item.title for item in unparsed_genre)),
        )
    if category_filtered:
        logger.info(
            "kinozal pipeline: filtered %d item(s) by excluded item category: %s",
            len(category_filtered),
            ", ".join(sorted(item.title for item in category_filtered)),
        )
    if genre_filtered:
        logger.info(
            "kinozal pipeline: filtered %d item(s) by excluded genre: %s",
            len(genre_filtered),
            ", ".join(sorted(item.title for item in genre_filtered)),
        )


def _append_category_drift_errors(
    category_total_by_source: dict[str, int],
    category_resolved_by_source: dict[str, int],
    results: list[PipelineResult],
) -> None:
    """Turn all-items-unresolved selector/auth drift into source errors."""
    result_by_source = {result.source_id: result for result in results}
    for source_id, total in category_total_by_source.items():
        if category_resolved_by_source.get(source_id, 0) > 0:
            continue
        message = f"item category resolved for zero of {total} new item(s); processing fail-open"
        logger.error("[%s] %s", source_id, message)
        result = result_by_source.get(source_id)
        if result is not None:
            result.errors.append(message)


def _apply_item_filters(
    items: list[NormalizedItem],
    fetcher: Kinozal,
    excluded_categories: set[str],
    excluded_genres: set[str],
    results: list[PipelineResult],
) -> tuple[list[NormalizedItem], list[NormalizedItem]]:
    """Apply item category then genre with one details fetch per new item.

    Category policy uses the authoritative per-release marker. Both filters are
    fail-open per item, while a category filter that resolves zero items becomes
    a visible source error. Filtered items are returned for terminal dedup storage
    and never reach trailer lookup or notification (#263, #506).
    """
    kept: list[NormalizedItem] = []
    filtered: list[NormalizedItem] = []
    category_filtered: list[NormalizedItem] = []
    genre_filtered: list[NormalizedItem] = []
    unparsed_genre: list[NormalizedItem] = []
    category_total_by_source: dict[str, int] = {}
    category_resolved_by_source: dict[str, int] = {}

    for item in items:
        if excluded_categories:
            category_total_by_source[item.source_id] = (
                category_total_by_source.get(item.source_id, 0) + 1
            )
        outcome = _filter_item(item, fetcher, excluded_categories, excluded_genres)
        if outcome.category_resolved:
            category_resolved_by_source[item.source_id] = (
                category_resolved_by_source.get(item.source_id, 0) + 1
            )
        if outcome.genre_unparsed:
            unparsed_genre.append(item)
        if outcome.denied_by is None:
            kept.append(item)
            continue
        filtered.append(item)
        if outcome.denied_by == "category":
            category_filtered.append(item)
        else:
            genre_filtered.append(item)

    _log_item_filter_summaries(category_filtered, genre_filtered, unparsed_genre)
    _append_category_drift_errors(
        category_total_by_source,
        category_resolved_by_source,
        results,
    )

    return kept, filtered


def _fetch_and_extract(
    kinozal_sources: list[dict[str, Any]],
    urls: list[str],
    fetcher: Kinozal,
) -> tuple[list[NormalizedItem], list[PipelineResult]]:
    """Fetch HTML for every (source × url) pair and extract items.

    Returns the accumulated items plus one `PipelineResult` per source (fetch and
    extraction errors recorded per-URL). Items keep their source_id from
    `extract_from_html` so the per-source dedup below picks them up correctly.

    The double `for source: for url:` loop is kept atomic on purpose: both
    `continue` branches (fetch-fail, extraction-fail) stay `continue`, so when one
    URL of a source fails its sibling URL's items still accumulate AND the error
    still surfaces in `result.errors`. Splitting the url-loop into a returning
    sub-helper would flip `continue`→`return` and silently regress that partial-
    fail-plus-success branch, which has no direct characterization test (#286).
    """
    all_items: list[NormalizedItem] = []
    results: list[PipelineResult] = []
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
            extracted = _extract_kinozal_items(
                html_text,
                source,
                base_url=effective_base_url,
                listing_url=url,
            )
            if not extracted.ok:
                result.errors.extend(extracted.errors)
                continue
            all_items.extend(extracted.items)
        results.append(result)
    return all_items, results


def _dedup_and_log_coverage(
    all_items: list[NormalizedItem],
    results: list[PipelineResult],
    storage: Storage,
) -> list[NormalizedItem]:
    """Collapse repacks, re-attach items per source, log coverage, return new items.

    Mutates `results` in-place: each result gets its `.items` set so callers can
    inspect coverage. Returns the not-yet-seen items (dedup against the sheet).
    """
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
    # path — so a vanished film reads in the Actions log instead of looking like
    # "no new films". raw_count is pre-normalize, exposing dedup-collapse.
    logger.info(
        "kinozal pipeline: %d extracted (%d after dedup-collapse), %d new, %d already-seen",
        raw_count,
        len(all_items),
        len(new_items),
        len(all_items) - len(new_items),
    )
    return new_items


def _notify_and_persist(
    kept: list[NormalizedItem],
    filtered: list[NormalizedItem],
    source_map: dict[str, dict[str, Any]],
    youtube: Any,
    notifier: Notifier,
    storage: Storage,
    results: list[PipelineResult],
) -> None:
    """Enrich, notify, persist delivered+filtered, and surface failed deliveries.

    Mutates `results` in-place: failed deliveries are appended to the matching
    per-source result's `.errors`. Persist confirmed-delivered items PLUS items
    filtered by category or genre (Principle III); failed deliveries stay unstored so the
    next run retries them, and surface as a visible anomaly via result.errors +
    non-zero exit (Principle IV). The store-guard keys on `items_to_store` (not
    `sent`) so filtered items are persisted even when every new item was filtered
    and nothing was sent.

    **Enrichment stops at the first quota refusal (#384).** The daily YouTube
    quota is 100 `search.list` calls (measured 2026-07-26 via Service Usage API,
    default tier, cannot be raised — billing is off), while a spike of new items
    asks for several times that: run 30143534431 spent 163 requests on guaranteed
    429s. Once `YoutubeQuotaExhausted` surfaces, every remaining film skips
    retrieval and carries `_TRAILER_QUOTA_MARKER` — the quota is daily and
    project-wide, so the next film would refuse just as certainly.

    A *precomputed* cap was rejected: any fixed number is a guess, it breaks on a
    second run in the same day (the quota is daily, a per-run budget is not), and
    it underuses the quota on single-branch films (`ru_title == original_title`
    costs 1 request, not 2). The real boundary is only known to the API, so we let
    it name it. Cost of discovery is one film's requests, not 163.

    Only quota refusals stop the loop. A generic `TrailerRetrievalError` (500,
    timeout) still degrades to a per-film marker (#383) — otherwise one flaky
    response would silence trailers for the whole run.

    The stop lives here because "a run" only exists at this level:
    `search_candidates` is stateless and `Youtube` builds a network client in its
    constructor.
    """
    notifications: list[Notification] = []
    quota_exhausted = False
    for i, item in enumerate(kept):
        if quota_exhausted:
            item.trailer_url = _TRAILER_QUOTA_MARKER
        else:
            try:
                item.trailer_url = enrich_with_trailer(item, youtube)
            except YoutubeQuotaExhausted as exc:
                quota_exhausted = True
                item.trailer_url = _TRAILER_QUOTA_MARKER
                # One line per run, not per film: the 163-line noise in run 30143534431
                # is part of the defect and must not recur (§IV: an anomaly must be
                # readable rather than drowned in repeats). `exc_info` names the API limit.
                logger.warning(
                    "youtube quota exhausted after %d enriched films: %s; "
                    "%d remaining films skip retrieval with a visible marker",
                    i,
                    exc,
                    len(kept) - i,
                    exc_info=True,
                )
        template = source_map[item.source_id]["message_template"]
        notifications.append(build_notification(item, template))

    sent, failed = notifier.send_items(notifications)

    sent_ids = {n.id for n in sent}
    items_to_store = [i for i in kept if i.dedupe_key in sent_ids] + filtered
    if items_to_store:
        storage.append_rows("movies", ROW_HEADERS, [i.to_row() for i in items_to_store])

    if failed:
        result_by_source = {r.source_id: r for r in results}
        item_by_key = {i.dedupe_key: i for i in kept}
        for notif in failed:
            # notif.id is always a new_item dedupe_key whose source has a result,
            # so both lookups must succeed — a KeyError here is a real bug.
            source_id = item_by_key[notif.id].source_id
            message = f"notification delivery failed for {notif.id!r}, will retry next run"
            logger.error("[%s] %s", source_id, message)
            result_by_source[source_id].errors.append(message)


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
    config = sources_config or load_sources_config()
    kinozal_sources = [
        s for s in config["sources"] if s.get("enabled") and s["id"].startswith("kinozal_")
    ]
    if not kinozal_sources:
        logger.info("no enabled kinozal sources found")
        return []

    source_map = {s["id"]: s for s in kinozal_sources}

    # URLs come from the KINOZAL_URLS env variable (label|url;... format).
    # sources.json url field is only a schema placeholder / local fallback.
    urls = _kinozal_urls()
    if not urls:
        logger.error("kinozal pipeline: no URLs configured (set KINOZAL_URLS or KINOZAL_TOP_URL)")
        results = []
        for source in kinozal_sources:
            result = PipelineResult(source_id=source["id"])
            result.errors.append("no URLs configured (set KINOZAL_URLS or KINOZAL_TOP_URL)")
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

    all_items, results = _fetch_and_extract(kinozal_sources, urls, fetcher)
    excluded_categories = _excluded_item_categories()
    excluded_genres = _excluded_genres()
    _validate_item_category_config(excluded_categories, results)
    if not all_items:
        logger.info("kinozal pipeline: no items extracted")
        return results

    new_items = _dedup_and_log_coverage(all_items, results, storage)
    if not new_items:
        logger.info("kinozal pipeline: no new items")
        return results

    kept, filtered = _apply_item_filters(
        new_items,
        fetcher,
        excluded_categories,
        excluded_genres,
        results,
    )
    _notify_and_persist(kept, filtered, source_map, youtube, notifier, storage, results)
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

    from kinozal_scraper.alerting import report_failures

    if report_failures(notifier, prod_results):
        sys.exit(1)
