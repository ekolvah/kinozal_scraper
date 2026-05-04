from __future__ import annotations

import logging
import string
from typing import Any, Protocol, runtime_checkable

import google.api_core.exceptions
import google.generativeai as genai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from generic_pipeline import NormalizedItem

logger = logging.getLogger(__name__)


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
            logger.error("[%s] Gemini enrichment failed: %s", item.dedupe_key, exc)
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
