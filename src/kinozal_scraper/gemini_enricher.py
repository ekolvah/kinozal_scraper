"""Enricher Protocol через Gemini: rotation / quota / retry."""

from __future__ import annotations

import logging
import os
import re
import string
import time
from typing import Any, Protocol, runtime_checkable

from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from kinozal_scraper.generic_pipeline import NormalizedItem
from kinozal_scraper.llm_observability import extract_usage, log_llm_call

logger = logging.getLogger(__name__)


# Visible marker that lands in Telegram when enrichment cannot produce a
# valid answer. Surfaces a tripwire to the operator instead of silently
# shipping garbage (Constitution Principle IV — visibility over silence).
FALLBACK_MARKER = "⚠️ summary unavailable"

# Cap raw `$description` substituted into prompts. Trending HTML <p> blocks
# regularly leak README markdown; long noisy inputs correlated with echo /
# markdown leakage in the 19.05.2026 batch.
_DESCRIPTION_MAX_LEN = 400


class QuotaExhausted(Exception):
    """All retry attempts hit ResourceExhausted — caller should stop or switch models."""


class ModelUnavailable(Exception):
    """Model returned `NotFound` (404) at GenerateContent — typically a model
    Google still lists via `ListModels` but has already disabled. The rotator
    should mark this model dead for the rest of the run instead of retrying
    it for every item (issue #128)."""


class TryNextModel(Exception):
    """Per-item failure (truncation, network timeout, any unexpected
    exception that is not 404/429). The model is otherwise healthy — same
    model may succeed on a different item — so the rotator advances to
    the next live model for *this* item but does NOT mark the model dead.
    See #130."""


class TruncatedResponse(Exception):
    """Model returned an incomplete answer (`finish_reason` MAX_TOKENS / SAFETY).
    Surfacing as exception so `enrich` can route to the visible-anomaly marker
    instead of forwarding the half-finished string to the notifier."""


def classify_generate_error(exc: BaseException) -> type[Exception]:
    """Map a raw `generate_content` failure to a rotation-exception CLASS.

    The new `google.genai` SDK raises a single `APIError` family discriminated by
    HTTP `.code`: 404 → `ModelUnavailable` (model listed but disabled), 429 →
    `QuotaExhausted`, anything else (network, InvalidArgument, non-API errors) →
    `TryNextModel`. The `.code` is read off both the exception and its
    `__cause__` (tenacity's `RetryError` wraps the last SDK error as `__cause__`;
    `GeminiEnricher` only ever sees that shape, the JSON/embedding generators see
    the raw error directly). Pure `exc → class` mapping shared by all live Gemini
    callers so the quota/unavailable taxonomy is not reinvented; context-specific
    logging stays at each call site (§II — no `item` coupling in the mapping)."""
    codes = {getattr(exc, "code", None), getattr(getattr(exc, "__cause__", None), "code", None)}
    if 404 in codes:
        return ModelUnavailable
    if 429 in codes:
        return QuotaExhausted
    return TryNextModel


_MARKDOWN_FENCE_BLOCK_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_MARKDOWN_FENCE_LINE_RE = re.compile(r"^\s*```\s*\w*\s*$", re.MULTILINE)
_LEADING_BULLET_RE = re.compile(r"^[\s>*#\-]+", re.MULTILINE)
_BOLD_WRAP_RE = re.compile(r"\*\*(.+?)\*\*")


def _sanitize_for_prompt(text: str, max_len: int = _DESCRIPTION_MAX_LEN) -> str:
    """Drop fenced code blocks, leading `*`/`#`/`>` and truncate.

    Applied to `$description` before substitution into the prompt template.
    The original `item.description` is left untouched — sanitization is
    only a defensive shaping of the prompt input.
    """
    if not text:
        return ""
    cleaned = _MARKDOWN_FENCE_BLOCK_RE.sub(" ", text)
    cleaned = _MARKDOWN_FENCE_LINE_RE.sub("", cleaned)
    cleaned = _LEADING_BULLET_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rsplit(" ", 1)[0] + "…"
    return cleaned


def _strip_markdown_wrap(text: str) -> str:
    """Remove outer ``` fences and `**bold**` wrappers from a model response.

    Conservative: we strip wrappers but keep content. Leaves inline asterisks
    alone — they could be legitimate characters in the answer.
    """
    stripped = _MARKDOWN_FENCE_LINE_RE.sub("", text).strip()
    stripped = _BOLD_WRAP_RE.sub(r"\1", stripped)
    stripped = re.sub(r"^[\s]*[*\-]\s+", "", stripped, flags=re.MULTILINE)
    return stripped.strip()


def _extract_finish_reason(response: Any) -> str:
    """Return the candidate's `finish_reason` as a string ('STOP', 'MAX_TOKENS', …).

    Tolerates both enum (with `.name`) and raw int/str shapes — different
    SDK versions and the test doubles use different forms.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "UNKNOWN"
    finish_reason = getattr(candidates[0], "finish_reason", None)
    if finish_reason is None:
        return "UNKNOWN"
    name = getattr(finish_reason, "name", None)
    return str(name) if name is not None else str(finish_reason)


@runtime_checkable
class GenaiClient(Protocol):
    """Narrow DI boundary (§II) for the `google.genai` client the live callers
    need: only its `.models` surface (`generate_content` / `list` /
    `embed_content`). The real `genai.Client` satisfies it structurally; unit
    tests inject a fake with a `.models` double instead of monkeypatching the SDK.
    Typed `Any` on purpose — `.models` is an external-SDK boundary, verified by
    integration, not re-typed here."""

    @property
    def models(self) -> Any: ...


@runtime_checkable
class Enricher(Protocol):
    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str: ...


class NullEnricher:
    """No-op — for tests and when GOOGLE_API_KEY is absent."""

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:  # noqa: ARG002
        # `item` is unused here but required by the Enricher Protocol signature.
        result: str = enrich_config.get("on_error", "")
        return result


# Gemini 2.5+ / 3.x run an internal reasoning phase that, left unbounded, spends
# the whole `max_output_tokens` on thoughts and returns `finish_reason=MAX_TOKENS`
# on valid prompts (#107). `thinking_budget=0` disables it. Older models (2.0)
# reject `thinking_config` with 400 INVALID_ARGUMENT, so gate it by version.
_THINKING_MIN_VERSION = 2.5


def _thinking_config(model_name: str) -> types.ThinkingConfig | None:
    """`ThinkingConfig(thinking_budget=0)` for models that support the reasoning
    phase (2.5+/3.x), else `None` — passing it to a 2.0 model 400s (#107)."""
    if _model_version_key(model_name)[0] >= _THINKING_MIN_VERSION:
        return types.ThinkingConfig(thinking_budget=0)
    return None


class GeminiEnricher:
    def __init__(self, model_name: str, client: GenaiClient) -> None:
        self._model_name = model_name
        self._client = client

    @property
    def model_name(self) -> str:
        """Public read-only view of the backing model name.

        Lets a coordinator (RotatingGeminiEnricher) name the model in logs
        without reaching into `_model_name` across the class boundary (§II).
        """
        return self._model_name

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        prompt_template = enrich_config["prompt"]
        params = enrich_config.get("parameters", {})
        on_error: str = enrich_config.get("on_error", "")
        response_pattern: str | None = enrich_config.get("response_pattern")

        context: dict[str, Any] = {
            "title": item.title,
            "url": item.url,
            "description": _sanitize_for_prompt(item.description),
            "metric": item.metric,
            "source_id": item.source_id,
            **item.raw,
        }
        prompt = string.Template(prompt_template).safe_substitute(context)

        config = types.GenerateContentConfig(
            temperature=params.get("temperature", 0.2),
            max_output_tokens=params.get("max_tokens", 150),
            thinking_config=_thinking_config(self._model_name),
        )

        try:
            text = self._generate(prompt, config)
        except TruncatedResponse as exc:
            logger.info(
                "[%s] enrichment truncated (%s) — asking rotator for next model",
                item.dedupe_key,
                exc,
            )
            raise TryNextModel from exc
        except Exception as exc:
            mapped = classify_generate_error(exc)
            if mapped is TryNextModel:
                logger.warning(
                    "[%s] enrichment failed: %s — trying next model", item.dedupe_key, exc
                )
            raise mapped from exc

        if response_pattern and not re.match(response_pattern, text):
            logger.warning(
                "[%s] enrichment format mismatch (first line: %r) — falling back to marker",
                item.dedupe_key,
                text.splitlines()[0] if text else "",
            )
            return on_error or FALLBACK_MARKER
        return text

    @retry(
        # Retry ONLY quota (429): the window can roll over. A 404 (dead model)
        # or any other error must NOT be retried — that just burns 3× backoff
        # before the rotator can advance (#107 BLOCKING-2).
        retry=retry_if_exception(lambda e: getattr(e, "code", None) == 429),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
    )
    def _generate(self, prompt: str, config: types.GenerateContentConfig) -> str:
        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=self._model_name, contents=prompt, config=config
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        text: str = (response.text or "").strip()
        finish_reason = _extract_finish_reason(response)
        first_line = text.splitlines()[0] if text else ""
        logger.info(
            "[%s] gen: prompt_len=%d resp_len=%d finish=%s first_line=%r",
            self._model_name,
            len(prompt),
            len(text),
            finish_reason,
            first_line,
        )
        truncated = finish_reason in ("MAX_TOKENS", "SAFETY")
        log_llm_call(
            logger,
            model=self._model_name,
            usage=extract_usage(response),
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            outcome="truncated" if truncated else "ok",
        )
        if truncated:
            raise TruncatedResponse(finish_reason)
        return _strip_markdown_wrap(text)


def _model_version_key(name: str) -> tuple[float, str]:
    """Extract version number for sorting: 'models/gemini-2.5-flash' → (2.5, 'flash')."""
    import re

    match = re.search(r"gemini-(\d+)\.(\d+)", name)
    if match:
        version = float(f"{match.group(1)}.{match.group(2)}")
        return (version, name)
    return (0.0, name)


_EXCLUDED_SUFFIXES = ("-tts", "-image", "-customtools", "-computer-use", "-robotics")

# Comma-separated model names to skip during rotation.
# Set via GitHub Actions variable GEMINI_EXCLUDED_MODELS, e.g.:
#   models/gemini-3.1-pro-preview,models/gemini-3-flash-preview
_EXCLUDED_MODELS: frozenset[str] = frozenset(
    m.strip() for m in os.getenv("GEMINI_EXCLUDED_MODELS", "").split(",") if m.strip()
)


def _is_text_gemini(name: str) -> bool:
    """Return True for pure text-generation Gemini models (ignores the excluded list)."""
    if not name.startswith("models/gemini-"):
        return False
    return not any(s in name for s in _EXCLUDED_SUFFIXES)


def get_generation_models(client: GenaiClient) -> list[str]:
    """Return text-generation Gemini model names, newer versions first.

    Models in GEMINI_EXCLUDED_MODELS are omitted from the result. The new SDK
    exposes capabilities via `Model.supported_actions` (was
    `supported_generation_methods` on the deprecated SDK, #107).
    """
    try:
        names: list[str] = []
        for m in client.models.list():
            name = m.name
            if (
                name is not None
                and "generateContent" in (m.supported_actions or [])
                and _is_text_gemini(name)
                and name not in _EXCLUDED_MODELS
            ):
                names.append(name)
    except Exception:  # noqa: BLE001 — list-models failure degrades to []; now visible via logger.exception (not silent)
        logger.exception("cannot list models")
        return []

    names.sort(key=_model_version_key, reverse=True)
    return names


class RotatingGeminiEnricher:
    """Tries multiple Gemini models before giving up on quota exhaustion."""

    _COOLDOWN = 60

    def __init__(self, model_names: list[str], client: GenaiClient) -> None:
        if not model_names:
            raise ValueError("model_names must not be empty")
        self._enrichers = [GeminiEnricher(n, client) for n in model_names]
        self._current = 0
        # Indices of models that returned `NotFound` this run. Skipped on
        # subsequent items until the cooldown window resets them (#128).
        self._dead: set[int] = set()

    def _advance_to_live(self) -> bool:
        """Move `_current` to the next non-dead index. Returns False when
        every model is dead."""
        if len(self._dead) >= len(self._enrichers):
            return False
        for _ in range(len(self._enrichers)):
            if self._current not in self._dead:
                return True
            self._current = (self._current + 1) % len(self._enrichers)
        return False

    def _handle_rotation_failure(self, exc: Exception) -> bool:
        """Classify a rotation failure, mark the model dead / advance the pointer,
        and log the next attempt. Returns whether the failure is *recoverable*
        (quota or model-unavailable) — i.e. whether a cooldown-retry is worth it;
        a pure TryNextModel truncation repeats identically after the wait."""
        prev_idx = self._current
        prev = self._enrichers[prev_idx].model_name
        if isinstance(exc, ModelUnavailable):
            self._dead.add(prev_idx)
            recoverable = True
            kind = "unavailable"
        elif isinstance(exc, QuotaExhausted):
            recoverable = True
            kind = "quota exhausted"
        else:
            recoverable = False
            kind = "try-next"
        self._current = (self._current + 1) % len(self._enrichers)
        # Skip over already-dead models when naming the next attempt,
        # so the log matches what the inner loop will actually try.
        preview = self._current
        for _ in range(len(self._enrichers)):
            if preview not in self._dead:
                break
            preview = (preview + 1) % len(self._enrichers)
        if preview in self._dead:
            logger.warning("model %s %s, no live models left", prev, kind)
        else:
            logger.warning(
                "model %s %s, trying %s",
                prev,
                kind,
                self._enrichers[preview].model_name,
            )
        return recoverable

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        last_exc: Exception | None = None
        # Cooldown only helps if some failure was Quota or Unavailable —
        # those can roll over / recover during the 60s wait. A pure
        # TryNextModel exhaustion (every model truncated this item) will
        # repeat identically after the sleep, so skip the cooldown then.
        saw_recoverable_failure = False
        for rotation in range(2):
            if rotation == 1:
                if not saw_recoverable_failure:
                    break
                logger.warning(
                    "all %d models exhausted, waiting %ds",
                    len(self._enrichers),
                    self._COOLDOWN,
                )
                time.sleep(self._COOLDOWN)
                self._current = 0
                self._dead.clear()

            if not self._advance_to_live():
                continue

            for _ in range(len(self._enrichers)):
                if self._current in self._dead:
                    self._current = (self._current + 1) % len(self._enrichers)
                    continue
                try:
                    return self._enrichers[self._current].enrich(item, enrich_config)
                except (QuotaExhausted, ModelUnavailable, TryNextModel) as exc:
                    if self._handle_rotation_failure(exc):
                        saw_recoverable_failure = True
                    last_exc = exc

        raise QuotaExhausted from last_exc


def build_default_enricher(api_key: str, log: logging.Logger) -> Enricher:
    """Construct the production Enricher from `GOOGLE_API_KEY`, logging a
    WARNING whenever we degrade to `NullEnricher` so the operator sees in
    cron logs that enrichment is off (see issue #93)."""
    if not api_key:
        log.warning(
            "GOOGLE_API_KEY is empty, enrichment disabled — notifications "
            "will lack enriched fields (e.g. summary_ru). Check workflow env-vars."
        )
        return NullEnricher()
    client = genai.Client(api_key=api_key)
    available_models = get_generation_models(client)
    log.info("available generation models: %s", available_models)
    if not available_models:
        log.warning("no generation models found, enrichment disabled")
        return NullEnricher()
    return RotatingGeminiEnricher(available_models, client)
