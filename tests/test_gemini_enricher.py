from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

from gemini_enricher import Enricher, NullEnricher, QuotaExhausted, RotatingGeminiEnricher
from generic_pipeline import NormalizedItem


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

    def test_non_quota_error_returns_on_error(self) -> None:
        from gemini_enricher import GeminiEnricher

        enricher = GeminiEnricher("test-model")
        with unittest.mock.patch.object(enricher, "_generate", side_effect=RuntimeError("net")):
            result = enricher.enrich(_item(), _ENRICH_CFG)
        self.assertEqual(result, "fallback")


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


if __name__ == "__main__":
    unittest.main()
