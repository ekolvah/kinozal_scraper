"""Trailer-selection retrieval → selection boundary (#139, trailer epic).

Retrieval (query → candidates) lives in `youtube.py` (#140); selection (one pick)
lives here behind `TrailerStrategy`. #139 established types, Protocol, and baseline
`FirstResultStrategy` (the former production logic, now comparison-only). Production
`kinozal_pipeline.enrich_with_trailer` (#144) chooses language-aware `HeuristicStrategy`
(#141), as does eval `default_strategy()`; RU priority closes regression #315.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from kinozal_scraper.text_utils import has_cyrillic, normalize_title, title_year_matches


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Word-boundary containment: `phrase` is a whole word/phrase in `haystack`,
    not part of a word. Both already passed `normalize_title`; without boundaries,
    a short title could confidently select an unrelated candidate (review #324)."""
    return bool(re.search(rf"\b{re.escape(phrase)}\b", haystack))


def _is_sequel_number(token: str) -> bool:
    """A standalone sequel number, not a four-digit year. Years discriminate in
    `title_year_matches` and must not be skipped."""
    return token.isdigit() and not re.fullmatch(r"(?:19|20)\d{2}", token)


def _title_tokens_in(film_tokens: list[str], cand_tokens: list[str]) -> bool:
    """`film_tokens` occur consecutively and in order in `cand_tokens`, allowing
    ONE interspersed numeric sequel token between title tokens. Channels may insert
    a sequel number absent from `ru_title`, breaking the phrase. Exact token equality
    retains word boundaries (#324); order is required and one skip is not fuzzy/edit
    distance matching (§VII)."""
    if not film_tokens:
        return False

    def aligns(start: int) -> bool:
        fi, ci, skipped = 0, start, False
        while fi < len(film_tokens) and ci < len(cand_tokens):
            if cand_tokens[ci] == film_tokens[fi]:
                fi += 1
                ci += 1
            elif fi > 0 and not skipped and _is_sequel_number(cand_tokens[ci]):
                skipped = True
                ci += 1
            else:
                break
        return fi == len(film_tokens)

    return any(aligns(s) for s in range(len(cand_tokens)))


@dataclass
class FilmProfile:
    """What selection knows about a film. `ru_title`/`original_title` carry the
    language signal used by strategy #141 for RU priority.

    `cast`/`director`/`genre`/`description` are details.php metadata (#140) used by
    #141's prefilter (cast in a candidate description). Defaults preserve #139
    construction and metadata-free golden-set records; best-effort fetching degrades
    to empty values."""

    ru_title: str
    original_title: str
    year: int | None
    cast: list[str] = field(default_factory=list)
    director: str = ""
    genre: str = ""
    description: str = ""


@dataclass
class Candidate:
    """One YouTube search result. #141 scores language/match from `title` and
    `description` (both in `search.list.snippet`); `published_at` was captured for
    a future recency tie-break without rewriting fixtures. `videos.list` signals
    such as `defaultAudioLanguage` are deliberately outside the snapshot; see #139."""

    video_id: str
    title: str
    channel: str = ""
    description: str = ""
    published_at: str = ""


@dataclass
class TrailerPick:
    """Strategy decision. `video_id=None` intentionally means no pick (a §IV
    production marker, not a silent skip). `reason` provides attribution visibility
    while inspecting the eval scorecard."""

    video_id: str | None
    confidence: float
    reason: str


class TrailerStrategy(Protocol):
    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick: ...


class FirstResultStrategy:
    """Baseline = former production selection (`get_trailer_url`, removed in #144):
    first candidate whose title passes the year filter. It is now harness/comparison
    only (production selects `HeuristicStrategy` #141). The rule shares
    `title_year_matches` (§II); a falsy year (None/0) disables filtering (`not year`)."""

    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick:
        year = film_profile.year
        for candidate in candidates:
            if not year or title_year_matches(candidate.title, year):
                return TrailerPick(candidate.video_id, 1.0, "first year-matching candidate")
        return TrailerPick(None, 0.0, "no year-matching candidate")


class HeuristicStrategy:
    """#141: deterministic language-aware prefilter (no LLM), baseline for a future
    AI picker (#142/#144).

    Selection has two steps:

    1. **relevance** — candidate title contains `ru_title` OR `original_title`
       tokens in order (allowing one interspersed numeric sequel token) and its year
       matches (shared `title_year_matches`, only for truthy year).
    2. **ranking** by `(is_ru, cast_hits, trailer_signal)`, descending and stable:
       language is **primary** (#315: RU > EN for a film match), cast in the candidate
       `description` breaks ties within one language, and `trailer_signal` (trailer/
       dub minus teaser) ranks a real trailer over a news clip/teaser. `cast_hits` is
       the number of the first ≤2 cast names found in the description.

    A unique top rank → confident pick (0.9); ≥2 equal top ranks → first in order,
    0.3, and an `ambiguous` reason marker (the #144 LLM-fallback signal; §IV visible
    ambiguity rather than a silent confident pick); no relevant candidate →
    `TrailerPick(None, 0.0)`."""

    _MAX_CAST = 2

    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick:
        relevant = [c for c in candidates if self._relevant(film_profile, c)]
        if not relevant:
            return TrailerPick(None, 0.0, "no title+year match")
        cast = [n for n in (normalize_title(x) for x in film_profile.cast[: self._MAX_CAST]) if n]
        ranked = sorted(relevant, key=lambda c: self._rank(c, cast), reverse=True)
        best = ranked[0]
        best_key = self._rank(best, cast)
        tied = sum(1 for c in relevant if self._rank(c, cast) == best_key)
        if tied > 1:
            return TrailerPick(best.video_id, 0.3, f"ambiguous: {tied} candidates share top rank")
        is_ru, cast_hits, _trailer = best_key
        reason = "cast tie-break" if cast_hits else ("ru language" if is_ru else "sole match")
        return TrailerPick(best.video_id, 0.9, reason)

    _TRAILER_WORDS = ("трейлер", "trailer", "дубляж")
    _TEASER_WORDS = ("тизер", "teaser")

    def _relevant(self, profile: FilmProfile, candidate: Candidate) -> bool:
        """Title-and-year match. In addition to both profile titles, try `original_title`
        **without** its parenthesized suffix (`Marvel's Spider-Man 2 (Digital Deluxe Edition)` →
        `Marvel's Spider-Man 2`) — otherwise an edition that never occurs in a trailer title
        excludes every real game trailer.

        The counts below come from one measurement of 3,764 raw titles (Sheets, 2026-07-29), but
        count DIFFERENT things, so they are named explicitly: 238 titles have parentheses in the
        second segment at all; 78 of them are games with an edition suffix (the class fixed here),
        while 160 are non-games where the parentheses carry an alternative title
        (`Ojingeo geim (Squid Game)`). A separate metric counts 96 game releases whose second
        segment is a title at all, not a service token (the class guarded by
        `text_utils.original_title`); exactly 78 of those have parentheses.

        **Only `original_title`, not `ru_title`.** In the second segment, parentheses carry an
        edition (games) or alternative title — in both cases the base remains a valid title (a
        part/season marker occurred there once out of 238, and that number is already in the title:
        `Heroes of Might and Magic IV (4) (Complete)`). In Russian, however, parentheses can
        qualify a part/season, and trimming would collapse `Dune (Part Two)` to the franchise
        `Dune`, which `_title_tokens_in` with its numeric skip would admit into “Dune 2” (year does
        not save this: trailer titles usually lack it). In production `ru_title` is already trimmed
        at `(` (`enrich_with_trailer`), so trimming here would add nothing but this risk.

        **The base variant is equal, not a fallback “when relevance is empty”.** That variant was
        in the plan and rejected by measurement: for `Marvel's Spider-Man 2`, the complete title
        with edition occurs consecutively in “All Marvel's Spider-Man 2 Digital Deluxe Edition
        Suits” (a suit compilation), so the first pass yielded non-empty relevance from ONE junk
        video and never reached trailers. Distinguishing a trailer from a non-trailer is the job of
        `_trailer_signal` in `_rank`, not relevance. Trimming uses the raw string because
        `normalize_title` consumes parentheses.
        """
        cand_tokens = normalize_title(candidate.title).split()
        titles = [profile.ru_title, profile.original_title, profile.original_title.split("(")[0]]
        film_titles = [normalize_title(t) for t in titles]
        if not any(t and _title_tokens_in(t.split(), cand_tokens) for t in film_titles):
            return False
        return not profile.year or title_year_matches(candidate.title, profile.year)

    def _rank(self, candidate: Candidate, cast: list[str]) -> tuple[int, int, int]:
        description = normalize_title(candidate.description)
        cast_hits = sum(1 for name in cast if _contains_phrase(description, name))
        return (int(has_cyrillic(candidate.title)), cast_hits, self._trailer_signal(candidate))

    def _trailer_signal(self, candidate: Candidate) -> int:
        """Within-language “real trailer” signal: minimal keyword set (trailer/dub
        +1) minus teaser (−1). It resolves an RU tie in favor of a trailer over a news
        clip/teaser instead of pool order (#141 defect). It mirrors `cast_hits` as a
        third dimension; NOT a channel-authority scorer or recency (§VII)."""
        title = normalize_title(candidate.title)
        return sum(w in title for w in self._TRAILER_WORDS) - sum(
            w in title for w in self._TEASER_WORDS
        )
