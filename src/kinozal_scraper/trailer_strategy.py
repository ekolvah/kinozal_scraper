"""Граница retrieval → selection подбора трейлера (#139, эпик трейлеров).

Сейчас `youtube.py` слитно делает retrieval (запрос → кандидаты) и selection
(выбор одного). Эпик разводит их: retrieval остаётся в `youtube.py` (#140),
selection переезжает сюда за `TrailerStrategy`. #139 определяет типы + Protocol
+ baseline `FirstResultStrategy` (= текущая прод-логика отбора) и НЕ меняет
прод-поведение — baseline измеряется harness'ом (`scripts/eval_trailers.py`), но
в прод-путь (`enrich_with_trailer`) не подключается до #144.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from kinozal_scraper.text_utils import has_cyrillic, normalize_title, title_year_matches


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Word-boundary containment: `phrase` — целое слово/фраза в `haystack`, не
    кусок слова (`дом` НЕ матчит `домашний`). Оба уже прошли `normalize_title`.
    Без boundary короткое название могло бы дать уверенный wrong-pick, войдя
    подстрокой в несвязанный candidate-title (review #324)."""
    return bool(re.search(rf"\b{re.escape(phrase)}\b", haystack))


@dataclass
class FilmProfile:
    """Что selection знает о фильме. `ru_title`/`original_title` несут языковой
    сигнал, который язык-aware стратегия (#141) использует для RU-приоритета.

    `cast`/`director`/`genre`/`description` — метаданные с details.php (#140),
    которыми пред-фильтр #141 сверяет кандидата (каст в `description` кандидата).
    Все с дефолтами: конструкции #139 и записи golden-set без метаданных строятся
    без изменений (backward-compat), а best-effort фетч деградирует в пустые."""

    ru_title: str
    original_title: str
    year: int | None
    cast: list[str] = field(default_factory=list)
    director: str = ""
    genre: str = ""
    description: str = ""


@dataclass
class Candidate:
    """Один результат YouTube-поиска. `title`/`description` — то, по чему #141
    скорит язык/матч (оба уже в `search.list.snippet`); `published_at` захвачен
    заранее под recency tie-break, чтобы не переписывать фикстуры. Сигналы из
    `videos.list` (`defaultAudioLanguage` и т.п.) сознательно вне снимка — см.
    Out of scope #139."""

    video_id: str
    title: str
    channel: str = ""
    description: str = ""
    published_at: str = ""


@dataclass
class TrailerPick:
    """Решение стратегии. `video_id=None` — сознательный «ничего не выбрал»
    (порождает §IV-маркер в проде, не тихий пропуск). `reason` несёт видимость
    атрибуции при разборе eval-скоркарты."""

    video_id: str | None
    confidence: float
    reason: str


class TrailerStrategy(Protocol):
    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick: ...


class FirstResultStrategy:
    """Baseline = текущая прод-логика `_search_youtube`: первый кандидат, чей
    title проходит год-фильтр. Год-правило шарится с продом через общий
    `title_year_matches` (§II — не переизобретается). При falsy year (None/0) —
    как прод (`if film_year:`, youtube.py) — год-фильтр не применяется, берётся
    первый кандидат (`not year`, а не `year is None`, — точное зеркало прода).
    """

    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick:
        year = film_profile.year
        for candidate in candidates:
            if not year or title_year_matches(candidate.title, year):
                return TrailerPick(candidate.video_id, 1.0, "first year-matching candidate")
        return TrailerPick(None, 0.0, "no year-matching candidate")


class HeuristicStrategy:
    """#141: детерминированный language-aware пред-фильтр (без LLM), baseline под
    будущий AI-picker (#142/#144).

    Отбор в два шага:

    1. **relevance** — кандидат релевантен, если нормализованное `ru_title` ИЛИ
       `original_title` входит подстрокой в нормализованный title И год совпадает
       (общий `title_year_matches`, только при truthy year — зеркало прода/baseline).
    2. **ранжирование** по ключу `(is_ru, cast_hits)`, desc, stable-порядок среди
       равных: язык **первичен** (#315 — при матче фильма RU>EN), каст в
       `description` — **вторичный** тай-брейк ВНУТРИ одного языка (EN-реакция с
       именем актёра в описании не побьёт RU-трейлер). `cast_hits` = сколько первых
       ≤2 имён каста нашлись строкой в описании.

    Исход: уникальный топ-ранг → уверенный pick (0.9); ≥2 равных топ-ранга → первый
    по порядку + 0.3 + `ambiguous`-маркер в `reason` (сигнал #144-fallback на LLM,
    §IV — видимая неоднозначность, не тихий уверенный выбор); ничего не прошло
    relevance → `TrailerPick(None, 0.0)`."""

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
        is_ru, cast_hits = best_key
        reason = "cast tie-break" if cast_hits else ("ru language" if is_ru else "sole match")
        return TrailerPick(best.video_id, 0.9, reason)

    def _relevant(self, profile: FilmProfile, candidate: Candidate) -> bool:
        title = normalize_title(candidate.title)
        film_titles = {normalize_title(profile.ru_title), normalize_title(profile.original_title)}
        if not any(t and _contains_phrase(title, t) for t in film_titles):
            return False
        return not profile.year or title_year_matches(candidate.title, profile.year)

    def _rank(self, candidate: Candidate, cast: list[str]) -> tuple[int, int]:
        description = normalize_title(candidate.description)
        cast_hits = sum(1 for name in cast if _contains_phrase(description, name))
        return (int(has_cyrillic(candidate.title)), cast_hits)
