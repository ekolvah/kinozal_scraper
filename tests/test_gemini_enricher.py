from __future__ import annotations

import unittest
from typing import Any

from gemini_enricher import Enricher, NullEnricher
from generic_pipeline import NormalizedItem


class TestNullEnricher(unittest.TestCase):
    def test_implements_enricher_protocol(self) -> None:
        self.assertIsInstance(NullEnricher(), Enricher)

    def test_returns_on_error_value(self) -> None:
        enricher = NullEnricher()
        item = NormalizedItem(dedupe_key="x", title="X", source_id="s", raw={})
        config: dict[str, Any] = {"field": "summary", "prompt": "...", "on_error": "fallback"}
        self.assertEqual(enricher.enrich(item, config), "fallback")

    def test_returns_empty_when_on_error_missing(self) -> None:
        enricher = NullEnricher()
        item = NormalizedItem(dedupe_key="x", title="X", source_id="s", raw={})
        config: dict[str, Any] = {"field": "summary", "prompt": "..."}
        self.assertEqual(enricher.enrich(item, config), "")


class TestPromptTemplateSubstitution(unittest.TestCase):
    def test_safe_substitute_handles_braces_in_description(self) -> None:
        import string

        template = "Describe: $title — $description"
        context = {
            "title": "cool-lib",
            "description": 'A {json: "value"} parser',
        }
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
        import unittest.mock

        import google.api_core.exceptions
        from tenacity import RetryError

        from gemini_enricher import GeminiEnricher, QuotaExhausted

        enricher = GeminiEnricher("test-model")
        item = NormalizedItem(dedupe_key="u/r", title="u/r", source_id="s", description="d", raw={})
        config: dict[str, Any] = {
            "field": "summary",
            "prompt": "Describe $title",
            "on_error": "",
        }

        exc = google.api_core.exceptions.ResourceExhausted("quota")
        retry_err = RetryError(last_attempt=unittest.mock.MagicMock())
        retry_err.__cause__ = exc

        with (
            unittest.mock.patch.object(enricher, "_generate", side_effect=retry_err),
            self.assertRaises(QuotaExhausted),
        ):
            enricher.enrich(item, config)

    def test_non_quota_error_returns_on_error(self) -> None:
        import unittest.mock

        from gemini_enricher import GeminiEnricher

        enricher = GeminiEnricher("test-model")
        item = NormalizedItem(dedupe_key="u/r", title="u/r", source_id="s", description="d", raw={})
        config: dict[str, Any] = {
            "field": "summary",
            "prompt": "Describe $title",
            "on_error": "fallback",
        }

        with unittest.mock.patch.object(enricher, "_generate", side_effect=RuntimeError("net")):
            result = enricher.enrich(item, config)

        self.assertEqual(result, "fallback")


if __name__ == "__main__":
    unittest.main()
