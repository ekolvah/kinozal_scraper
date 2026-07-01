"""Enricher Protocol через Gemini: rotation / quota / retry."""

from __future__ import annotations

import logging
import os
import re
import string
import time
from typing import Any, Protocol, runtime_checkable

import google.api_core.exceptions
import google.generativeai as genai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from kinozal_scraper.generic_pipeline import NormalizedItem

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

        generation_config = genai.types.GenerationConfig(
            temperature=params.get("temperature", 0.2),
            max_output_tokens=params.get("max_tokens", 150),
        )

        try:
            text = self._generate(prompt, generation_config)
        except TruncatedResponse as exc:
            logger.info(
                "[%s] enrichment truncated (%s) — asking rotator for next model",
                item.dedupe_key,
                exc,
            )
            raise TryNextModel from exc
        except Exception as exc:
            if isinstance(exc, google.api_core.exceptions.NotFound) or isinstance(
                exc.__cause__, google.api_core.exceptions.NotFound
            ):
                raise ModelUnavailable from exc
            if isinstance(exc.__cause__, google.api_core.exceptions.ResourceExhausted):
                raise QuotaExhausted from exc
            logger.warning("[%s] enrichment failed: %s — trying next model", item.dedupe_key, exc)
            raise TryNextModel from exc

        if response_pattern and not re.match(response_pattern, text):
            logger.warning(
                "[%s] enrichment format mismatch (first line: %r) — falling back to marker",
                item.dedupe_key,
                text.splitlines()[0] if text else "",
            )
            return on_error or FALLBACK_MARKER
        return text

    @retry(
        retry=retry_if_exception_type(google.api_core.exceptions.ResourceExhausted),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
    )
    def _generate(self, prompt: str, generation_config: genai.types.GenerationConfig) -> str:
        model = genai.GenerativeModel(self._model_name)
        response = model.generate_content(prompt, generation_config=generation_config)
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
        if finish_reason in ("MAX_TOKENS", "SAFETY"):
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


def get_generation_models() -> list[str]:
    """Return text-generation Gemini model names, newer versions first.

    Models in GEMINI_EXCLUDED_MODELS are omitted from the result.
    """
    try:
        names = [
            m.name
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
            and _is_text_gemini(m.name)
            and m.name not in _EXCLUDED_MODELS
        ]
    except Exception:  # noqa: BLE001 — list-models failure degrades to []; now visible via logger.exception (not silent)
        logger.exception("cannot list models")
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

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:  # noqa: C901, PLR0912
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
                    prev_idx = self._current
                    prev = self._enrichers[prev_idx]._model_name
                    if isinstance(exc, ModelUnavailable):
                        self._dead.add(prev_idx)
                        saw_recoverable_failure = True
                        kind = "unavailable"
                    elif isinstance(exc, QuotaExhausted):
                        saw_recoverable_failure = True
                        kind = "quota exhausted"
                    else:
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
                            self._enrichers[preview]._model_name,
                        )
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
    genai.configure(api_key=api_key)
    available_models = get_generation_models()
    log.info("available generation models: %s", available_models)
    if not available_models:
        log.warning("no generation models found, enrichment disabled")
        return NullEnricher()
    return RotatingGeminiEnricher(available_models)
