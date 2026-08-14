"""#142: trailer-selection strategy A — LLM picker (Gemini structured output).

`LLMTrailerStrategy` implements `TrailerStrategy` (`trailer_strategy.py`): it builds
a prompt from `FilmProfile` and a `Candidate` pool, calls Gemini in JSON mode, and
parses the result into `TrailerPick` with an **honest `None`** (the model may decline;
§IV prohibits forcing an unrelated trailer). It is an expensive fallback for disputed
candidates after prefilter #141; #144 owns heuristic→LLM composition and production/eval
wiring. This module contains the pure selection layer and structured-Gemini engine.

`JsonGenerator` is a narrow DI boundary (§II): unit tests inject a double with fixed
JSON, while `GeminiJsonGenerator` is the production `genai` implementation. Rotation
error taxonomy (`QuotaExhausted/ModelUnavailable/TryNextModel`) and its classifier are
shared with `gemini_enricher.py`, not recreated here.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from google.genai import types

from kinozal_scraper.gemini_enricher import (
    GenaiClient,
    ModelConfigRejected,
    TryNextModel,
    _extract_finish_reason,
    _thinking_policy,
    classify_generate_error,
    generate_with_thinking_fallback,
)
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile, TrailerPick

logger = logging.getLogger(__name__)

# Structured-output response schema. Nullable `video_id` means an honest “no suitable
# candidate”: the model must return the key but may use null instead of inventing an id.
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
    """Model prompt: film plus numbered candidates with their `video_id`, so the
    model returns one candidate id (or null). Ties prefer Russian-language output
    per #315."""
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
    """Clamp confidence to `[0,1]`; non-number/None/bool → 0.0 (visible degradation,
    not a crash or silent default). The range contract is shared by baseline (1.0)
    and ambiguous (0.3)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _parse_pick(raw: str, valid_ids: set[str]) -> TrailerPick:
    """Convert a model JSON response to `TrailerPick`. Every degraded branch is
    §IV-visible: a distinct `reason` plus a WARNING at detection, rather than only
    a field currently read by the offline harness. Catches remain narrow."""
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
        # `json.loads` returned a non-object (list/number), so string indexing fails.
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
    """`TrailerStrategy` through Gemini structured output. An empty pool → honest
    `None` WITHOUT a model call (runtime tokens are not spent on a known-empty pick)."""

    def __init__(self, generator: JsonGenerator) -> None:
        self._generator = generator

    def pick(self, film_profile: FilmProfile, candidates: list[Candidate]) -> TrailerPick:
        if not candidates:
            return TrailerPick(None, 0.0, "no candidates to choose from")
        raw = self._generator.generate(_build_prompt(film_profile, candidates))
        return _parse_pick(raw, {c.video_id for c in candidates})


class GeminiJsonGenerator:
    """Structured-output sibling of `GeminiEnricher`: one Gemini JSON-mode call
    (`response_mime_type` + `response_schema`). API errors use the shared classifier
    (`QuotaExhausted/ModelUnavailable/TryNextModel`); truncation (MAX_TOKENS/SAFETY
    → invalid JSON) raises `TryNextModel`, the retry signal for #144 rotation rather
    than silent degradation. The `model_name` property mirrors its sibling so #144
    wraps `list[GeminiJsonGenerator]` with rotation extraction, not a rewrite.

    `client` (genai.Client) is injected into the constructor (§II: ready client, not credentials).
    """

    def __init__(self, model_name: str, client: GenaiClient) -> None:
        self._model_name = model_name
        self._client = client
        self._thinking_config, self._thinking_level = _thinking_policy(model_name)

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(self, prompt: str) -> str:
        config = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=PICK_SCHEMA,
            thinking_config=self._thinking_config,
        )
        try:
            response, effective_level = generate_with_thinking_fallback(
                self._client,
                self._model_name,
                prompt,
                config,
                self._thinking_level,
            )
        except Exception as exc:
            if (
                self._thinking_level == "minimal"
                and classify_generate_error(exc) is ModelConfigRejected
            ):
                self._thinking_level = "low"
            raise classify_generate_error(exc)() from exc
        self._thinking_level = effective_level

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
