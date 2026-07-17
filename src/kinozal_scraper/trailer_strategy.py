"""Граница retrieval → selection подбора трейлера (#139, эпик трейлеров).

Сейчас `youtube.py` слитно делает retrieval (запрос → кандидаты) и selection
(выбор одного). Эпик разводит их: retrieval остаётся в `youtube.py` (#140),
selection переезжает сюда за `TrailerStrategy`. #139 определяет типы + Protocol
+ baseline `FirstResultStrategy` (= текущая прод-логика отбора) и НЕ меняет
прод-поведение — baseline измеряется harness'ом (`scripts/eval_trailers.py`), но
в прод-путь (`enrich_with_trailer`) не подключается до #144.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from kinozal_scraper.text_utils import title_year_matches


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
