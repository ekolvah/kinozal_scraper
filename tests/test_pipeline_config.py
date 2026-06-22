import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from pipeline_config import (
    ConfigError,
    build_macro_context,
    expand_macros,
    load_sources_config,
    validate_sources_config,
)

_MINIMAL_SOURCE = {
    "id": "test_src",
    "type": "json",
    "url": "https://example.com/api",
    "limit": 5,
    "sheet_tab": "test_tab",
    "dedupe_key": "id",
    "fields": {"title": "name", "url": "link"},
    "message_template": "{title}",
}


def _make_config(sources: list | None = None) -> dict:
    return {"version": 1, "sources": sources if sources is not None else [dict(_MINIMAL_SOURCE)]}


def _write_tmp(data: object) -> Path:
    with NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f)
        return Path(f.name)


class TestBuildMacroContext(unittest.TestCase):
    def test_date_macros_iso_format(self) -> None:
        ctx = build_macro_context(today=date(2024, 3, 15))
        self.assertEqual(ctx["TODAY"], "2024-03-15")
        self.assertEqual(ctx["DATE_MINUS_30_DAYS"], "2024-02-14")

    def test_env_macro_defaults(self) -> None:
        ctx = build_macro_context(today=date(2024, 1, 1), env={})
        self.assertEqual(ctx["GH_TOP_LIMIT"], "10")
        self.assertEqual(ctx["GH_TRENDING_LIMIT"], "10")
        self.assertEqual(ctx["STEAM_TOP_LIMIT"], "10")
        self.assertEqual(ctx["SOLDOUT_URL"], "")

    def test_soldout_url_override(self) -> None:
        ctx = build_macro_context(
            today=date(2024, 1, 1), env={"SOLDOUT_URL": "https://example.com/events"}
        )
        self.assertEqual(ctx["SOLDOUT_URL"], "https://example.com/events")

    def test_env_macro_overrides(self) -> None:
        ctx = build_macro_context(
            today=date(2024, 1, 1),
            env={"GH_TOP_LIMIT": "25", "STEAM_TOP_LIMIT": "50", "GH_TRENDING_LIMIT": "5"},
        )
        self.assertEqual(ctx["GH_TOP_LIMIT"], "25")
        self.assertEqual(ctx["STEAM_TOP_LIMIT"], "50")
        self.assertEqual(ctx["GH_TRENDING_LIMIT"], "5")


class TestExpandMacros(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = {"TODAY": "2024-03-15", "LIMIT": "10"}

    def test_expands_string(self) -> None:
        self.assertEqual(expand_macros("date={{TODAY}}", self.ctx), "date=2024-03-15")

    def test_expands_dict_recursively(self) -> None:
        result = expand_macros({"url": "?since={{TODAY}}&limit={{LIMIT}}"}, self.ctx)
        self.assertEqual(result, {"url": "?since=2024-03-15&limit=10"})

    def test_expands_list_recursively(self) -> None:
        result = expand_macros(["{{TODAY}}", "{{LIMIT}}"], self.ctx)
        self.assertEqual(result, ["2024-03-15", "10"])

    def test_unknown_macro_left_as_is(self) -> None:
        self.assertEqual(expand_macros("{{UNKNOWN}}", self.ctx), "{{UNKNOWN}}")

    def test_non_string_passthrough(self) -> None:
        self.assertIsNone(expand_macros(None, self.ctx))
        self.assertEqual(expand_macros(42, self.ctx), 42)


class TestValidateSourcesConfig(unittest.TestCase):
    def test_valid_config_passes(self) -> None:
        validate_sources_config(_make_config())

    def test_unsupported_version(self) -> None:
        with self.assertRaises(ConfigError):
            validate_sources_config({"version": 99, "sources": []})

    def test_missing_version(self) -> None:
        with self.assertRaises(ConfigError):
            validate_sources_config({"sources": []})

    def test_sources_not_list(self) -> None:
        with self.assertRaises(ConfigError):
            validate_sources_config({"version": 1, "sources": "bad"})

    def test_missing_required_field(self) -> None:
        source = dict(_MINIMAL_SOURCE)
        del source["dedupe_key"]
        with self.assertRaises(ConfigError):
            validate_sources_config(_make_config([source]))

    def test_unsupported_type(self) -> None:
        source = {**_MINIMAL_SOURCE, "type": "csv"}
        with self.assertRaises(ConfigError):
            validate_sources_config(_make_config([source]))

    def test_non_integer_limit(self) -> None:
        source = {**_MINIMAL_SOURCE, "limit": "not_a_number"}
        with self.assertRaises(ConfigError):
            validate_sources_config(_make_config([source]))

    def test_zero_limit(self) -> None:
        source = {**_MINIMAL_SOURCE, "limit": 0}
        with self.assertRaises(ConfigError):
            validate_sources_config(_make_config([source]))

    def test_negative_limit(self) -> None:
        source = {**_MINIMAL_SOURCE, "limit": -5}
        with self.assertRaises(ConfigError):
            validate_sources_config(_make_config([source]))

    def test_html_requires_row_selector(self) -> None:
        source = {**_MINIMAL_SOURCE, "type": "html"}
        source.pop("row_selector", None)
        with self.assertRaises(ConfigError) as ctx:
            validate_sources_config(_make_config([source]))
        self.assertIn("test_src", str(ctx.exception))
        self.assertIn("row_selector", str(ctx.exception))

    def test_html_with_row_selector_passes(self) -> None:
        source = {**_MINIMAL_SOURCE, "type": "html", "row_selector": "article.Box-row"}
        validate_sources_config(_make_config([source]))

    def test_invalid_css_row_selector_raises(self) -> None:
        source = {**_MINIMAL_SOURCE, "type": "html", "row_selector": "div[unclosed-bracket"}
        with self.assertRaises(ConfigError) as ctx:
            validate_sources_config(_make_config([source]))
        self.assertIn("row_selector", str(ctx.exception))

    def test_invalid_css_field_selector_raises(self) -> None:
        source = {
            **_MINIMAL_SOURCE,
            "type": "html",
            "row_selector": "article.Box-row",
            "fields": {"title": "h2[unclosed"},
        }
        with self.assertRaises(ConfigError) as ctx:
            validate_sources_config(_make_config([source]))
        self.assertIn("fields.title", str(ctx.exception))

    def test_broken_at_selector_rejects_css_part(self) -> None:
        # rsplit("@", 1) leaves css part "div[bad" — must be compiled and rejected.
        source = {
            **_MINIMAL_SOURCE,
            "type": "html",
            "row_selector": "article.Box-row",
            "fields": {"url": "div[bad@href"},
        }
        with self.assertRaises(ConfigError):
            validate_sources_config(_make_config([source]))

    def test_valid_selectors_with_attr_pass(self) -> None:
        source = {
            **_MINIMAL_SOURCE,
            "type": "html",
            "row_selector": "article.Box-row",
            "dedupe_key": "h2 a@href",
            "fields": {"title": "h2 a@href", "url": "@href", "metric": 'a[href$="/stargazers"]'},
        }
        validate_sources_config(_make_config([source]))


class TestLoadSourcesConfig(unittest.TestCase):
    def test_loads_valid_file(self) -> None:
        path = _write_tmp(_make_config())
        config = load_sources_config(path)
        self.assertEqual(config["version"], 1)
        self.assertEqual(len(config["sources"]), 1)

    def test_macro_expansion_in_url(self) -> None:
        source = {**_MINIMAL_SOURCE, "url": "https://api.example.com?since={{DATE_MINUS_30_DAYS}}"}
        path = _write_tmp(_make_config([source]))
        config = load_sources_config(path)
        url = config["sources"][0]["url"]
        self.assertNotIn("{{", url)
        self.assertRegex(url, r"\d{4}-\d{2}-\d{2}")

    def test_limit_converted_to_int_after_macro(self) -> None:
        source = {**_MINIMAL_SOURCE, "limit": "5"}
        path = _write_tmp(_make_config([source]))
        config = load_sources_config(path)
        self.assertIsInstance(config["sources"][0]["limit"], int)
        self.assertEqual(config["sources"][0]["limit"], 5)

    def test_file_not_found(self) -> None:
        with self.assertRaises(ConfigError):
            load_sources_config("nonexistent_file.json")

    def test_invalid_json(self) -> None:
        with NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{bad json")
            tmp_path = Path(f.name)
        with self.assertRaises(ConfigError):
            load_sources_config(tmp_path)

    def test_loads_actual_sources_json(self) -> None:
        sources_path = Path(__file__).resolve().parents[1] / "sources.json"
        if not sources_path.exists():
            self.skipTest("sources.json not found")
        config = load_sources_config(sources_path)
        self.assertIn("sources", config)

    def test_unresolved_macro_in_url_raises_config_error(self) -> None:
        source = {**_MINIMAL_SOURCE, "url": "https://api.example.com?d={{TOAY}}"}
        path = _write_tmp(_make_config([source]))
        with self.assertRaises(ConfigError) as ctx:
            load_sources_config(path)
        self.assertIn("{{TOAY}}", str(ctx.exception))
        self.assertIn("sources[0].url", str(ctx.exception))

    def test_unresolved_macro_in_nested_field_raises(self) -> None:
        source = {**_MINIMAL_SOURCE, "headers": {"X-Token": "{{NOPE}}"}}
        path = _write_tmp(_make_config([source]))
        with self.assertRaises(ConfigError) as ctx:
            load_sources_config(path)
        self.assertIn("{{NOPE}}", str(ctx.exception))


class TestRussianEnrichPrompts(unittest.TestCase):
    """Pin-tests for #88: both GitHub sources must emit the same Russian
    two-line `summary_ru` (Для кого / Зачем) so notifications read uniformly.
    """

    def setUp(self) -> None:
        sources_path = Path(__file__).resolve().parents[1] / "sources.json"
        if not sources_path.exists():
            self.skipTest("sources.json not found")
        self.config = load_sources_config(sources_path)
        self.by_id = {s["id"]: s for s in self.config["sources"]}

    def _assert_who_pain_enrich(self, source_id: str) -> None:
        assert source_id in self.by_id, f"missing source: {source_id}"
        source = self.by_id[source_id]
        assert "enrich" in source, f"{source_id} has no enrich block"
        enrich = source["enrich"]
        self.assertEqual(
            enrich["field"], "summary_ru", f"{source_id}.enrich.field must be 'summary_ru'"
        )
        prompt = enrich["prompt"]
        self.assertIn("Для кого", prompt, f"{source_id}.enrich.prompt must mention 'Для кого'")
        self.assertIn("Зачем", prompt, f"{source_id}.enrich.prompt must mention 'Зачем'")
        template = source["message_template"]
        self.assertIn(
            "{summary_ru}", template, f"{source_id}.message_template must use {{summary_ru}}"
        )

    def test_github_new_popular_has_who_pain_prompt(self) -> None:
        self._assert_who_pain_enrich("github_new_popular")

    def test_github_trending_has_who_pain_prompt(self) -> None:
        self._assert_who_pain_enrich("github_trending")

    def test_steam_prompt_targets_russian(self) -> None:
        """Pin-test for #124: steam_charts_mostplayed must translate `short_description`
        to Russian via `description_ru` field, and template must reference it."""
        source_id = "steam_charts_mostplayed"
        assert source_id in self.by_id, f"missing source: {source_id}"
        source = self.by_id[source_id]
        assert "enrich" in source, f"{source_id} has no enrich block"
        enrich = source["enrich"]
        self.assertEqual(
            enrich["field"],
            "description_ru",
            f"{source_id}.enrich.field must be 'description_ru'",
        )
        prompt = enrich["prompt"]
        self.assertIn(
            "русск",
            prompt.lower(),
            f"{source_id}.enrich.prompt must explicitly ask for Russian translation",
        )
        template = source["message_template"]
        self.assertIn(
            "{description_ru}",
            template,
            f"{source_id}.message_template must use {{description_ru}}",
        )


if __name__ == "__main__":
    unittest.main()
