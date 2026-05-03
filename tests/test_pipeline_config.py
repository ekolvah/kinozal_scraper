import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from pipeline_config import ConfigError, build_macro_context, expand_macros, load_sources_config, validate_sources_config

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
    f = NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, f)
    f.flush()
    return Path(f.name)


class TestBuildMacroContext(unittest.TestCase):
    def test_date_macros_iso_format(self) -> None:
        ctx = build_macro_context(today=date(2024, 3, 15))
        self.assertEqual(ctx["TODAY"], "2024-03-15")
        self.assertEqual(ctx["DATE_MINUS_7_DAYS"], "2024-03-08")

    def test_env_macro_defaults(self) -> None:
        ctx = build_macro_context(today=date(2024, 1, 1), env={})
        self.assertEqual(ctx["GITHUB_TOP_LIMIT"], "10")
        self.assertEqual(ctx["STEAM_TOP_LIMIT"], "10")

    def test_env_macro_overrides(self) -> None:
        ctx = build_macro_context(today=date(2024, 1, 1), env={"GITHUB_TOP_LIMIT": "25", "STEAM_TOP_LIMIT": "50"})
        self.assertEqual(ctx["GITHUB_TOP_LIMIT"], "25")
        self.assertEqual(ctx["STEAM_TOP_LIMIT"], "50")


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


class TestLoadSourcesConfig(unittest.TestCase):
    def test_loads_valid_file(self) -> None:
        path = _write_tmp(_make_config())
        config = load_sources_config(path)
        self.assertEqual(config["version"], 1)
        self.assertEqual(len(config["sources"]), 1)

    def test_macro_expansion_in_url(self) -> None:
        source = {**_MINIMAL_SOURCE, "url": "https://api.example.com?since={{DATE_MINUS_7_DAYS}}"}
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
        f = NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        f.write("{bad json")
        f.flush()
        with self.assertRaises(ConfigError):
            load_sources_config(Path(f.name))

    def test_loads_actual_sources_json(self) -> None:
        sources_path = Path(__file__).resolve().parents[1] / "sources.json"
        if not sources_path.exists():
            self.skipTest("sources.json not found")
        config = load_sources_config(sources_path)
        self.assertIn("sources", config)


if __name__ == "__main__":
    unittest.main()
