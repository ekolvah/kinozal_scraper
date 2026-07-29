"""Граница retrieval → selection подбора трейлера (#139, эпик трейлеров).

Retrieval (запрос → кандидаты) живёт в `youtube.py` (#140), selection (выбор одного)
— здесь за `TrailerStrategy`. #139 задал типы + Protocol + baseline `FirstResultStrategy`
(= прежняя прод-логика отбора, теперь только под harness/сравнение). Прод-путь
`kinozal_pipeline.enrich_with_trailer` (#144) отбирает язык-aware `HeuristicStrategy`
(#141) = eval `default_strategy()` — RU-приоритет закрывает регрессию #315.
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


def _is_sequel_number(token: str) -> bool:
    """Одиночный номер сиквела («2» в «Джокер 2»), НЕ 4-значный год (год —
    дискриминатор в `title_year_matches`, его пропускать нельзя)."""
    return token.isdigit() and not re.fullmatch(r"(?:19|20)\d{2}", token)


def _title_tokens_in(film_tokens: list[str], cand_tokens: list[str]) -> bool:
    """`film_tokens` идут по порядку и подряд в `cand_tokens`, допуская ОДИН
    интерспёрснутый numeric sequel-токен МЕЖДУ токенами названия — каналы
    вставляют номер сиквела, которого нет в ru_title («Джокер 2: Безумие на
    двоих» vs ru_title «Джокер: Безумие на двоих»), и цифра рвёт непрерывную
    фразу. Точное токен-равенство сохраняет word-boundary (#324: «Дом»≠«Домашний»);
    порядок обязателен, одиночный пропуск — не fuzzy/edit-distance (§VII)."""
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
    """Baseline = прежняя прод-логика отбора (одиночный `get_trailer_url`, удалён в
    #144): первый кандидат, чей title проходит год-фильтр. Теперь только под
    harness/сравнение (прод отбирает `HeuristicStrategy` #141). Год-правило шарится
    через общий `title_year_matches` (§II — не переизобретается). При falsy year
    (None/0) год-фильтр не применяется, берётся первый кандидат (`not year`, а не
    `year is None`)."""

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

    1. **relevance** — кандидат релевантен, если токены `ru_title` ИЛИ
       `original_title` идут по порядку в title (допуская один интерспёрснутый
       numeric sequel-токен — «Джокер 2» матчит «Джокер») И год совпадает
       (общий `title_year_matches`, только при truthy year — зеркало прода/baseline).
    2. **ранжирование** по ключу `(is_ru, cast_hits, trailer_signal)`, desc,
       stable-порядок среди равных: язык **первичен** (#315 — при матче фильма
       RU>EN), каст в `description` — вторичный тай-брейк ВНУТРИ одного языка
       (EN-реакция с именем актёра в описании не побьёт RU-трейлер), `trailer_signal`
       (трейлер/дубляж − тизер) — третичный: настоящий трейлер бьёт новостной
       клип/тизер при равном языке+касте. `cast_hits` = сколько первых ≤2 имён
       каста нашлись строкой в описании.

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
        is_ru, cast_hits, _trailer = best_key
        reason = "cast tie-break" if cast_hits else ("ru language" if is_ru else "sole match")
        return TrailerPick(best.video_id, 0.9, reason)

    _TRAILER_WORDS = ("трейлер", "trailer", "дубляж")
    _TEASER_WORDS = ("тизер", "teaser")

    def _relevant(self, profile: FilmProfile, candidate: Candidate) -> bool:
        """Матч названия+года. Кроме двух названий профиля пробуется `original_title`
        БЕЗ скобочного хвоста (`Marvel's Spider-Man 2 (Digital Deluxe Edition)` →
        `Marvel's Spider-Man 2`) — иначе издание, которого в заголовке трейлера не
        бывает, отсекает все настоящие трейлеры игры.

        Числа ниже — из одного замера 3764 raw-заголовков (Sheets, 2026-07-29), но
        считают РАЗНОЕ, поэтому названы явно: 238 заголовков имеют скобку во втором
        сегменте вообще; из них 78 — игровые с суффиксом издания (это класс, который
        чинится здесь), 160 — не-игровые, где в скобке альтернативное название
        (`Ojingeo geim (Squid Game)`). Отдельная метрика — 96 игровых раздач, у
        которых второй сегмент вообще является названием, а не служебным токеном
        (класс, который чинит гард в `text_utils.original_title`); со скобкой из них
        как раз 78.

        **Только `original_title`, не `ru_title`.** Во втором сегменте скобка несёт
        издание (игры) или альтернативное название — в обоих случаях база остаётся
        валидным названием (маркер части/сезона там встретился 1 раз из 238, и там
        номер уже в самом названии: `Heroes of Might and Magic IV (4) (Complete)`).
        В русском
        же скобка бывает уточнением части/сезона, и срез схлопнул бы `Дюна (Часть
        вторая)` до франшизы `Дюна`, которую `_title_tokens_in` со своим
        numeric-skip пропустил бы в «Дюна 2» (год не спасает: в заголовках
        трейлеров его обычно нет). В проде `ru_title` и так обрезан по `(`
        (`enrich_with_trailer`), так что срез здесь ничего бы не добавил, кроме
        этого риска.

        **Базовый вариант равноправен, а не fallback «при пустом relevant».** Тот
        вариант был в плане и отвергнут замером: у `Marvel's Spider-Man 2` полное
        название с изданием подряд входит в «All Marvel's Spider-Man 2 Digital
        Deluxe Edition Suits» (нарезка костюмов), то есть первый проход давал
        непустой relevant из ОДНОГО мусорного ролика и до трейлеров не доходил.
        Отличать трейлер от не-трейлера — работа `_trailer_signal` в `_rank`, а не
        relevance. Срез делается по сырой строке: `normalize_title` скобку съедает.
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
        """Within-language сигнал «это настоящий трейлер»: минимальный
        keyword-набор (трейлер/дубляж +1) минус тизер/teaser (−1). Разрывает
        RU-ничью в пользу трейлера над новостным клипом/тизером вместо порядка в
        пуле (#141-дефект). Зеркалит `cast_hits` третьим измерением; НЕ
        channel-authority scorer и НЕ recency — 2 дефекта не оправдывают их (§VII)."""
        title = normalize_title(candidate.title)
        return sum(w in title for w in self._TRAILER_WORDS) - sum(
            w in title for w in self._TEASER_WORDS
        )
