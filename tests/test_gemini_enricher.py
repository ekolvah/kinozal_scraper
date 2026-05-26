from __future__ import annotations

import logging
import unittest
import unittest.mock
from typing import Any

from gemini_enricher import (
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
)
from generic_pipeline import NormalizedItem


class _FakeCandidate:
    def __init__(self, finish_reason: str) -> None:
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text: str, finish_reason: str = "STOP") -> None:
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)]


class _FakeGenerativeModel:
    """Stand-in for `genai.GenerativeModel` capturing the prompt and returning a canned response."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_prompt: str = ""

    def generate_content(self, prompt: str, generation_config: Any = None) -> _FakeResponse:  # noqa: ARG002
        self.last_prompt = prompt
        return self._response


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
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="Для кого: разработчи", finish_reason="MAX_TOKENS")
        with (
            unittest.mock.patch(
                "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
            ),
            self.assertRaises(TruncatedResponse),
        ):
            enricher._generate(
                "p",
                __import__("google.generativeai", fromlist=["types"]).types.GenerationConfig(),
            )

    def test_safety_raises_truncated_response(self) -> None:
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="…", finish_reason="SAFETY")
        with (
            unittest.mock.patch(
                "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
            ),
            self.assertRaises(TruncatedResponse),
        ):
            enricher._generate(
                "p",
                __import__("google.generativeai", fromlist=["types"]).types.GenerationConfig(),
            )

    def test_stop_returns_stripped_text(self) -> None:
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="  Для кого: X\nЗачем: Y\n  ", finish_reason="STOP")
        with unittest.mock.patch(
            "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
        ):
            result = enricher._generate(
                "p",
                __import__("google.generativeai", fromlist=["types"]).types.GenerationConfig(),
            )
        self.assertEqual(result, "Для кого: X\nЗачем: Y")


class TestEnrichTruncationRotates(unittest.TestCase):
    """After #130, MAX_TOKENS / SAFETY truncation is no longer terminal — the
    enricher asks the rotator to try the next model instead of returning the
    visible marker on first attempt. Marker shows up only when every live
    model failed on this specific item (rotator → QuotaExhausted → pipeline)."""

    def test_max_tokens_raises_try_next_model(self) -> None:
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="Для кого: разработчи", finish_reason="MAX_TOKENS")
        with (
            unittest.mock.patch(
                "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
            ),
            self.assertRaises(TryNextModel),
        ):
            enricher.enrich(_item(), _TWO_LINE_CFG)

    def test_safety_raises_try_next_model_even_with_custom_on_error(self) -> None:
        """`on_error` is the final shape of the field if rotation exhausts —
        truncation itself must still rotate first."""
        cfg = {**_TWO_LINE_CFG, "on_error": "custom-fallback"}
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="…", finish_reason="SAFETY")
        with (
            unittest.mock.patch(
                "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
            ),
            self.assertRaises(TryNextModel),
        ):
            enricher.enrich(_item(), cfg)


class TestEnrichFormatValidation(unittest.TestCase):
    """When `enrich.response_pattern` is set, the answer must match — otherwise
    the malformed text is replaced with a visible-anomaly marker so the user
    sees a tripwire in Telegram instead of garbage (Constitution Principle IV)."""

    def test_echo_prompt_returns_fallback_marker(self) -> None:
        enricher = GeminiEnricher("test-model")
        # Real-world bad output captured 19.05.2026: model echoed the instruction.
        response = _FakeResponse(text="строка 1:\nстрока 2:", finish_reason="STOP")
        with unittest.mock.patch(
            "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
        ):
            result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, FALLBACK_MARKER)

    def test_markdown_wrap_around_valid_text_is_stripped_and_accepted(self) -> None:
        enricher = GeminiEnricher("test-model")
        # Some models like to wrap structured output in fenced code blocks.
        wrapped = "```\nДля кого: разработчиков\nЗачем: ускорить сборку\n```"
        response = _FakeResponse(text=wrapped, finish_reason="STOP")
        with unittest.mock.patch(
            "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
        ):
            result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, "Для кого: разработчиков\nЗачем: ускорить сборку")

    def test_valid_two_line_passes_through(self) -> None:
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(
            text="Для кого: разработчиков\nЗачем: ускорить сборку", finish_reason="STOP"
        )
        with unittest.mock.patch(
            "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
        ):
            result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, "Для кого: разработчиков\nЗачем: ускорить сборку")

    def test_no_response_pattern_skips_validation(self) -> None:
        """Configs without `response_pattern` (e.g. free-form prompts) must
        not enforce the two-line shape — validation is opt-in per source."""
        cfg = {**_TWO_LINE_CFG}
        del cfg["response_pattern"]
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="anything goes here", finish_reason="STOP")
        with unittest.mock.patch(
            "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
        ):
            result = enricher.enrich(_item(), cfg)
        self.assertEqual(result, "anything goes here")


class TestPromptSanitization(unittest.TestCase):
    """The `$description` substituted into the prompt comes from raw <p> tags
    of trending HTML and frequently contains markdown noise (` ``` ` fences,
    leading `*`/`#`). That noise was correlated with echo / markdown leaks in
    the 19.05.2026 batch. Sanitize before substitution; do NOT mutate the
    item itself."""

    def test_description_markdown_stripped_before_substitution(self) -> None:
        enricher = GeminiEnricher("test-model")
        item = NormalizedItem(
            dedupe_key="x",
            title="proj",
            source_id="s",
            description="```js\nconst x = 1;\n```\n* feature one\n# heading",
            raw={},
        )
        response = _FakeResponse(
            text="Для кого: X\nЗачем: Y",
            finish_reason="STOP",
        )
        fake = _FakeGenerativeModel(response)
        with unittest.mock.patch("gemini_enricher.genai.GenerativeModel", return_value=fake):
            enricher.enrich(item, _TWO_LINE_CFG)
        # description was the raw markdown blob — it should NOT appear verbatim
        # in the prompt sent to the model.
        self.assertNotIn("```", fake.last_prompt)
        self.assertNotIn("# heading", fake.last_prompt)
        # item itself was untouched.
        self.assertIn("```", item.description)

    def test_description_truncated_to_400_chars(self) -> None:
        enricher = GeminiEnricher("test-model")
        long_desc = "a" * 1000
        item = NormalizedItem(
            dedupe_key="x", title="proj", source_id="s", description=long_desc, raw={}
        )
        response = _FakeResponse(text="Для кого: X\nЗачем: Y", finish_reason="STOP")
        fake = _FakeGenerativeModel(response)
        with unittest.mock.patch("gemini_enricher.genai.GenerativeModel", return_value=fake):
            enricher.enrich(item, _TWO_LINE_CFG)
        # Bound: sanitized text + ellipsis. Allow some slack for word-boundary
        # truncation but ensure we're well below the original 1000.
        substituted_desc_len = fake.last_prompt.count("a")
        self.assertLess(substituted_desc_len, 500)


class TestGeminiEnricherQuota(unittest.TestCase):
    def test_resource_exhausted_raises_quota_exhausted(self) -> None:
        import google.api_core.exceptions
        from tenacity import RetryError

        from gemini_enricher import GeminiEnricher

        enricher = GeminiEnricher("test-model")
        exc = google.api_core.exceptions.ResourceExhausted("quota")
        retry_err = RetryError(last_attempt=unittest.mock.MagicMock())
        retry_err.__cause__ = exc

        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=retry_err),
            self.assertRaises(QuotaExhausted),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)

    def test_non_quota_error_raises_try_next_model(self) -> None:
        """After #130, any unexpected exception (network timeout,
        InvalidArgument, …) asks the rotator to try the next model rather
        than silently returning `on_error` for this single item."""
        from gemini_enricher import GeminiEnricher

        enricher = GeminiEnricher("test-model")
        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=RuntimeError("net")),
            self.assertRaises(TryNextModel),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)


class TestModelVersionSorting(unittest.TestCase):
    def test_newer_models_first(self) -> None:
        from gemini_enricher import _model_version_key

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
        from gemini_enricher import _model_version_key

        self.assertEqual(_model_version_key("models/chat-bison-001")[0], 0.0)


class TestIsTextGemini(unittest.TestCase):
    def test_accepts_text_models(self) -> None:
        from gemini_enricher import _is_text_gemini

        self.assertTrue(_is_text_gemini("models/gemini-2.5-flash"))
        self.assertTrue(_is_text_gemini("models/gemini-2.0-flash-lite"))
        self.assertTrue(_is_text_gemini("models/gemini-3.1-pro-preview"))
        self.assertTrue(_is_text_gemini("models/gemini-2.5-flash-lite"))

    def test_rejects_specialized_models(self) -> None:
        from gemini_enricher import _is_text_gemini

        self.assertFalse(_is_text_gemini("models/gemini-3.1-flash-tts-preview"))
        self.assertFalse(_is_text_gemini("models/gemini-3.1-flash-image-preview"))
        self.assertFalse(_is_text_gemini("models/gemini-3.1-pro-preview-customtools"))
        self.assertFalse(_is_text_gemini("models/gemini-2.5-computer-use-preview-10-2025"))
        self.assertFalse(_is_text_gemini("models/gemini-robotics-er-1.6-preview"))

    def test_rejects_non_gemini(self) -> None:
        from gemini_enricher import _is_text_gemini

        self.assertFalse(_is_text_gemini("models/gemma-3-27b-it"))
        self.assertFalse(_is_text_gemini("models/lyria-3-pro-preview"))
        self.assertFalse(_is_text_gemini("models/nano-banana-pro-preview"))


class TestRotatingGeminiEnricher(unittest.TestCase):
    def test_implements_enricher_protocol(self) -> None:
        self.assertIsInstance(RotatingGeminiEnricher(["m1"]), Enricher)

    def test_empty_model_list_raises(self) -> None:
        with self.assertRaises(ValueError):
            RotatingGeminiEnricher([])

    def test_rotates_to_next_model_on_quota(self) -> None:
        enricher = RotatingGeminiEnricher(["model-a", "model-b"])

        def side_effect(item: Any, cfg: Any) -> str:
            raise QuotaExhausted

        enricher._enrichers[0].enrich = side_effect  # type: ignore[assignment]
        enricher._enrichers[1].enrich = lambda item, cfg: "from-b"  # type: ignore[assignment]

        result = enricher.enrich(_item(), _ENRICH_CFG)
        self.assertEqual(result, "from-b")
        self.assertEqual(enricher._current, 1)

    def test_remembers_working_model_for_next_call(self) -> None:
        enricher = RotatingGeminiEnricher(["model-a", "model-b"])

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

    @unittest.mock.patch("gemini_enricher.time.sleep")
    def test_all_models_exhausted_sleeps_and_retries(self, mock_sleep: Any) -> None:
        call_count = 0

        def enrich_fn(item: Any, cfg: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise QuotaExhausted
            return "recovered"

        enricher = RotatingGeminiEnricher(["m1", "m2"])
        enricher._enrichers[0].enrich = enrich_fn  # type: ignore[assignment]
        enricher._enrichers[1].enrich = enrich_fn  # type: ignore[assignment]

        result = enricher.enrich(_item(), _ENRICH_CFG)
        self.assertEqual(result, "recovered")
        mock_sleep.assert_called_once_with(60)

    @unittest.mock.patch("gemini_enricher.time.sleep")
    def test_all_models_exhausted_twice_raises(self, mock_sleep: Any) -> None:
        def always_fail(item: Any, cfg: Any) -> str:
            raise QuotaExhausted

        enricher = RotatingGeminiEnricher(["m1", "m2"])
        enricher._enrichers[0].enrich = always_fail  # type: ignore[assignment]
        enricher._enrichers[1].enrich = always_fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            enricher.enrich(_item(), _ENRICH_CFG)
        mock_sleep.assert_called_once_with(60)


class TestGeminiEnricherModelUnavailable(unittest.TestCase):
    """Pin-tests for #128: ListModels returns Gemini models still in
    deprecation that 404 at GenerateContent. Treat the per-model `NotFound`
    as a signal to switch models — like `ResourceExhausted` (quota) does —
    instead of silently returning `on_error` for every item.
    """

    def test_not_found_raises_model_unavailable(self) -> None:
        import google.api_core.exceptions

        enricher = GeminiEnricher("models/gemini-3.1-flash-lite-preview")
        not_found = google.api_core.exceptions.NotFound(
            "404 This model models/gemini-3.1-flash-lite-preview is no longer available."
        )
        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=not_found),
            self.assertRaises(ModelUnavailable),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)

    def test_not_found_wrapped_in_retry_error_raises_model_unavailable(self) -> None:
        """Defensive: if tenacity is ever broadened to retry NotFound, the
        outer `RetryError` carries the original NotFound as `__cause__` —
        exactly the same shape `QuotaExhausted` detection handles today."""
        import google.api_core.exceptions
        from tenacity import RetryError

        enricher = GeminiEnricher("models/gemini-3.1-flash-lite-preview")
        not_found = google.api_core.exceptions.NotFound("404 model gone")
        retry_err = RetryError(last_attempt=unittest.mock.MagicMock())
        retry_err.__cause__ = not_found
        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=retry_err),
            self.assertRaises(ModelUnavailable),
        ):
            enricher.enrich(_item(), _ENRICH_CFG)


class TestRotatingGeminiEnricherModelUnavailable(unittest.TestCase):
    def test_dead_model_skipped_on_subsequent_items(self) -> None:
        """A 404'd model should be tried at most once per run. Today's bug
        (#128): all 8 trending items hit the same dead model before rotator
        even noticed, because `NotFound` was swallowed as `on_error`."""
        rotator = RotatingGeminiEnricher(["model-a", "model-b"])
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

        for i in range(5):
            self.assertEqual(rotator.enrich(_item(str(i)), _ENRICH_CFG), "from-b")

        self.assertEqual(a_calls, 1, "dead model must not be retried for every item")
        self.assertEqual(b_calls, 5)

    @unittest.mock.patch("gemini_enricher.time.sleep")
    def test_all_models_unavailable_raises_quota_exhausted(self, mock_sleep: Any) -> None:
        """When every rotated model 404s, surface a single quota-style
        exception so the pipeline switches to the visible fallback marker.
        """

        def fail(item: Any, cfg: Any) -> str:
            raise ModelUnavailable

        rotator = RotatingGeminiEnricher(["m1", "m2"])
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
        enricher = GeminiEnricher("test-model")
        response = _FakeResponse(text="строка 1:\nстрока 2:", finish_reason="STOP")
        with unittest.mock.patch(
            "gemini_enricher.genai.GenerativeModel", return_value=_FakeGenerativeModel(response)
        ):
            result = enricher.enrich(_item(), _TWO_LINE_CFG)
        self.assertEqual(result, FALLBACK_MARKER)


class TestRotatingGeminiEnricherTryNext(unittest.TestCase):
    def test_rotator_retries_on_next_model_after_truncated(self) -> None:
        rotator = RotatingGeminiEnricher(["model-a", "model-b"])

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
        rotator = RotatingGeminiEnricher(["model-a", "model-b"])
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
        # After item 1: _current is on model-b, but model-a is still live.
        # Move pointer back to model-a so item 2 actually exercises it; if
        # rotator wrongly added model-a to `_dead`, this call would skip it.
        rotator._current = 0
        rotator.enrich(_item("2"), _ENRICH_CFG)

        self.assertEqual(a_calls, 2, "model-a must remain live after TryNextModel")
        self.assertNotIn(0, rotator._dead)

    @unittest.mock.patch("gemini_enricher.time.sleep")
    def test_all_models_truncated_raises_quota_exhausted(self, mock_sleep: Any) -> None:
        """When every model fails on the same item — even if every failure
        is `TryNextModel` rather than 429 — the rotator surfaces a single
        `QuotaExhausted` so the pipeline substitutes `FALLBACK_MARKER`."""

        def fail(item: Any, cfg: Any) -> str:
            raise TryNextModel

        rotator = RotatingGeminiEnricher(["m1", "m2"])
        rotator._enrichers[0].enrich = fail  # type: ignore[assignment]
        rotator._enrichers[1].enrich = fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            rotator.enrich(_item(), _ENRICH_CFG)

    @unittest.mock.patch("gemini_enricher.time.sleep")
    def test_all_try_next_skips_cooldown(self, mock_sleep: Any) -> None:
        """Per claude-review #131: if every model raised only `TryNextModel`
        (no quota / no unavailable), the 60s cooldown buys nothing — the same
        item × same models will truncate identically after the wait. Skip it.
        """

        def fail(item: Any, cfg: Any) -> str:
            raise TryNextModel

        rotator = RotatingGeminiEnricher(["m1", "m2"])
        rotator._enrichers[0].enrich = fail  # type: ignore[assignment]
        rotator._enrichers[1].enrich = fail  # type: ignore[assignment]

        with self.assertRaises(QuotaExhausted):
            rotator.enrich(_item(), _ENRICH_CFG)
        mock_sleep.assert_not_called()

    @unittest.mock.patch("gemini_enricher.time.sleep")
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

        rotator = RotatingGeminiEnricher(["m1", "m2"])
        rotator._enrichers[0].enrich = enrich_fn  # type: ignore[assignment]
        rotator._enrichers[1].enrich = enrich_fn  # type: ignore[assignment]

        self.assertEqual(rotator.enrich(_item(), _ENRICH_CFG), "recovered")
        mock_sleep.assert_called_once_with(60)


class TestBuildDefaultEnricher(unittest.TestCase):
    """Pin-test for issue #93: silent degradation when GOOGLE_API_KEY is absent.

    Previously, `__main__` in json_pipeline / github_trending_pipeline silently
    constructed a `NullEnricher` when the env-var was missing, hiding the fact
    that enrichment was disabled. The trending workflow shipped without
    GOOGLE_API_KEY for ~3 cron runs after PR #89; the visibility gap kept this
    invisible. We now WARN whenever the production helper falls back to
    NullEnricher.
    """

    def test_empty_api_key_returns_null_enricher_and_warns(self) -> None:
        log = logging.getLogger("test_build_default_enricher.empty")
        with self.assertLogs(log, level="WARNING") as captured:
            result = build_default_enricher("", log)
        self.assertIsInstance(result, NullEnricher)
        joined = "\n".join(captured.output)
        self.assertIn("GOOGLE_API_KEY is empty", joined)
        self.assertIn("summary_ru", joined)

    def test_api_key_with_no_models_returns_null_enricher_and_warns(self) -> None:
        log = logging.getLogger("test_build_default_enricher.no_models")
        with (
            unittest.mock.patch("gemini_enricher.genai.configure"),
            unittest.mock.patch("gemini_enricher.get_generation_models", return_value=[]),
            self.assertLogs(log, level="WARNING") as captured,
        ):
            result = build_default_enricher("real-key", log)
        self.assertIsInstance(result, NullEnricher)
        self.assertIn("no generation models found", "\n".join(captured.output))

    def test_api_key_with_models_returns_rotating_enricher(self) -> None:
        log = logging.getLogger("test_build_default_enricher.ok")
        with (
            unittest.mock.patch("gemini_enricher.genai.configure") as mock_configure,
            unittest.mock.patch(
                "gemini_enricher.get_generation_models",
                return_value=["models/gemini-2.5-flash", "models/gemini-2.0-flash"],
            ),
        ):
            result = build_default_enricher("real-key", log)
        self.assertIsInstance(result, RotatingGeminiEnricher)
        mock_configure.assert_called_once_with(api_key="real-key")


if __name__ == "__main__":
    unittest.main()
