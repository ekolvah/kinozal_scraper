"""Матч названия+года (title_year_matches)."""

from __future__ import annotations

import re


def title_year_matches(title: str, film_year: int) -> bool:
    """Return False if the video title explicitly mentions a year other than film_year."""
    title_years = {int(m) for m in re.findall(r"\b((?:19|20)\d{2})\b", title)}
    return not title_years or film_year in title_years


def original_title(raw: str) -> str:
    """Extract the original (foreign) title from a raw kinozal `@title` (#138).

    Kinozal encodes titles as `RU / Original / Year / Format`; the original title
    is the second ` / `-segment when present. It yields far better YouTube trailer
    matches than the transliterated/localised RU title, so the caller prefers it.

    Returns '' when there is no distinct original segment — either the raw has no
    ` / ` separator (`Дюна`) or the second segment is just the year
    (`Film One / 2024 / BDRip`, i.e. `Title / Year / Format`) — so the caller
    falls back to the clean RU title. The year guard uses the same `(?:19|20)\\d{2}`
    shape as `title_year_matches`; a numeric-only original (e.g. `2001`) is
    consciously swallowed as a year (rare edge, see #138 Out of scope).
    """
    parts = [p.strip() for p in raw.split(" / ")]
    if len(parts) < 2:
        return ""
    candidate = parts[1]
    if re.fullmatch(r"(?:19|20)\d{2}", candidate):
        return ""
    return candidate
