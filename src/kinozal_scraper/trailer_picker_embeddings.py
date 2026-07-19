"""#143: стратегия B подбора трейлера — re-ranker на эмбеддингах (Gemini).

`EmbeddingTrailerStrategy` реализует `TrailerStrategy` (`trailer_strategy.py`):
эмбеддит `FilmProfile` и пул `Candidate` одним батчем, считает косинус в памяти
(векторная БД не нужна на ≤10 кандидатов) и берёт argmax; если лучший косинус ниже
порога — **честный `None`** (§IV-эквивалент «нет достаточно похожего», как
`video_id is None` в стратегии A). Прод-кандидат: качество меряется на golden-set
vs A (#142) / пред-фильтр (#141), победитель — #144. Здесь чистый selection-слой +
движок; композиция heuristic→B и прод/eval-wiring несёт #144.

`Embedder` — узкая DI-граница (§II): unit-тесты подставляют double с
зафиксированными векторами, `GeminiEmbedder` — прод-реализация через `genai`.
Таксономия ошибок ротации (`QuotaExhausted/ModelUnavailable/TryNextModel`) и
классификатор — общие с `gemini_enricher.py` / стратегией A, не переизобретаются.
"""

from __future__ import annotations

import logging
import math
from typing import Protocol, cast

import google.generativeai as genai

from kinozal_scraper.gemini_enricher import classify_generate_error
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile, TrailerPick

logger = logging.getLogger(__name__)

# Стартовый порог косинуса — прод-кандидат, тюнится harness'ом на golden-set в #144.
SIMILARITY_THRESHOLD = 0.5
# task_type="semantic_similarity" — эмбеддинги оптимизированы под сравнение пар (не retrieval).
EMBED_MODEL = "models/text-embedding-004"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    """Косинус двух векторов. Нулевой вектор (норма 0) → 0.0 (degraded-visible, не
    ZeroDivisionError/NaN): пустой эмбеддинг = «не похож ни на что», штатный 0."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _film_text(film: FilmProfile) -> str:
    """Что эмбеддим для фильма: оба названия (RU+original) + год + каст — сигналы,
    по которым косинус ловит семантическое соответствие кандидату."""
    parts = [film.ru_title, film.original_title]
    if film.year:
        parts.append(str(film.year))
    if film.cast:
        parts.append(", ".join(film.cast))
    return " ".join(p for p in parts if p)


def _candidate_text(candidate: Candidate) -> str:
    return " ".join(p for p in (candidate.title, candidate.channel, candidate.description) if p)


class EmbeddingTrailerStrategy:
    """`TrailerStrategy` через эмбеддинги + косинус. Пустой пул → честный `None`
    БЕЗ вызова движка (runtime-токены не тратятся). Below-threshold — штатный отказ
    (§IV-маркер в проде на #144, БЕЗ warning); length-mismatch — аномалия контракта
    движка (None + warning, зеркалит malformed-json в стратегии A)."""

    def __init__(self, embedder: Embedder, threshold: float = SIMILARITY_THRESHOLD) -> None:
        self._embedder = embedder
        self._threshold = threshold

    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick:
        if not candidates:
            return TrailerPick(None, 0.0, "no candidates to choose from")
        texts = [_film_text(film_profile)] + [_candidate_text(c) for c in candidates]
        vectors = self._embedder.embed(texts)

        expected = len(candidates) + 1
        if len(vectors) != expected:
            reason = f"unexpected embedding count: got {len(vectors)}, expected {expected}"
            logger.warning("trailer embed-pick degraded: %s", reason)
            return TrailerPick(None, 0.0, reason)

        film_vec = vectors[0]
        best, best_score = max(
            ((c, _cosine(film_vec, v)) for c, v in zip(candidates, vectors[1:], strict=True)),
            key=lambda pair: pair[1],
        )
        if best_score < self._threshold:
            # Честный отказ: похожего нет. НЕ аномалия — reason виден, warning не эмитим.
            return TrailerPick(
                None, 0.0, f"best cosine {best_score:.3f} below threshold {self._threshold:.3f}"
            )
        confidence = max(0.0, min(1.0, best_score))
        return TrailerPick(best.video_id, confidence, f"embedding cosine {best_score:.3f}")


class GeminiEmbedder:
    """Живой движок эмбеддингов — собрат `GeminiEnricher`/`GeminiJsonGenerator`: один
    батч-вызов `genai.embed_content` (`task_type="semantic_similarity"`). API-ошибки →
    общий классификатор (`QuotaExhausted/ModelUnavailable/TryNextModel`). У эмбеддингов
    нет `finish_reason`/MAX_TOKENS, поэтому truncation-ветки стратегии A тут нет.
    `model_name` property зеркалит собратьев, чтобы #144 обернул `list[GeminiEmbedder]`
    экстракцией ротации, не переписыванием.

    genai.configure() должен быть вызван один раз до инстанцирования (как у собратьев).
    """

    def __init__(self, model_name: str = EMBED_MODEL) -> None:
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = genai.embed_content(
                model=self._model_name,
                content=texts,
                task_type="semantic_similarity",
            )
            # Индексация внутри try: malformed-ответ без ключа "embedding" (KeyError)
            # идёт через ту же таксономию (→ TryNextModel), а не сырым краком мимо неё.
            vectors = response["embedding"]
        except Exception as exc:
            raise classify_generate_error(exc)() from exc
        # SDK-стаб типизирует batch-ответ как list[float]; на списке текстов это
        # list[list[float]] (по вектору на текст) — cast выправляет тип, не поведение.
        return cast("list[list[float]]", vectors)
