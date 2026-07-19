"""RED tests for #143: EmbeddingTrailerStrategy (стратегия B — re-ranker на эмбеддингах).

Контракт скоринга детерминируется double'ом (§I/§II): `FakeEmbedder` отдаёт
зафиксированные векторы — тесты пришпиливают argmax-выбор, порог→честный `None`
(штатный отказ, БЕЗ warning — как `video_id is None` в стратегии A), §IV-видимость
аномалии контракта (length-mismatch → None + WARNING), pure-косинус (zero-вектор →
0.0, не ZeroDivisionError), экономию токенов (пустой пул — без вызова движка).
`GeminiEmbedder` тестируется через `patch(genai.embed_content)` — устоявшийся паттерн
`test_gemini_enricher.py`. Качество модели меряет harness live-прогоном, не unit-тест.
"""

from __future__ import annotations

import logging
import unittest
from typing import Any
from unittest import mock

import google.api_core.exceptions

from kinozal_scraper.gemini_enricher import ModelUnavailable, QuotaExhausted
from kinozal_scraper.trailer_picker_embeddings import (
    EmbeddingTrailerStrategy,
    GeminiEmbedder,
    _cosine,
)
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile


def _film() -> FilmProfile:
    return FilmProfile(ru_title="Гнев", original_title="Man on Fire", year=2026)


def _candidates() -> list[Candidate]:
    return [
        Candidate(video_id="ru_01", title="Гнев 2026 трейлер", description="официальный трейлер"),
        Candidate(video_id="en_01", title="Man on Fire 2026 Trailer", description="official"),
    ]


class FakeEmbedder:
    """Double границы Embedder: отдаёт зафиксированные векторы, ловит тексты и счётчик."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.calls = 0
        self.last_texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.last_texts = list(texts)
        return self._vectors


# ── pure cosine ───────────────────────────────────────────────────────────────


class TestCosine(unittest.TestCase):
    def test_identical_vectors_cosine_is_one(self) -> None:
        self.assertAlmostEqual(_cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_orthogonal_vectors_cosine_is_zero(self) -> None:
        self.assertAlmostEqual(_cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector_cosine_is_zero_not_crash(self) -> None:
        # Нулевой вектор → деление на 0; должно деградировать в 0.0, не ZeroDivisionError.
        self.assertEqual(_cosine([0.0, 0.0], [1.0, 1.0]), 0.0)


# ── EmbeddingTrailerStrategy: скоринг / None-ветки ────────────────────────────


class TestEmbeddingTrailerStrategy(unittest.TestCase):
    def test_picks_most_similar_candidate(self) -> None:
        # film ‖ ru_01 (cos=1), en_01 ортогонален (cos=0) → выбор ru_01.
        emb = FakeEmbedder([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        pick = EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), _candidates())
        self.assertEqual(pick.video_id, "ru_01")

    def test_below_threshold_returns_visible_none(self) -> None:
        # Оба кандидата ортогональны film (cos=0) < порога → честный None + reason.
        emb = FakeEmbedder([[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
        pick = EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertIn("below threshold", pick.reason)

    def test_below_threshold_does_not_warn(self) -> None:
        # Штатный отказ (honest-None-эквивалент) — НЕ аномалия, warning не эмитим.
        emb = FakeEmbedder([[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
        with self.assertLogs("kinozal_scraper.trailer_picker_embeddings", level="WARNING") as cm:
            logging.getLogger("kinozal_scraper.trailer_picker_embeddings").warning("probe")
            EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), _candidates())
        self.assertEqual(cm.output, ["WARNING:kinozal_scraper.trailer_picker_embeddings:probe"])

    def test_empty_candidates_returns_none_without_calling_embedder(self) -> None:
        emb = FakeEmbedder([[1.0, 0.0]])
        pick = EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), [])
        self.assertIsNone(pick.video_id)
        self.assertEqual(emb.calls, 0)

    def test_length_mismatch_returns_visible_none(self) -> None:
        # Движок вернул != N+1 векторов (аномалия контракта) → None + reason + WARNING.
        emb = FakeEmbedder([[1.0, 0.0], [1.0, 0.0]])  # 2 вместо 3 (film + 2 cand)
        with self.assertLogs("kinozal_scraper.trailer_picker_embeddings", level="WARNING") as cm:
            pick = EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), _candidates())
        self.assertIsNone(pick.video_id)
        self.assertIn("unexpected embedding count", pick.reason)
        self.assertTrue(any("unexpected embedding count" in line for line in cm.output))

    def test_confidence_reflects_similarity(self) -> None:
        # film=[1,1], winner=[1,0] → cos = 1/sqrt(2) ≈ 0.7071.
        emb = FakeEmbedder([[1.0, 1.0], [1.0, 0.0], [0.0, 1.0]])
        pick = EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), _candidates())
        self.assertEqual(pick.video_id, "ru_01")
        self.assertAlmostEqual(pick.confidence, 0.7071, places=3)

    def test_embeds_film_and_all_candidates(self) -> None:
        emb = FakeEmbedder([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        EmbeddingTrailerStrategy(emb, threshold=0.5).pick(_film(), _candidates())
        self.assertIn("Гнев", emb.last_texts[0])
        self.assertIn("Man on Fire", emb.last_texts[0])
        joined = " ".join(emb.last_texts[1:])
        self.assertIn("Гнев 2026 трейлер", joined)
        self.assertIn("Man on Fire 2026 Trailer", joined)


# ── GeminiEmbedder: живой движок + маппинг ошибок ротации ──────────────────────


class TestGeminiEmbedder(unittest.TestCase):
    def _patch(self, **kwargs: Any) -> Any:
        return mock.patch("kinozal_scraper.trailer_picker_embeddings.genai.embed_content", **kwargs)

    def test_returns_vectors(self) -> None:
        with self._patch(return_value={"embedding": [[1.0, 0.0], [0.0, 1.0]]}):
            out = GeminiEmbedder("models/text-embedding-004").embed(["a", "b"])
        self.assertEqual(out, [[1.0, 0.0], [0.0, 1.0]])

    def test_uses_semantic_similarity_task_type(self) -> None:
        with self._patch(return_value={"embedding": [[1.0, 0.0]]}) as m:
            GeminiEmbedder("m").embed(["a"])
        self.assertEqual(m.call_args.kwargs["task_type"], "semantic_similarity")

    def test_resource_exhausted_maps_to_quota_exhausted(self) -> None:
        exc = google.api_core.exceptions.ResourceExhausted("quota")
        with self._patch(side_effect=exc), self.assertRaises(QuotaExhausted):
            GeminiEmbedder("m").embed(["a"])

    def test_not_found_maps_to_model_unavailable(self) -> None:
        exc = google.api_core.exceptions.NotFound("gone")
        with self._patch(side_effect=exc), self.assertRaises(ModelUnavailable):
            GeminiEmbedder("m").embed(["a"])


if __name__ == "__main__":
    unittest.main()
