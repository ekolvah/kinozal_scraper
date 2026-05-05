from __future__ import annotations

import logging
import string
import time
from typing import Any, Protocol, runtime_checkable

import google.api_core.exceptions
import google.generativeai as genai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from generic_pipeline import NormalizedItem

logger = logging.getLogger(__name__)


class QuotaExhausted(Exception):
    """All retry attempts hit ResourceExhausted — caller should stop or switch models."""


@runtime_checkable
class Enricher(Protocol):
    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str: ...


class NullEnricher:
    """No-op — for tests and when GOOGLE_API_KEY is absent."""

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        result: str = enrich_config.get("on_error", "")
        return result


class GeminiEnricher:
    # genai.configure() must be called once before instantiating this class.

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        prompt_template = enrich_config["prompt"]
        params = enrich_config.get("parameters", {})
        on_error: str = enrich_config.get("on_error", "")

        context: dict[str, Any] = {
            "title": item.title,
            "url": item.url,
            "description": item.description,
            "metric": item.metric,
            "source_id": item.source_id,
            **item.raw,
        }
        prompt = string.Template(prompt_template).safe_substitute(context)

        generation_config = genai.types.GenerationConfig(
            temperature=params.get("temperature", 0.2),
            max_output_tokens=params.get("max_tokens", 150),
        )

        try:
            return self._generate(prompt, generation_config)
        except Exception as exc:
            is_quota = isinstance(exc.__cause__, google.api_core.exceptions.ResourceExhausted)
            if is_quota:
                raise QuotaExhausted from exc
            logger.error("[%s] enrichment failed: %s", item.dedupe_key, exc)
            return on_error

    @retry(
        retry=retry_if_exception_type(google.api_core.exceptions.ResourceExhausted),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
    )
    def _generate(self, prompt: str, generation_config: genai.types.GenerationConfig) -> str:
        model = genai.GenerativeModel(self._model_name)
        response = model.generate_content(prompt, generation_config=generation_config)
        text: str = response.text.strip()
        return text


def _model_version_key(name: str) -> tuple[float, str]:
    """Extract version number for sorting: 'models/gemini-2.5-flash' → (2.5, 'flash')."""
    import re

    match = re.search(r"gemini-(\d+)\.(\d+)", name)
    if match:
        version = float(f"{match.group(1)}.{match.group(2)}")
        return (version, name)
    return (0.0, name)


def get_generation_models() -> list[str]:
    """Return model names with generateContent support, newer versions first."""
    try:
        names = [
            m.name
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ]
    except Exception:
        logger.warning("cannot list models")
        return []

    names.sort(key=_model_version_key, reverse=True)
    return names


class RotatingGeminiEnricher:
    """Tries multiple Gemini models before giving up on quota exhaustion."""

    _COOLDOWN = 60

    def __init__(self, model_names: list[str]) -> None:
        if not model_names:
            raise ValueError("model_names must not be empty")
        self._enrichers = [GeminiEnricher(n) for n in model_names]
        self._current = 0

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        last_exc: Exception | None = None
        for rotation in range(2):
            if rotation == 1:
                logger.warning(
                    "all %d models exhausted, waiting %ds",
                    len(self._enrichers),
                    self._COOLDOWN,
                )
                time.sleep(self._COOLDOWN)
                self._current = 0

            for _ in range(len(self._enrichers)):
                try:
                    return self._enrichers[self._current].enrich(item, enrich_config)
                except QuotaExhausted as exc:
                    prev = self._enrichers[self._current]._model_name
                    self._current = (self._current + 1) % len(self._enrichers)
                    nxt = self._enrichers[self._current]._model_name
                    logger.warning("model %s quota exhausted, trying %s", prev, nxt)
                    last_exc = exc

        raise QuotaExhausted from last_exc
