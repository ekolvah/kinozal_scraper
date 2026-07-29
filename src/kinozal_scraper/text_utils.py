"""Матч названия+года (title_year_matches)."""

from __future__ import annotations

import re

# A 4-digit release year (1900–2099) as it appears as a WHOLE ` / `-segment of a
# raw kinozal title. Single canonical shape reused by `original_title` and the
# pipeline's `_dedupe_key` (#363) so the raw-title parse isn't spelled with three
# drifting regexes. The `19|20` alternation (not the narrower `20\d{2}`) admits
# pre-2000 films.
YEAR_SEGMENT_RE = re.compile(r"(?:19|20)\d{2}")

# Служебные формы второго ` / `-сегмента: архитектура сборки и языковой код. Оба
# стоят на месте оригинального названия у части раздач, поэтому дискриминатор
# «есть ли у раздачи оригинал» — это форма сегмента, а не категория листинга
# (#412; #385 отличал их по `t=7` и терял оригинал у локализованных игр).
# Набор закрыт замером всех 3764 raw-заголовков из Sheets: `x64` — 888, `RU` —
# 139, `EN` — 1, ничего иного служебного во второй позиции нет. `x86`/`x32` в
# выгрузке не встретились, но названы грамматикой #385 — держим, чтобы первая же
# такая раздача не поехала мусорным запросом. Эвристика «короткий → служебный»
# запрещена тем же замером: `Silo`, `From`, `Halo`, `Apex` — настоящие названия.
_SERVICE_SEGMENT_RE = re.compile(r"x(?:64|86|32)|RU|EN", re.IGNORECASE)


def title_year_matches(title: str, film_year: int) -> bool:
    """Return False if the video title explicitly mentions a year other than film_year."""
    title_years = {int(m) for m in re.findall(r"\b((?:19|20)\d{2})\b", title)}
    return not title_years or film_year in title_years


def normalize_title(s: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to single spaces for substring
    title-matching (#141). Keeps Latin, Cyrillic and digits; `Dune: Part Two!` →
    `dune part two`, `Волк, 2025` → `волк 2025`. Punctuation/case differences
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
    separator (`Дюна`), or the second segment is a **service** one rather than a
    title, so the caller falls back to the clean RU title:

    * the year (`Film One / 2024 / BDRip`, i.e. `Title / Year / Format`) — same
      `(?:19|20)\\d{2}` shape as `title_year_matches`; a numeric-only original
      (e.g. `2001`) is consciously swallowed as a year (#138 Out of scope);
    * build architecture or a language code (`S.T.A.L.K.E.R. 2 / x64 / …`,
      `Fallout 2 / RU / RPG / …`) — the game grammar
      `Название / x64 / RU / Жанр / Год / Формат / PC (Windows)`, where the
      original title simply does not exist (#385/#412).

    Игровая раздача, у которой русское название есть, кладёт настоящий оригинал
    ровно туда же, куда фильм (`Marvel Человек-Паук 2 / Marvel's Spider-Man 2
    (Digital Deluxe Edition) / x64 / …`) — поэтому дискриминатор здесь, в форме
    сегмента, а не в категории листинга: #385 гасил оригинал по признаку `t=7`
    и у таких раздач оставлял в запросе только русское название, которого на
    YouTube нет (#412). Побочно гард действует для всех источников сразу, включая
    `build_film_profile`, куда `kinozal_is_game` не был проброшен вовсе.
    """
    parts = [p.strip() for p in raw.split(" / ")]
    if len(parts) < 2:
        return ""
    candidate = parts[1]
    if YEAR_SEGMENT_RE.fullmatch(candidate) or _SERVICE_SEGMENT_RE.fullmatch(candidate):
        return ""
    return candidate
