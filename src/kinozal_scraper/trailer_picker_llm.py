"""#142: стратегия A подбора трейлера — LLM-picker (Gemini structured-output).

`LLMTrailerStrategy` реализует `TrailerStrategy` (`trailer_strategy.py`): строит промпт
из `FilmProfile` + пула `Candidate`, зовёт Gemini в JSON-режиме и парсит ответ в
`TrailerPick` с **честным `None`** (модель может отказаться — §IV, не навязывать чужой
трейлер). Задуман как дорогой fallback на «спорных» кандидатах пред-фильтра #141;
композицию heuristic→LLM и подключение в прод/eval несёт #144 — здесь чистый
selection-слой + structured-Gemini движок.

`JsonGenerator` — узкая DI-граница (§II): unit-тесты подставляют double с
зафиксированным JSON, `GeminiJsonGenerator` — прод-реализация через `genai`.
Таксономия ошибок ротации (`QuotaExhausted/ModelUnavailable/TryNextModel`) и
классификатор — общие с `gemini_enricher.py`, не переизобретаются.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

import google.generativeai as genai

from kinozal_scraper.gemini_enricher import (
    TryNextModel,
    _extract_finish_reason,
    classify_generate_error,
)
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile, TrailerPick

logger = logging.getLogger(__name__)

# Response-схема structured output. `video_id` nullable → честный «нет подходящего»:
# модель обязана вернуть ключ, но вправе поставить null, а не выдумывать id.
PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "video_id": {"type": "string", "nullable": True},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["video_id", "confidence", "reason"],
}


class JsonGenerator(Protocol):
    def generate(self, prompt: str) -> str: ...


def _build_prompt(film: FilmProfile, candidates: list[Candidate]) -> str:
    """Промпт для модели: фильм + пронумерованный пул кандидатов с их `video_id`,
    чтобы модель вернула id одного из них (или null). Русскоязычный приоритет при
    равном соответствии — #315."""
    lines = [
        "Выбери ОФИЦИАЛЬНЫЙ трейлер фильма среди кандидатов YouTube.",
        f"Фильм: {film.ru_title} / {film.original_title} ({film.year}).",
    ]
    if film.cast:
        lines.append(f"В ролях: {', '.join(film.cast)}.")
    lines.append("")
    lines.append("Кандидаты:")
    for c in candidates:
        lines.append(f"- video_id={c.video_id}: {c.title!r} [канал: {c.channel}] {c.description}")
    lines.append("")
    lines.append(
        "Верни JSON {video_id, confidence, reason}. video_id — id лучшего кандидата "
        "или null, если ни один не является официальным трейлером этого фильма. "
        "При равном соответствии предпочти русскоязычный трейлер. confidence — число [0,1]."
    )
    return "\n".join(lines)


def _clamp_confidence(value: object) -> float:
    """confidence в `[0,1]`; не-число/None/bool → 0.0 (degraded-visible, не краш,
    не тихий дефолт). Контракт диапазона общий с baseline (1.0) / ambiguous (0.3)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _parse_pick(raw: str, valid_ids: set[str]) -> TrailerPick:
    """JSON-ответ модели → `TrailerPick`. Каждая degraded-ветка §IV-видима:
    различимый `reason` + WARNING-лог в точке детекции (не только поле, которое
    сейчас читает лишь offline-harness). Узкий catch — не глушить прочие баги."""
    try:
        data = json.loads(raw)
        video_id = data["video_id"]
    except json.JSONDecodeError:
        reason = f"malformed json from llm: {raw[:80]!r}"
        logger.warning("trailer llm-pick degraded: %s", reason)
        return TrailerPick(None, 0.0, reason)
    except KeyError:
        reason = "missing video_id key in llm response"
        logger.warning("trailer llm-pick degraded: %s", reason)
        return TrailerPick(None, 0.0, reason)
    except TypeError:
        # json.loads вернул не-object (list/число) → индексация строкой падает.
        reason = f"malformed json from llm: not an object ({raw[:80]!r})"
        logger.warning("trailer llm-pick degraded: %s", reason)
        return TrailerPick(None, 0.0, reason)

    confidence = _clamp_confidence(data.get("confidence"))
    reason = str(data.get("reason", ""))

    if video_id is None:
        return TrailerPick(None, confidence, reason)
    if video_id not in valid_ids:
        msg = f"llm returned unknown id {video_id!r} (not in candidate pool)"
        logger.warning("trailer llm-pick degraded: %s", msg)
        return TrailerPick(None, 0.0, msg)
    return TrailerPick(video_id, confidence, reason)


class LLMTrailerStrategy:
    """`TrailerStrategy` через Gemini structured-output. Пустой пул → честный
    `None` БЕЗ вызова модели (runtime-токены не тратятся на заведомо пустой выбор)."""

    def __init__(self, generator: JsonGenerator) -> None:
        self._generator = generator

    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick:
        if not candidates:
            return TrailerPick(None, 0.0, "no candidates to choose from")
        raw = self._generator.generate(_build_prompt(film_profile, candidates))
        return _parse_pick(raw, {c.video_id for c in candidates})


class GeminiJsonGenerator:
    """structured-output собрат `GeminiEnricher`: один вызов Gemini в JSON-режиме
    (`response_mime_type` + `response_schema`). API-ошибки → общий классификатор
    (`QuotaExhausted/ModelUnavailable/TryNextModel`); truncation (MAX_TOKENS/SAFETY
    → невалидный JSON) → `TryNextModel` — сигнал retry для #144-ротатора, не тихая
    деградация. `model_name` property зеркалит собрата, чтобы #144 обернул
    `list[GeminiJsonGenerator]` экстракцией ротации, не переписыванием.

    genai.configure() должен быть вызван один раз до инстанцирования (как у собрата).
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(self, prompt: str) -> str:
        generation_config = genai.types.GenerationConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=PICK_SCHEMA,
        )
        try:
            model = genai.GenerativeModel(self._model_name)
            response = model.generate_content(prompt, generation_config=generation_config)
        except Exception as exc:
            raise classify_generate_error(exc)() from exc

        finish_reason = _extract_finish_reason(response)
        if finish_reason in ("MAX_TOKENS", "SAFETY"):
            logger.warning(
                "[%s] trailer-pick truncated (%s) — trying next model",
                self._model_name,
                finish_reason,
            )
            raise TryNextModel(finish_reason)
        text: str = (response.text or "").strip()
        return text
