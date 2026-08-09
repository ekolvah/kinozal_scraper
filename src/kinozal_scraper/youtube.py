"""YouTube retrieval of a trailer-candidate pool (#140): `Youtube.search_candidates`.

Selection (one pick) belongs to `trailer_strategy.HeuristicStrategy` (#141), called
by `kinozal_pipeline.enrich_with_trailer` (#144). The former single
`get_trailer_url` (English-biased after #138 → RU regression #315) is removed."""

from __future__ import annotations

import html
import logging
import os
from typing import Any

from googleapiclient.discovery import build

from kinozal_scraper.trailer_strategy import Candidate, FilmProfile

logger = logging.getLogger(__name__)


class TrailerRetrievalError(RuntimeError):
    """No union branch succeeded, so retrieval did not occur (#383).

    Lives here rather than in a separate errors module: one raiser (`search_candidates`)
    and one catcher (`kinozal_pipeline.enrich_with_trailer`); §VII prohibits extracting
    an abstraction with one caller.
    """


class YoutubeQuotaExhausted(TrailerRetrievalError):
    """YouTube quota is exhausted, so API calls are pointless for the rest of the run (#384).

    A subclass rather than a sibling: all existing `TrailerRetrievalError` (#383)
    catchers keep working, while an informed caller (`kinozal_pipeline`) distinguishes
    exhausted quota from a transient server failure and stops enrichment.
    """


# Google usageLimits-family reasons. The axis is quota/non-quota, not transient/terminal:
# at 100 `search.list` calls daily (2026-07-26 measurement), `rateLimitExceeded` and
# `quotaExceeded` are the same event for us because minute and day ceilings coincide.
_QUOTA_REASONS = frozenset(
    {"quotaexceeded", "dailylimitexceeded", "ratelimitexceeded", "userratelimitexceeded"}
)


def _is_quota_error(exc: BaseException) -> bool:
    """Whether `HttpError` represents exhausted quota; reads `error_details`, not `.reason`.

    In `googleapiclient/errors.py`, `self.reason = data["error"]["message"]` is human
    text that Google may rewrite. The machine code is in `error_details`, either legacy
    `errors[0]["reason"] == "rateLimitExceeded"` or ErrorInfo
    `details[0]["reason"] == "RATE_LIMIT_EXCEEDED"`, hence normalization. Google
    historically returns those usageLimits reasons as 403 as well as 429. A string
    `error_details` (only a `message` in the body) carries no machine reason, so is non-quota.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status not in (403, 429):
        return False
    details = getattr(exc, "error_details", None)
    if not isinstance(details, list):
        return False
    for detail in details:
        reason = detail.get("reason") if isinstance(detail, dict) else None
        if isinstance(reason, str) and reason.replace("_", "").lower() in _QUOTA_REASONS:
            return True
    return False


def _search_one(client: Any, query: str) -> list[Candidate]:
    """One YouTube query → candidates (only `youtube#video`); snippet fields map to
    `Candidate`. No year/title filter: this is pure retrieval; selection
    (`FirstResultStrategy`) filters year.

    Snippet text is HTML-escaped (`Marvel&#39;s`, `Deadpool &amp; Wolverine`) and is
    decoded here at the protocol boundary, not in `normalize_title` (§II: fix data shape
    at input rather than every consumer). Otherwise entities reach title tokenization:
    `&#39;` produces token `39`, `&amp;` produces `amp`, and the phrase breaks (#412)."""
    response = (
        client.search()
        .list(q=query, part="id,snippet", maxResults=5, type="video", videoDuration="short")
        .execute()
    )
    out: list[Candidate] = []
    for item in response.get("items", []):
        if item.get("id", {}).get("kind") != "youtube#video":
            continue
        snippet = item.get("snippet", {})
        out.append(
            Candidate(
                video_id=item["id"]["videoId"],
                title=html.unescape(snippet.get("title", "")),
                channel=html.unescape(snippet.get("channelTitle", "")),
                description=html.unescape(snippet.get("description", "")),
                published_at=snippet.get("publishedAt", ""),
            )
        )
    return out


def search_candidates(client: Any, profile: FilmProfile) -> list[Candidate]:
    """Trailer candidate pool = **union** of RU and original-title queries, deduplicated
    by `video_id` (#140). An RU trailer must enter the pool when available (#315:
    retrieval breadth, not selection bias); selection (#141), not retrieval, picks language.

    One query when `ru_title == original_title` saves YouTube quota. One failed union
    branch is §IV best-effort: log WARNING and return the surviving branch. All failed
    branches are retrieval failure, not an empty pool: `TrailerRetrievalError` (#383).
    An empty list means queries succeeded and YouTube returned nothing; it must not absorb
    429 or infrastructure failure becomes an honest selection miss (74 lines in run
    2026-07-25). `client` is injected googleapiclient YouTube resource so the `--record`
    harness reuses this retrieval (§II)."""
    year = profile.year
    titles = [profile.ru_title]
    if profile.original_title and profile.original_title != profile.ru_title:
        titles.append(profile.original_title)
    seen: set[str] = set()
    pool: list[Candidate] = []
    failed = 0
    last_exc: Exception | None = None
    quota_seen = False
    queries = [f"{t} {year} trailer" if year else f"{t} trailer" for t in titles]
    # §IV visibility for title grammar (#412 review): queries are the sole location that
    # shows what the pipeline treated as a title. They exposed #385 (`x64 2024 trailer`
    # 27 times), but had previously entered logs accidentally from quota failures. Without
    # this log, a new service literal in the second segment (`RUS`, `Multi`, `Update 5`)
    # would become an “original title” query indistinguishable from a real trailer miss.
    logger.info("trailer retrieval queries for %r: %s", profile.ru_title, queries)
    for query in queries:
        try:
            candidates = _search_one(client, query)
        except Exception as exc:  # noqa: BLE001 — best-effort breadth: one union branch must not sink the pool (§IV); the counter below catches universal failure
            logger.warning("trailer retrieval branch failed for %r: %s", query, exc)
            failed += 1
            last_exc = exc
            quota_seen = quota_seen or _is_quota_error(exc)
            continue
        for candidate in candidates:
            if candidate.video_id in seen:
                continue
            seen.add(candidate.video_id)
            pool.append(candidate)
    # `titles` is non-empty by construction (it always contains ru_title), so
    # `failed == len(titles)` cannot run after zero attempts; an `attempted > 0`
    # safeguard would be dead code (precedent #256).
    if failed == len(titles):
        # Quota failure stops enrichment for the run (#384); another failure affects only
        # this film (#383). Any quota branch decides, not the last one: in a mixed pair
        # (RU quota failure, original timeout), choosing `last_exc` would lose the quota
        # signal and make the next film retry despite exhausted quota.
        error = YoutubeQuotaExhausted if quota_seen else TrailerRetrievalError
        raise error(
            f"all {failed} retrieval branch(es) failed for {profile.ru_title!r}"
        ) from last_exc
    return pool


class Youtube:
    def __init__(self) -> None:
        self.youtube = build("youtube", "v3", developerKey=os.environ["API_KEY"])

    def search_candidates(self, profile: FilmProfile) -> list[Candidate]:
        """Candidate pool for `profile` through shared `search_candidates` (#140)."""
        return search_candidates(self.youtube, profile)
