"""Structured observability for live Gemini calls (#145).

Both live LLM call sites — `GeminiEnricher._generate` (item enrichment) and
`GeminiSummarizer.summarize` (Telegram channel summaries) — run inside the daily
cron and spend tokens, but neither surfaced *how many* tokens / *how long* per
call. This module is the shared, dependency-free core they both use: pull the
token counts off the SDK response (`usage_metadata`) and emit one structured
`llm_call` breadcrumb (tokens + wall-clock latency) into the cron log.

Shared here (not inlined) because there are ≥2 real callers; a missing / partial
`usage_metadata` degrades to `None` fields + a visible `degraded` marker rather
than crashing or logging a misleading zero (§IV — visibility over silence).

Phoenix / OpenInference is intentionally *not* wired here — it stays a local,
opt-in dev recipe (see docs), never committed code that would rot untested in CI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Usage:
    """Token counts for a single Gemini call. Any field may be `None` when the
    SDK response omits `usage_metadata` (or a specific count) — a degraded-visible
    state, not an error."""

    prompt_tokens: int | None
    candidates_tokens: int | None
    total_tokens: int | None


def extract_usage(response: Any) -> Usage:
    """Read token counts off a Gemini `generate_content` response.

    Tolerant by design: a missing `usage_metadata` attribute, or any missing
    per-count field, yields `None` for that field instead of raising — the SDK
    does not guarantee the block is present, and losing observability must never
    take down a live call (§IV)."""
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return Usage(prompt_tokens=None, candidates_tokens=None, total_tokens=None)
    return Usage(
        prompt_tokens=getattr(metadata, "prompt_token_count", None),
        candidates_tokens=getattr(metadata, "candidates_token_count", None),
        total_tokens=getattr(metadata, "total_token_count", None),
    )


def log_llm_call(
    logger: logging.Logger,
    *,
    model: str,
    usage: Usage,
    latency_ms: int,
    finish_reason: str,
    outcome: str,
) -> None:
    """Emit one structured INFO breadcrumb for a completed live Gemini call.

    Missing token counts (`total_tokens is None`) append a `degraded` marker to
    `outcome` so the operator sees the metric gap in the log rather than reading a
    silent `None`/zero as a healthy call (§IV)."""
    if usage.total_tokens is None:
        outcome = f"{outcome},degraded"
    logger.info(
        "llm_call model=%s prompt_tokens=%s candidates_tokens=%s total_tokens=%s "
        "latency_ms=%s finish=%s outcome=%s",
        model,
        usage.prompt_tokens,
        usage.candidates_tokens,
        usage.total_tokens,
        latency_ms,
        finish_reason,
        outcome,
    )
