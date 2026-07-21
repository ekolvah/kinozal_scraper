from __future__ import annotations

import unittest
import unittest.mock
from types import SimpleNamespace
from typing import Any

from google.genai import errors, types

from kinozal_scraper.gemini_enricher import (
    FALLBACK_MARKER,
    Enricher,
    GeminiEnricher,
    ModelUnavailable,
    NullEnricher,
    QuotaExhausted,
    RotatingGeminiEnricher,
    TruncatedResponse,
    TryNextModel,
    build_default_enricher,
    classify_generate_error,
)
from kinozal_scraper.generic_pipeline import NormalizedItem

# ── Test doubles for the new google.genai client (§II external boundary) ─────────


class _FakeCandidate:
    def __init__(self, finish_reason: str) -> None:
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text: str, finish_reason: str = "STOP", usage_metadata: Any = None) -> None:
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)]
        self.usage_metadata = usage_metadata


class _FakeModels:
    """Stand-in for `client.models`: records generate_content kwargs, returns a
    canned response or raises a canned error."""

    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> _FakeResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeClient:
    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self.models = _FakeModels(response, error)


def _api_error(code: int) -> errors.ClientError:
    """Build a google.genai APIError double carrying only `.code` — the field the
    new-SDK error taxonomy discriminates on (avoids the real constructor's
    response_json parsing)."""
    e = errors.ClientError.__new__(errors.ClientError)
    e.code = code
    e.status = "RESOURCE_EXHAUSTED" if code == 429 else "NOT_FOUND"
    e.message = str(code)
    return e


def _enricher(
    response: _FakeResponse | None = None,
    error: Exception | None = None,
    model: str = "test-model",
) -> GeminiEnricher:
    return GeminiEnricher(model, _FakeClient(response=response, error=error))


def _item(key: str = "x") -> NormalizedItem:
    return NormalizedItem(dedupe_key=key, title=key, source_id="s", description="d", raw={})


_ENRICH_CFG: dict[str, Any] = {
    "field": "summary",
    "prompt": "Describe $title",
    "on_error": "fallback",
}


class TestNullEnricher(unittest.TestCase):
    def test_implements_enricher_protocol(self) -> None:
        self.assertIsInstance(NullEnricher(), Enricher)

    def test_returns_on_error_value(self) -> None:
        result = NullEnricher().enrich(_item(), _ENRICH_CFG)
        self.assertEqual(result, "fallback")

    def test_returns_empty_when_on_error_missing(self) -> None:
        cfg: dict[str, Any] = {"field": "summary", "prompt": "..."}
        self.assertEqual(NullEnricher().enrich(_item(), cfg), "")


class TestPromptTemplateSubstitution(unittest.TestCase):
    def test_safe_substitute_handles_braces_in_description(self) -> None:
        import string

        template = "Describe: $title — $description"
        context = {"title": "cool-lib", "description": 'A {json: "value"} parser'}
        result = string.Template(template).safe_substitute(context)
        self.assertIn("cool-lib", result)
        self.assertIn('{json: "value"}', result)

    def test_safe_substitute_missing_key_not_crash(self) -> None:
        import string

        template = "Name: $title, Lang: $language"
        context = {"title": "repo"}
        result = string.Template(template).safe_substitute(context)
        self.assertIn("repo", result)
        self.assertIn("$language", result)


_TWO_LINE_PATTERN = r"^Для кого:\s*.+\nЗачем:\s*.+"

_TWO_LINE_CFG: dict[str, Any] = {
    "field": "summary_ru",
    "prompt": "Inst. Title: $title\nDesc: $description",
    "response_pattern": _TWO_LINE_PATTERN,
    "parameters": {"temperature": 0.2, "max_tokens": 220},
    "on_error": "",
}


class TestGenerateFinishReason(unittest.TestCase):
    """`_generate` must surface MAX_TOKENS / SAFETY truncation as
    `TruncatedResponse`, not silently return a half-finished string that
    pollutes the Telegram notification (review of broken 19.05.2026 batch)."""

    def test_max_tokens_raises_truncated_response(self) -> None:
        enricher = _enricher(_FakeResponse(text="Для кого: разработчи", finish_reason="MAX_TOKENS"))
        with self.assertRaises(TruncatedResponse):
            enricher._generate("p", types.GenerateContentConfig())

    def test_safety_raises_truncated_response(self) -> None:
        enricher = _enricher(_FakeResponse(text="…", finish_reason="SAFETY"))
        with self.assertRaises(TruncatedResponse):
            enricher._generate("p", types.GenerateContentConfig())

    def test_stop_returns_stripped_text(self) -> None:
        enricher = _enricher(
            _FakeResponse(text="  Для кого: X\nЗачем: Y\n  ", finish_reason="STOP")
        )
        result = enricher._generate("p", types.GenerateContentConfig())
        self.assertEqual(result, "Для кого: X\nЗачем: Y")


class TestEnrichTruncationRotates(unittest.TestCase):
    """After #130, MAX_TOKENS / SAFETY truncation is no longer terminal — the
    enricher asks the rotator to try the next model instead of returning the
    visible marker on first attempt. Marker shows up only when every live
    model failed on this specific item (rotator → QuotaExhausted → pipeline)."""

    def test_max_tokens_raises_try_next_model(self) -> None:
        enricher = _enricher(_FakeResponse(text="Для кого: разработчи", finish_reason="MAX_TOKENS"))
        with self.assertRaises(TryNextModel):
            enricher.enrich(_item(), _TWO_LINE_CFG)

    def test_safety_raises_try_next_model_even_with_custom_on_error(self) -> None:
        """`on_error` is the final shape of the field if rotation exhausts —
        truncation itself must still rotate first."""
        cfg = {**_TWO_LINE_CFG, "on_error": "custom-fallback"}
        enricher = _enricher(_FakeResponse(text="…", finish_reason="SAFETY"))
        with self.assertRaises(TryNextModel):
            enricher.enrich(_item(), cfg)


class TestEnrichFormatValidation(unittest.TestCase):
    """When `enrich.response_pattern` is set, the answer must match — otherwise
    the malformed text is replaced with a visible-anomaly marker so the user
    sees a tripwire in Telegram instead of garbage (Constitution Principle IV)."""

    def test_echo_prompt_returns_fallback_marker(self) -> None:
        # Real-world bad output captured 19.05.2026: model echoed the instruction.
        enricher = _enricher(_FakeResponse(text="строка 1:\nстрока 2:", finish_reason="STOP"))
        result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, FALLBACK_MARKER)

    def test_markdown_wrap_around_valid_text_is_stripped_and_accepted(self) -> None:
        # Some models like to wrap structured output in fenced code blocks.
        wrapped = "```\nДля кого: разработчиков\nЗачем: ускорить сборку\n```"
        enricher = _enricher(_FakeResponse(text=wrapped, finish_reason="STOP"))
        result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, "Для кого: разработчиков\nЗачем: ускорить сборку")

    def test_valid_two_line_passes_through(self) -> None:
        enricher = _enricher(
            _FakeResponse(
                text="Для кого: разработчиков\nЗачем: ускорить сборку", finish_reason="STOP"
            )
        )
        result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, "Для кого: разработчиков\nЗачем: ускорить сборку")

    def test_no_response_pattern_skips_validation(self) -> None:
        """Configs without `response_pattern` (e.g. free-form prompts) must
        not enforce the two-line shape — validation is opt-in per source."""
        cfg = {**_TWO_LINE_CFG}
        del cfg["response_pattern"]
        enricher = _enricher(_FakeResponse(text="anything goes here", finish_reason="STOP"))
        result = enricher.enrich(_item(), cfg)
        self.assertEqual(result, "anything goes here")


class TestPromptSanitization(unittest.TestCase):
    """The `$description` substituted into the prompt comes from raw <p> tags
    of trending HTML and frequently contains markdown noise (` ``` ` fences,
    leading `*`/`#`). That noise was correlated with echo / markdown leaks in
    the 19.05.2026 batch. Sanitize before substitution; do NOT mutate the
    item itself."""

    def test_description_markdown_stripped_before_substitution(self) -> None:
        item = NormalizedItem(
            dedupe_key="x",
            title="proj",
            source_id="s",
            description="```js\nconst x = 1;\n```\n* feature one\n# heading",
            raw={},
        )
        client = _FakeClient(_FakeResponse(text="Для кого: X\nЗачем: Y", finish_reason="STOP"))
        GeminiEnricher("test-model", client).enrich(item, _TWO_LINE_CFG)
        sent = client.models.calls[-1]["contents"]
        # description was the raw markdown blob — it should NOT appear verbatim
        # in the prompt sent to the model.
        self.assertNotIn("```", sent)
        self.assertNotIn("# heading", sent)
        # item itself was untouched.
        self.assertIn("```", item.description)

    def test_description_truncated_to_400_chars(self) -> None:
        long_desc = "a" * 1000
        item = NormalizedItem(
            dedupe_key="x", title="proj", source_id="s", description=long_desc, raw={}
        )
        client = _FakeClient(_FakeResponse(text="Для кого: X\nЗачем: Y", finish_reason="STOP"))
        GeminiEnricher("test-model", client).enrich(item, _TWO_LINE_CFG)
        # Bound: sanitized text + ellipsis. Allow some slack for word-boundary
        # truncation but ensure we're well below the original 1000.
        substituted_desc_len = client.models.calls[-1]["contents"].count("a")
        self.assertLess(substituted_desc_len, 500)


class TestGeminiEnricherQuota(unittest.TestCase):
    def test_resource_exhausted_raises_quota_exhausted(self) -> None:
        from tenacity import RetryError

        enricher = _enricher()
        retry_err = RetryError(last_attempt=unittest.mock.MagicMock())
        retry_err.__cause__ = _api_error(429)

        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=retry_err),
            self.assertRaises(QuotaExhausted),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)

    def test_non_quota_error_raises_try_next_model(self) -> None:
        """After #130, any unexpected exception (network timeout,
        InvalidArgument, …) asks the rotator to try the next model rather
        than silently returning `on_error` for this single item."""
        enricher = _enricher()
        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=RuntimeError("net")),
            self.assertRaises(TryNextModel),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)


class TestErrorClassification(unittest.TestCase):
    """New google.genai errors are one class (`APIError`) discriminated by
    `.code`, not distinct `google.api_core` types. `classify_generate_error`
    must route 429→quota, 404→unavailable, everything else→try-next, and
    unwrap a tenacity `RetryError.__cause__` (#107)."""

    def test_code_429_is_quota(self) -> None:
        self.assertIs(classify_generate_error(_api_error(429)), QuotaExhausted)

    def test_code_404_is_model_unavailable(self) -> None:
        self.assertIs(classify_generate_error(_api_error(404)), ModelUnavailable)

    def test_other_exception_is_try_next_model(self) -> None:
        self.assertIs(classify_generate_error(RuntimeError("net down")), TryNextModel)

    def test_unwraps_cause_code(self) -> None:
        wrapper = RuntimeError("wrapped")
        wrapper.__cause__ = _api_error(404)
        self.assertIs(classify_generate_error(wrapper), ModelUnavailable)


class TestThinkingConfigGate(unittest.TestCase):
    """#107: `thinking_budget=0` disables the reasoning phase that made Gemini
    3.x burn the whole `max_output_tokens` on thoughts. It must be set only for
    models that support it (2.5+/3.x); older models (2.0) must NOT receive a
    `thinking_config` (they 400 on it → false QuotaExhausted, §IV)."""

    def test_thinking_budget_zero_for_gemini_2_5(self) -> None:
        client = _FakeClient(_FakeResponse(text="Для кого: X\nЗачем: Y", finish_reason="STOP"))
        GeminiEnricher("models/gemini-2.5-flash", client).enrich(_item(), _TWO_LINE_CFG)
        cfg = client.models.calls[-1]["config"]
        self.assertIsNotNone(cfg.thinking_config)
        self.assertEqual(cfg.thinking_config.thinking_budget, 0)

    def test_thinking_budget_zero_for_gemini_3_x(self) -> None:
        client = _FakeClient(_FakeResponse(text="Для кого: X\nЗачем: Y", finish_reason="STOP"))
        GeminiEnricher("models/gemini-3.1-flash-lite-preview", client).enrich(
            _item(), _TWO_LINE_CFG
        )
        cfg = client.models.calls[-1]["config"]
        self.assertIsNotNone(cfg.thinking_config)
        self.assertEqual(cfg.thinking_config.thinking_budget, 0)

    def test_no_thinking_config_for_gemini_2_0(self) -> None:
        client = _FakeClient(_FakeResponse(text="Для кого: X\nЗачем: Y", finish_reason="STOP"))
        GeminiEnricher("models/gemini-2.0-flash", client).enrich(_item(), _TWO_LINE_CFG)
        cfg = client.models.calls[-1]["config"]
        self.assertIsNone(cfg.thinking_config)


class TestRetryOnlyOnQuota(unittest.TestCase):
    """#107 BLOCKING-2: with one error class, tenacity must retry ONLY 429
    (quota can roll over), never 404 (dead model — retrying wastes 3× backoff
    before the rotator can advance)."""

    @unittest.mock.patch("time.sleep")
    def test_quota_429_is_retried_three_times(self, _sleep: Any) -> None:
        enricher = _enricher(error=_api_error(429))
        with self.assertRaises(QuotaExhausted):
            enricher.enrich(_item(), _ENRICH_CFG)
        # tenacity stop_after_attempt(3) → generate_content called 3×.
        self.assertEqual(len(enricher._client.models.calls), 3)  # type: ignore[attr-defined]

    @unittest.mock.patch("time.sleep")
    def test_not_found_404_is_not_retried(self, _sleep: Any) -> None:
        enricher = _enricher(error=_api_error(404))
        with self.assertRaises(ModelUnavailable):
            enricher.enrich(_item(), _ENRICH_CFG)
        # 404 is not retried → generate_content called exactly once.
        self.assertEqual(len(enricher._client.models.calls), 1)  # type: ignore[attr-defined]


class TestModelVersionSorting(unittest.TestCase):
    def test_newer_models_first(self) -> None:
        from kinozal_scraper.gemini_enricher import _model_version_key

        names = [
            "models/gemini-1.0-pro",
            "models/gemini-2.5-flash-preview",
            "models/gemini-1.5-flash",
            "models/gemini-2.0-flash",
        ]
        result = sorted(names, key=_model_version_key, reverse=True)
        self.assertEqual(
            result,
            [
                "models/gemini-2.5-flash-preview",
                "models/gemini-2.0-flash",
                "models/gemini-1.5-flash",
                "models/gemini-1.0-pro",
            ],
        )

    def test_unknown_format_gets_zero_version(self) -> None:
        from kinozal_scraper.gemini_enricher import _model_version_key

        self.assertEqual(_model_version_key("models/chat-bison-001")[0], 0.0)


class TestIsTextGemini(unittest.TestCase):
    def test_accepts_text_models(self) -> None:
        from kinozal_scraper.gemini_enricher import _is_text_gemini

        self.assertTrue(_is_text_gemini("models/gemini-2.5-flash"))
        self.assertTrue(_is_text_gemini("models/gemini-2.0-flash-lite"))
        self.assertTrue(_is_text_gemini("models/gemini-3.1-pro-preview"))
        self.assertTrue(_is_text_gemini("models/gemini-2.5-flash-lite"))

    def test_rejects_specialized_models(self) -> None:
        from kinozal_scraper.gemini_enricher import _is_text_gemini

        self.assertFalse(_is_text_gemini("models/gemini-3.1-flash-tts-preview"))
        self.assertFalse(_is_text_gemini("models/gemini-3.1-flash-image-preview"))
        self.assertFalse(_is_text_gemini("models/gemini-3.1-pro-preview-customtools"))
        self.assertFalse(_is_text_gemini("models/gemini-2.5-computer-use-preview-10-2025"))
        self.assertFalse(_is_text_gemini("models/gemini-robotics-er-1.6-preview"))

    def test_rejects_non_gemini(self) -> None:
        from kinozal_scraper.gemini_enricher import _is_text_gemini

        self.assertFalse(_is_text_gemini("models/gemma-3-27b-it"))
        self.assertFalse(_is_text_gemini("models/lyria-3-pro-preview"))
        self.assertFalse(_is_text_gemini("models/nano-banana-pro-preview"))


class TestGetGenerationModels(unittest.TestCase):
    """`get_generation_models(client)` filters `client.models.list()` by the new
    SDK `supported_actions` field (was `supported_generation_methods`), keeps
    text Gemini models, newest first (#107)."""

    def test_filters_and_sorts_by_supported_actions(self) -> None:
        from kinozal_scraper.gemini_enricher import get_generation_models

        client = unittest.mock.MagicMock()
        client.models.list.return_value = [
            SimpleNamespace(name="models/gemini-2.0-flash", supported_actions=["generateContent"]),
            SimpleNamespace(name="models/gemini-2.5-flash", supported_actions=["generateContent"]),
            SimpleNamespace(name="models/text-embedding-004", supported_actions=["embedContent"]),
            SimpleNamespace(name="models/gemma-3-27b-it", supported_actions=["generateContent"]),
        ]
        result = get_generation_models(client)
        self.assertEqual(result, ["models/gemini-2.5-flash", "models/gemini-2.0-flash"])

    def test_list_failure_degrades_to_empty_and_logs(self) -> None:
        from kinozal_scraper.gemini_enricher import get_generation_models

        client = unittest.mock.MagicMock()
        client.models.list.side_effect = RuntimeError("network")
        with self.assertLogs("kinozal_scraper.gemini_enricher", level="ERROR"):
            self.assertEqual(get_generation_models(client), [])


class TestRotatingGeminiEnricher(unittest.TestCase):
    def _rotator(self, model_names: list[str]) -> RotatingGeminiEnricher:
        return RotatingGeminiEnricher(model_names, _FakeClient())

    def test_implements_enricher_protocol(self) -> None:
        self.assertIsInstance(self._rotator(["m1"]), Enricher)

    def test_empty_model_list_raises(self) -> None:
        with self.assertRaises(ValueError):
            RotatingGeminiEnricher([], _FakeClient())

    def test_rotates_to_next_model_on_quota(self) -> None:
        enricher = self._rotator(["model-a", "model-b"])

        def side_effect(item: Any, cfg: Any) -> str:
            raise QuotaExhausted

        enricher._enrichers[0].enrich = side_effect  # type: ignore[assignment]
        enricher._enrichers[1].enrich = lambda item, cfg: "from-b"  # type: ignore[assignment]

        result = enricher.enrich(_item(), _ENRICH_CFG)
        self.assertEqual(result, "from-b")
        self.assertEqual(enricher._current, 1)

    def test_remembers_working_model_for_next_call(self) -> None:
        enricher = self._rotator(["model-a", "model-b"])

        call_log: list[str] = []

        def fail_a(item: Any, cfg: Any) -> str:
            raise QuotaExhausted

        def ok_b(item: Any, cfg: Any) -> str:
            call_log.append("b")
            return "ok"

        enricher._enrichers[0].enrich = fail_a  # type: ignore[assignment]
        enricher._enrichers[1].enrich = ok_b  # type: ignore[assignment]

        enricher.enrich(_item("1"), _ENRICH_CFG)
        enricher.enrich(_item("2"), _ENRICH_CFG)
        self.assertEqual(call_log, ["b", "b"])

    @unittest.mock.patch("kinozal_scraper.gemini_enricher.time.sleep")
    def test_all_models_exhausted_sleeps_and_retries(self, mock_sleep: Any) -> None:
        call_count = 0

        def enrich_fn(item: Any, cfg: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise QuotaExhausted
            return "recovered"

        enricher = self._rotator(["m1", "m2"])
        enricher._enrichers[0].enrich = enrich_fn  # type: ignore[assignment]
        enricher._enrichers[1].enrich = enrich_fn  # type: ignore[assignment]

        result = enricher.enrich(_item(), _ENRICH_CFG)
        self.assertEqual(result, "recovered")
        mock_sleep.assert_called_once_with(60)

    @unittest.mock.patch("kinozal_scraper.gemini_enricher.time.sleep")
    def test_all_models_exhausted_twice_raises(self, mock_sleep: Any) -> None:
        def always_fail(item: Any, cfg: Any) -> str:
            raise QuotaExhausted

        enricher = self._rotator(["m1", "m2"])
        enricher._enrichers[0].enrich = always_fail  # type: ignore[assignment]
        enricher._enrichers[1].enrich = always_fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            enricher.enrich(_item(), _ENRICH_CFG)
        mock_sleep.assert_called_once_with(60)


class TestGeminiEnricherModelUnavailable(unittest.TestCase):
    """Pin-tests for #128: ListModels returns Gemini models still in
    deprecation that 404 at GenerateContent. Treat the per-model `NotFound`
    as a signal to switch models — like quota (429) does — instead of silently
    returning `on_error` for every item.
    """

    def test_not_found_raises_model_unavailable(self) -> None:
        enricher = _enricher(model="models/gemini-3.1-flash-lite-preview")
        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=_api_error(404)),
            self.assertRaises(ModelUnavailable),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)

    def test_not_found_wrapped_in_retry_error_raises_model_unavailable(self) -> None:
        """Defensive: if tenacity is ever broadened to retry 404, the outer
        `RetryError` carries the original error as `__cause__` — the same shape
        `QuotaExhausted` detection handles today."""
        from tenacity import RetryError

        enricher = _enricher(model="models/gemini-3.1-flash-lite-preview")
        retry_err = RetryError(last_attempt=unittest.mock.MagicMock())
        retry_err.__cause__ = _api_error(404)
        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=retry_err),
            self.assertRaises(ModelUnavailable),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)


class TestRotatingGeminiEnricherModelUnavailable(unittest.TestCase):
    def _rotator(self, model_names: list[str]) -> RotatingGeminiEnricher:
        return RotatingGeminiEnricher(model_names, _FakeClient())

    def test_dead_model_skipped_on_subsequent_items(self) -> None:
        """A 404'd model should be tried at most once per run. Today's bug
        (#128): all 8 trending items hit the same dead model before rotator
        even noticed, because `NotFound` was swallowed as `on_error`."""
        rotator = self._rotator(["model-a", "model-b"])
        a_calls = 0
        b_calls = 0

        def fail_a(item: Any, cfg: Any) -> str:
            nonlocal a_calls
            a_calls += 1
            raise ModelUnavailable

        def ok_b(item: Any, cfg: Any) -> str:
            nonlocal b_calls
            b_calls += 1
            return "from-b"

        rotator._enrichers[0].enrich = fail_a  # type: ignore[assignment]
        rotator._enrichers[1].enrich = ok_b  # type: ignore[assignment]

        with self.assertLogs("kinozal_scraper.gemini_enricher", level="WARNING") as captured:
            for i in range(5):
                self.assertEqual(rotator.enrich(_item(str(i)), _ENRICH_CFG), "from-b")

        self.assertEqual(a_calls, 1, "dead model must not be retried for every item")
        self.assertEqual(b_calls, 5)
        rotation_log = "\n".join(captured.output)
        self.assertIn("model-a", rotation_log)
        self.assertIn("model-b", rotation_log)

    @unittest.mock.patch("kinozal_scraper.gemini_enricher.time.sleep")
    def test_all_models_unavailable_raises_quota_exhausted(self, mock_sleep: Any) -> None:
        """When every rotated model 404s, surface a single quota-style
        exception so the pipeline switches to the visible fallback marker.
        """

        def fail(item: Any, cfg: Any) -> str:
            raise ModelUnavailable

        rotator = self._rotator(["m1", "m2"])
        rotator._enrichers[0].enrich = fail  # type: ignore[assignment]
        rotator._enrichers[1].enrich = fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            rotator.enrich(_item(), _ENRICH_CFG)


class TestRotateOnTryNextModel(unittest.TestCase):
    """#130: per-item failures (truncation, network) signal `TryNextModel`
    so the rotator can give the item another chance on a different model.
    Bad-prompt failures (response_pattern mismatch) stay terminal — burning
    14 models on a broken prompt is wasted quota."""

    def test_pattern_mismatch_still_returns_marker_no_rotation(self) -> None:
        """Response that doesn't match `response_pattern` is a prompt-level
        issue, not a model issue — keep the immediate marker return so the
        rotator does not retry across all live models."""
        enricher = _enricher(_FakeResponse(text="строка 1:\nстрока 2:", finish_reason="STOP"))
        result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, FALLBACK_MARKER)


class TestRotatingGeminiEnricherTryNext(unittest.TestCase):
    def _rotator(self, model_names: list[str]) -> RotatingGeminiEnricher:
        return RotatingGeminiEnricher(model_names, _FakeClient())

    def test_rotator_retries_on_next_model_after_truncated(self) -> None:
        rotator = self._rotator(["model-a", "model-b"])

        def fail_a(item: Any, cfg: Any) -> str:
            raise TryNextModel

        def ok_b(item: Any, cfg: Any) -> str:
            return "text-b"

        rotator._enrichers[0].enrich = fail_a  # type: ignore[assignment]
        rotator._enrichers[1].enrich = ok_b  # type: ignore[assignment]

        self.assertEqual(rotator.enrich(_item(), _ENRICH_CFG), "text-b")

    def test_rotator_truncated_does_not_mark_dead(self) -> None:
        """A model that truncated one item may handle the next one fine —
        `_dead` is reserved for `ModelUnavailable` (#128). On item 2 the
        rotator must be allowed to call model-A again."""
        rotator = self._rotator(["model-a", "model-b"])
        a_calls = 0

        def fail_a(item: Any, cfg: Any) -> str:
            nonlocal a_calls
            a_calls += 1
            raise TryNextModel

        def ok_b(item: Any, cfg: Any) -> str:
            return "text-b"

        rotator._enrichers[0].enrich = fail_a  # type: ignore[assignment]
        rotator._enrichers[1].enrich = ok_b  # type: ignore[assignment]

        rotator.enrich(_item("1"), _ENRICH_CFG)
        rotator._current = 0
        rotator.enrich(_item("2"), _ENRICH_CFG)

        self.assertEqual(a_calls, 2, "model-a must remain live after TryNextModel")
        self.assertNotIn(0, rotator._dead)

    @unittest.mock.patch("kinozal_scraper.gemini_enricher.time.sleep")
    def test_all_models_truncated_raises_quota_exhausted(self, mock_sleep: Any) -> None:
        """When every model fails on the same item — even if every failure
        is `TryNextModel` rather than 429 — the rotator surfaces a single
        `QuotaExhausted` so the pipeline substitutes `FALLBACK_MARKER`."""

        def fail(item: Any, cfg: Any) -> str:
            raise TryNextModel

        rotator = self._rotator(["m1", "m2"])
        rotator._enrichers[0].enrich = fail  # type: ignore[assignment]
        rotator._enrichers[1].enrich = fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            rotator.enrich(_item(), _ENRICH_CFG)

    @unittest.mock.patch("kinozal_scraper.gemini_enricher.time.sleep")
    def test_all_try_next_skips_cooldown(self, mock_sleep: Any) -> None:
        """Per claude-review #131: if every model raised only `TryNextModel`
        (no quota / no unavailable), the 60s cooldown buys nothing — the same
        item × same models will truncate identically after the wait. Skip it.
        """

        def fail(item: Any, cfg: Any) -> str:
            raise TryNextModel

        rotator = self._rotator(["m1", "m2"])
        rotator._enrichers[0].enrich = fail  # type: ignore[assignment]
        rotator._enrichers[1].enrich = fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            rotator.enrich(_item(), _ENRICH_CFG)
        mock_sleep.assert_not_called()

    @unittest.mock.patch("kinozal_scraper.gemini_enricher.time.sleep")
    def test_quota_in_first_rotation_still_uses_cooldown(self, mock_sleep: Any) -> None:
        """Cooldown stays for the original use case: if any model hit quota
        (or 404) in the first rotation, a 60s wait can let the window roll
        over before the second pass."""
        call_count = 0

        def enrich_fn(item: Any, cfg: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise QuotaExhausted
            return "recovered"

        rotator = self._rotator(["m1", "m2"])
        rotator._enrichers[0].enrich = enrich_fn  # type: ignore[assignment]
        rotator._enrichers[1].enrich = enrich_fn  # type: ignore[assignment]

        self.assertEqual(rotator.enrich(_item(), _ENRICH_CFG), "recovered")
        mock_sleep.assert_called_once_with(60)


class TestBuildDefaultEnricher(unittest.TestCase):
    """Pin-test for issue #93: silent degradation when GOOGLE_API_KEY is absent.

    We WARN whenever the production helper falls back to NullEnricher so the
    operator sees in cron logs that enrichment is off. With the new SDK the
    helper builds an explicit `genai.Client` (variant B, §II) instead of the
    global `genai.configure()`.
    """

    def test_empty_api_key_returns_null_enricher_and_warns(self) -> None:
        import logging

        log = logging.getLogger("test_build_default_enricher.empty")
        with self.assertLogs(log, level="WARNING") as captured:
            result = build_default_enricher("", log)
        self.assertIsInstance(result, NullEnricher)
        joined = "\n".join(captured.output)
        self.assertIn("GOOGLE_API_KEY is empty", joined)
        self.assertIn("summary_ru", joined)

    def test_api_key_with_no_models_returns_null_enricher_and_warns(self) -> None:
        import logging

        log = logging.getLogger("test_build_default_enricher.no_models")
        with (
            unittest.mock.patch("kinozal_scraper.gemini_enricher.genai.Client"),
            unittest.mock.patch(
                "kinozal_scraper.gemini_enricher.get_generation_models", return_value=[]
            ),
            self.assertLogs(log, level="WARNING") as captured,
        ):
            result = build_default_enricher("real-key", log)
        self.assertIsInstance(result, NullEnricher)
        self.assertIn("no generation models found", "\n".join(captured.output))

    def test_api_key_with_models_returns_rotating_enricher(self) -> None:
        import logging

        log = logging.getLogger("test_build_default_enricher.ok")
        with (
            unittest.mock.patch("kinozal_scraper.gemini_enricher.genai.Client") as mock_client,
            unittest.mock.patch(
                "kinozal_scraper.gemini_enricher.get_generation_models",
                return_value=["models/gemini-2.5-flash", "models/gemini-2.0-flash"],
            ),
        ):
            result = build_default_enricher("real-key", log)
        self.assertIsInstance(result, RotatingGeminiEnricher)
        mock_client.assert_called_once_with(api_key="real-key")


class TestObservability(unittest.TestCase):
    """A live Gemini call must emit a structured `llm_call` breadcrumb with token
    usage (`usage_metadata`) and wall-clock latency, so cron logs show per-call
    token spend instead of just prompt/response lengths (#145)."""

    def test_generate_logs_token_usage_and_latency(self) -> None:
        response = _FakeResponse(
            text="hello",
            finish_reason="STOP",
            usage_metadata=SimpleNamespace(
                prompt_token_count=320, candidates_token_count=48, total_token_count=368
            ),
        )
        enricher = _enricher(response, model="models/gemini-2.5-flash")
        with self.assertLogs("kinozal_scraper.gemini_enricher", level="INFO") as cm:
            enricher.enrich(_item(), _ENRICH_CFG)
        line = "\n".join(cm.output)
        self.assertIn("llm_call", line)
        self.assertIn("prompt_tokens=320", line)
        self.assertIn("total_tokens=368", line)
        self.assertIn("latency_ms=", line)

    def test_generate_logs_truncated_outcome_on_max_tokens(self) -> None:
        # A truncated call is still observed (breadcrumb fires before the raise),
        # tagged outcome=truncated — the log must not go silent on the calls that
        # burned tokens without a usable answer (§IV).
        response = _FakeResponse(
            text="partial",
            finish_reason="MAX_TOKENS",
            usage_metadata=SimpleNamespace(
                prompt_token_count=10, candidates_token_count=150, total_token_count=160
            ),
        )
        enricher = _enricher(response, model="models/gemini-2.5-flash")
        with (
            self.assertLogs("kinozal_scraper.gemini_enricher", level="INFO") as cm,
            self.assertRaises(TruncatedResponse),
        ):
            enricher._generate("p", types.GenerateContentConfig())
        self.assertIn("outcome=truncated", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
