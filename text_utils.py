from __future__ import annotations

import re


def title_year_matches(title: str, film_year: int) -> bool:
    """Return False if the video title explicitly mentions a year other than film_year."""
    title_years = {int(m) for m in re.findall(r"\b((?:19|20)\d{2})\b", title)}
    return not title_years or film_year in title_years
