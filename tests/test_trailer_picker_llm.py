"""RED tests for #142: LLMTrailerStrategy (стратегия A — Gemini structured-output).

Контракт парсинга детерминируется double'ом (§I/§II): `FakeJsonGenerator` отдаёт
зафиксированный JSON — тесты пришпиливают честный `None`, §IV-видимость degraded-веток
(различимый `reason` на каждую), clamp `confidence`, экономию токенов (пустой пул — без
вызова модели). `GeminiJsonGenerator` тестируется через `patch(genai.GenerativeModel)` —
устоявшийся паттерн `test_gemini_enricher.py`. Качество модели меряет harness, не unit-тест.
"""

from __future__ import annotations

import unittest
from typing import Any

from google.genai import errors

from kinozal_scraper.gemini_enricher import (
    ModelUnavailable,
    QuotaExhausted,
    TryNextModel,
)
from kinozal_scraper.trailer_picker_llm import (
    GeminiJsonGenerator,
    LLMTrailerStrategy,
)
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile


class FakeJsonGenerator:
    """Double границы JsonGenerator: отдаёт канонный JSON, ловит промпт и счётчик вызовов."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0
        self.last_prompt = ""

    def generate(self, prompt: str) -> str:
        self.calls += 1
        self.last_prompt = prompt
        return self._response


def _film() -> FilmProfile:
    return FilmProfile(ru_title="Гнев", original_title="Man on Fire", year=2026)


def _candidates() -> list[Candidate]:
    return [
        Candidate(video_id="ru_01", title="Гнев 2026 трейлер", description="официальный трейлер"),
        Candidate(video_id="en_01", title="Man on Fire 2026 Trailer", description="official"),
    ]


# ── LLMTrailerStrategy: контракт парсинга/None-ветки ──────────────────────────


class TestLLMTrailerStrategy(unittest.TestCase):
    def test_picks_candidate_from_valid_json(self) -> None:
        gen = FakeJsonGenerator('{"video_id": "ru_01", "confidence": 0.8, "reason": "ru official"}')
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertEqual(pick.video_id, "ru_01")
        self.assertAlmostEqual(pick.confidence, 0.8)

    def test_honest_none_when_model_returns_null_video_id(self) -> None:
        gen = FakeJsonGenerator('{"video_id": null, "confidence": 0.0, "reason": "no official"}')
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertIn("no official", pick.reason)

    def test_unknown_id_becomes_visible_none(self) -> None:
        gen = FakeJsonGenerator('{"video_id": "ghost", "confidence": 0.9, "reason": "x"}')
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertEqual(pick.confidence, 0.0)
        self.assertIn("unknown id", pick.reason)
        self.assertIn("ghost", pick.reason)

    def test_malformed_json_becomes_visible_none(self) -> None:
        gen = FakeJsonGenerator("not json{{")
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertEqual(pick.confidence, 0.0)
        self.assertIn("malformed json", pick.reason)

    def test_missing_video_id_key_becomes_visible_none(self) -> None:
        gen = FakeJsonGenerator('{"confidence": 0.5, "reason": "x"}')
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertIn("missing video_id", pick.reason)

    def test_non_object_json_becomes_visible_none(self) -> None:
        # Синтаксически валидный JSON, но не object (список/число) → data["video_id"]
        # падает TypeError, а не KeyError/JSONDecodeError — отдельная §IV-ветка.
        gen = FakeJsonGenerator("[1, 2]")
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertEqual(pick.confidence, 0.0)
        self.assertIn("not an object", pick.reason)

    def test_out_of_range_confidence_is_clamped(self) -> None:
        high = FakeJsonGenerator('{"video_id": "ru_01", "confidence": 1.5, "reason": "x"}')
        self.assertEqual(LLMTrailerStrategy(high).pick(_film(), _candidates()).confidence, 1.0)
        low = FakeJsonGenerator('{"video_id": "ru_01", "confidence": -0.2, "reason": "x"}')
        self.assertEqual(LLMTrailerStrategy(low).pick(_film(), _candidates()).confidence, 0.0)

    def test_non_numeric_confidence_degrades_visibly(self) -> None:
        gen = FakeJsonGenerator('{"video_id": "ru_01", "confidence": "high", "reason": "x"}')
        pick = LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertEqual(pick.video_id, "ru_01")
        self.assertEqual(pick.confidence, 0.0)

    def test_empty_candidates_returns_none_without_calling_model(self) -> None:
        gen = FakeJsonGenerator('{"video_id": "ru_01", "confidence": 0.8, "reason": "x"}')
        pick = LLMTrailerStrategy(gen).pick(_film(), [])
        self.assertIsNone(pick.video_id)
        self.assertEqual(gen.calls, 0)

    def test_prompt_lists_candidate_ids_and_film_titles(self) -> None:
        gen = FakeJsonGenerator('{"video_id": "ru_01", "confidence": 0.5, "reason": "x"}')
        LLMTrailerStrategy(gen).pick(_film(), _candidates())
        self.assertIn("ru_01", gen.last_prompt)
        self.assertIn("en_01", gen.last_prompt)
        self.assertIn("Гнев", gen.last_prompt)
        self.assertIn("Man on Fire", gen.last_prompt)


# ── GeminiJsonGenerator: structured-output + маппинг ошибок ротации ────────────


class _FakeCandidate:
    def __init__(self, finish_reason: str) -> None:
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text: str, finish_reason: str = "STOP") -> None:
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)]


def _api_error(code: int) -> errors.ClientError:
    """google.genai APIError double carrying only `.code` (the taxonomy field)."""
    e = errors.ClientError.__new__(errors.ClientError)
    e.code = code
    e.status = "RESOURCE_EXHAUSTED" if code == 429 else "NOT_FOUND"
    e.message = str(code)
    return e


class _FakeModels:
    """Stand-in for `client.models`: captures the config, returns canned / raises."""

    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error
        self.captured_config: Any = None

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> _FakeResponse:  # noqa: ARG002
        self.captured_config = config
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeClient:
    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self.models = _FakeModels(response, error)


class TestGeminiJsonGenerator(unittest.TestCase):
    def test_returns_response_text(self) -> None:
        client = _FakeClient(
            _FakeResponse('{"video_id": "ru_01", "confidence": 0.8, "reason": "x"}')
        )
        out = GeminiJsonGenerator("models/gemini-2.5-flash", client).generate("prompt")
        self.assertIn("ru_01", out)

    def test_uses_json_mime_type(self) -> None:
        client = _FakeClient(_FakeResponse('{"video_id": null, "confidence": 0, "reason": ""}'))
        GeminiJsonGenerator("m", client).generate("p")
        self.assertEqual(client.models.captured_config.response_mime_type, "application/json")

    def test_resource_exhausted_maps_to_quota_exhausted(self) -> None:
        client = _FakeClient(error=_api_error(429))
        with self.assertRaises(QuotaExhausted):
            GeminiJsonGenerator("m", client).generate("p")

    def test_not_found_maps_to_model_unavailable(self) -> None:
        client = _FakeClient(error=_api_error(404))
        with self.assertRaises(ModelUnavailable):
            GeminiJsonGenerator("m", client).generate("p")

    def test_truncated_response_maps_to_try_next_model(self) -> None:
        client = _FakeClient(_FakeResponse('{"video_id":', finish_reason="MAX_TOKENS"))
        with self.assertRaises(TryNextModel):
            GeminiJsonGenerator("m", client).generate("p")


if __name__ == "__main__":
    unittest.main()
