"""Title-and-year matching (`title_year_matches`)."""

from __future__ import annotations

import re

# A 4-digit release year (1900–2099) as it appears as a WHOLE ` / `-segment of a
# raw kinozal title. Single canonical shape reused by `original_title` and the
# pipeline's `_dedupe_key` (#363) so the raw-title parse isn't spelled with three
# drifting regexes. The `19|20` alternation (not the narrower `20\d{2}`) admits
# pre-2000 films.
YEAR_SEGMENT_RE = re.compile(r"(?:19|20)\d{2}")

# Service forms of the second ` / ` segment: build architecture and a language
# code. Both occupy the original-title position in some listings, so the
# discriminator for whether a listing has an original title is the segment's
# form, not the listing category (#412; #385 distinguished them by `t=7` and
# lost originals for localized games).
# The set is closed by a measurement of all 3,764 raw titles from Sheets: `x64`
# occurs 888 times, `RU` 139, and `EN` once; no other service form appears in
# the second position. `x86`/`x32` did not occur in the export but are named by
# #385's grammar so the first such listing does not produce a junk query. The
# same measurement prohibits a “short means service” heuristic: `Silo`, `From`,
# `Halo`, and `Apex` are real titles.
# The alternation is intentionally grouped: the caller uses `fullmatch`, but
# without parentheses a later `.search()` reuse would match `RUS`, `ENGLISH`,
# and `Renaissance` (review #412).
_SERVICE_SEGMENT_RE = re.compile(r"(?:x(?:64|86|32)|RU|EN)", re.IGNORECASE)


def title_year_matches(title: str, film_year: int) -> bool:
    """Return False if the video title explicitly mentions a year other than film_year."""
    title_years = {int(m) for m in re.findall(r"\b((?:19|20)\d{2})\b", title)}
    return not title_years or film_year in title_years


def normalize_title(s: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to single spaces for substring
    title-matching (#141). Keeps Latin, Cyrillic and digits; `Dune: Part Two!` →
    `dune part two`, `Wolf, 2025` → `wolf 2025`. Punctuation/case differences
    between a YouTube title and the film title stop being spurious mismatches."""
    return re.sub(r"[^0-9a-zа-яё]+", " ", s.lower()).strip()


def has_cyrillic(s: str) -> bool:
    """True if the string contains any Cyrillic letter — the language signal the
    heuristic pre-filter uses to prefer a Russian trailer over an English one
    (#141/#315). A cheap proxy for `defaultAudioLanguage`, which is out of the
    Candidate snapshot (#139)."""
    return bool(re.search(r"[а-яё]", s, re.IGNORECASE))


def original_title(raw: str) -> str:
    """Extract the original (foreign) title from a raw kinozal `@title` (#138).

    Kinozal encodes titles as `RU / Original / Year / Format`; the original title
    is the second ` / `-segment when present. It yields far better YouTube trailer
    matches than the transliterated/localised RU title, so the caller prefers it.

    Returns '' when there is no distinct original segment — the raw has no ` / `
    separator (`Dune`), or the second segment is a **service** one rather than a
    title, so the caller falls back to the clean RU title:

    * the year (`Film One / 2024 / BDRip`, i.e. `Title / Year / Format`) — same
      `(?:19|20)\\d{2}` shape as `title_year_matches`; a numeric-only original
      (e.g. `2001`) is consciously swallowed as a year (#138 Out of scope);
    * build architecture or a language code (`S.T.A.L.K.E.R. 2 / x64 / …`,
      `Fallout 2 / RU / RPG / …`) — the game grammar
      `Title / x64 / RU / Genre / Year / Format / PC (Windows)`, where the
      original title simply does not exist (#385/#412).

    A game listing that has a Russian title places its actual original in the
    same position as a film (`Marvel Spider-Man 2 / Marvel's Spider-Man 2
    (Digital Deluxe Edition) / x64 / …`). The discriminator is therefore the
    segment form, not listing category: #385 suppressed the original based on
    `t=7`, leaving these listings with only a Russian title that YouTube does
    not have (#412). Incidentally, the guard applies to every source at once,
    including `build_film_profile`, which never received `kinozal_is_game`.
    """
    parts = [p.strip() for p in raw.split(" / ")]
    if len(parts) < 2:
        return ""
    candidate = parts[1]
    if YEAR_SEGMENT_RE.fullmatch(candidate) or _SERVICE_SEGMENT_RE.fullmatch(candidate):
        return ""
    return candidate
