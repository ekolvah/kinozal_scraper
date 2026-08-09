"""TMDB videos as a trailer source (#329, trailer epic).

The epic hypothesis, “an official trailer with Russian priority,” is solved:
the canonical method is metadata from an API with language-tagged videos, not
YouTube scraping plus heuristics (#141), LLM (#142), or embeddings (#143). TMDB
`/movie/{id}/videos` returns each video's `key` (YouTube id), `iso_639_1`,
`type`, `official`, and `site`, reducing selection to a deterministic filter.

The retrieval → selection boundary mirrors `youtube.py` (§II):
`TmdbClient.resolve` (external boundary, DI) fetches videos and pure
`pick_trailer` ranks them. Production is not connected until separate
integration (analogous to #144); this is an offline eval-harness component.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from kinozal_scraper.trailer_strategy import FilmProfile, TrailerPick

_YOUTUBE = "YouTube"
_TMDB_API = "https://api.themoviedb.org/3"


@dataclass
class TmdbVideo:
    """One TMDB `/movie/{id}/videos` entry. `key` is the YouTube id stored in
    `TrailerPick.video_id`; `iso_639_1`/`type`/`official`/`site` are selection
    signals, while `name` carries an accessibility nuance (an ASL variant) for
    within-tier deprioritization."""

    key: str
    iso_639_1: str
    type: str
    official: bool
    site: str
    name: str = ""


# ── selection: pure deterministic rule (§II, no network/LLM) ──────────────────


def _tier(video: TmdbVideo) -> int:
    """Priority tier (lower is better); `_INELIGIBLE` is neither trailer nor teaser.
    RU Trailer → RU Teaser → official EN Trailer → any EN Trailer. The disputed
    `RU Teaser (1) < official en Trailer (2)` boundary is pinned by a test (§I)."""
    is_ru = video.iso_639_1 == "ru"
    if is_ru and video.type == "Trailer":
        return 0
    if is_ru and video.type == "Teaser":
        return 1
    if video.type == "Trailer" and video.official:
        return 2
    if video.type == "Trailer":
        return 3
    return _INELIGIBLE


_INELIGIBLE = 99


def _is_sign_language(video: TmdbVideo) -> bool:
    """ASL/sign-language variant (the Beetlejuice case): one `name` substring
    check, NOT a growing accessibility taxonomy (§VII)."""
    return "sign language" in video.name.lower()


def pick_trailer(videos: list[TmdbVideo]) -> TrailerPick | None:
    """Pick a trailer from TMDB videos. Only `site=YouTube` qualifies; `_tier`
    sets priority and an ASL variant is deprioritized within a tier. No pick →
    `None` (§IV miss semantics: a visible production marker, not a silent default)."""
    eligible = [v for v in videos if v.site == _YOUTUBE and _tier(v) != _INELIGIBLE]
    if not eligible:
        return None
    best = min(eligible, key=lambda v: (_tier(v), _is_sign_language(v)))
    tier = _tier(best)
    confidence, reason = _TIER_META[tier]
    return TrailerPick(best.key, confidence, reason)


# Per-tier confidence/attribution makes language priority visible in the scorecard (§IV).
_TIER_META: dict[int, tuple[float, str]] = {
    0: (0.95, "tmdb ru trailer"),
    1: (0.7, "tmdb ru teaser"),
    2: (0.6, "tmdb official en trailer"),
    3: (0.4, "tmdb en trailer"),
}


# ── retrieval: external boundary (DI, mirrors Youtube), NOT unit-tested (§II) ─


class TmdbClient:
    """TMDB external boundary (the `Youtube` DI pattern): `resolve(profile)` → videos.
    Token comes from `os.environ["TMDB_TOKEN"]` (v4 Bearer). Network I/O is a
    thin layer over pure `pick_trailer`, so it is not unit-tested (§II)."""

    def __init__(self, session: requests.Session | None = None) -> None:
        token = os.environ["TMDB_TOKEN"]
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.get(f"{_TMDB_API}{path}", params=params, timeout=15)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def _find_movie_id(self, profile: FilmProfile) -> int | None:
        query = profile.original_title or profile.ru_title
        params: dict[str, Any] = {"query": query}
        if profile.year:
            params["primary_release_year"] = profile.year
        results = self._get("/search/movie", params).get("results", [])
        return int(results[0]["id"]) if results else None

    def resolve(self, profile: FilmProfile) -> list[TmdbVideo]:
        """Film videos = ru-RU ∪ en-US (deduplicated by `key`); the RU track must
        enter the pool when it exists (mirrors union retrieval in `search_candidates`).
        Film not found → empty list (§IV: pick_trailer → None → visible Miss)."""
        movie_id = self._find_movie_id(profile)
        if movie_id is None:
            return []
        seen: set[str] = set()
        out: list[TmdbVideo] = []
        for lang in ("ru-RU", "en-US"):
            data = self._get(f"/movie/{movie_id}/videos", {"language": lang})
            for item in data.get("results", []):
                key = item.get("key")
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(
                    TmdbVideo(
                        key=key,
                        iso_639_1=item.get("iso_639_1", ""),
                        type=item.get("type", ""),
                        official=bool(item.get("official", False)),
                        site=item.get("site", ""),
                        name=item.get("name", ""),
                    )
                )
        return out
