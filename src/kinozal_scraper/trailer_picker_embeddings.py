"""#143: trailer-selection strategy B — embedding re-ranker (Gemini).

`EmbeddingTrailerStrategy` implements `TrailerStrategy` (`trailer_strategy.py`): it
embeds `FilmProfile` and the `Candidate` pool in one batch, computes cosine in memory
(no vector DB is needed for ≤10 candidates), and takes argmax. Below threshold means
an **honest `None`** (§IV equivalent of “nothing similar enough,” like `video_id is None`
in strategy A). Quality is measured on a golden set against A (#142) and prefilter
(#141); #144 selects and wires the winner. This module is the pure selection layer and engine.

`Embedder` is a narrow DI boundary (§II): unit tests inject a double with fixed vectors,
and `GeminiEmbedder` is the production `genai` implementation. Rotation error taxonomy
(`QuotaExhausted/ModelUnavailable/TryNextModel`) and classifier are shared with
`gemini_enricher.py` and strategy A, not recreated.
"""

from __future__ import annotations

import logging
import math
from typing import Protocol, cast

from google.genai import types

from kinozal_scraper.gemini_enricher import GenaiClient, classify_generate_error
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile, TrailerPick

logger = logging.getLogger(__name__)

# Initial cosine threshold is a production candidate tuned by the #144 golden-set harness.
SIMILARITY_THRESHOLD = 0.5
# `task_type="semantic_similarity"` optimizes embeddings for pair comparison, not retrieval.
EMBED_MODEL = "models/text-embedding-004"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine of two vectors. A zero vector (norm 0) → 0.0 (visible degradation,
    not ZeroDivisionError/NaN): an empty embedding is normally similar to nothing."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _film_text(film: FilmProfile) -> str:
    """Film embedding input: both titles (RU+original), year, and cast—the signals
    by which cosine detects semantic correspondence to a candidate."""
    parts = [film.ru_title, film.original_title]
    if film.year:
        parts.append(str(film.year))
    if film.cast:
        parts.append(", ".join(film.cast))
    return " ".join(p for p in parts if p)


def _candidate_text(candidate: Candidate) -> str:
    return " ".join(p for p in (candidate.title, candidate.channel, candidate.description) if p)


class EmbeddingTrailerStrategy:
    """`TrailerStrategy` through embeddings and cosine. An empty pool → honest `None`
    WITHOUT calling the engine (no runtime tokens spent). Below threshold is normal
    rejection (§IV marker in #144 production, no warning); length mismatch is an engine
    contract anomaly (None + warning, mirroring malformed JSON in strategy A)."""

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
            # Honest rejection: nothing is similar. Not an anomaly; expose the reason, no warning.
            return TrailerPick(
                None, 0.0, f"best cosine {best_score:.3f} below threshold {self._threshold:.3f}"
            )
        confidence = max(0.0, min(1.0, best_score))
        return TrailerPick(best.video_id, confidence, f"embedding cosine {best_score:.3f}")


class GeminiEmbedder:
    """Live embedding engine, sibling of `GeminiEnricher`/`GeminiJsonGenerator`: one
    `genai.embed_content` batch call (`task_type="semantic_similarity"`). API errors use
    the shared classifier (`QuotaExhausted/ModelUnavailable/TryNextModel`). Embeddings
    have no `finish_reason`/MAX_TOKENS, so strategy A's truncation branch does not apply.
    The `model_name` property mirrors its siblings so #144 wraps `list[GeminiEmbedder]`
    with rotation extraction, not a rewrite.

    `client` (genai.Client) is injected into the constructor (§II: ready client, not credentials).
    """

    def __init__(self, client: GenaiClient, model_name: str = EMBED_MODEL) -> None:
        self._client = client
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.models.embed_content(
                model=self._model_name,
                contents=texts,
                config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
            )
            # Extract inside try: malformed output (`embeddings=None`) → TypeError through
            # the same taxonomy (→ TryNextModel), not an unclassified raw crash.
            embeddings = response.embeddings
            if embeddings is None:
                raise TypeError("embed_content returned no embeddings")
            vectors = [embedding.values for embedding in embeddings]
        except Exception as exc:
            raise classify_generate_error(exc)() from exc
        return cast("list[list[float]]", vectors)
