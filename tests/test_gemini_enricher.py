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


if __name__ == "__main__":
    unittest.main()
