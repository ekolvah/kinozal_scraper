from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

from kinozal_scraper.generic_pipeline import PipelineResult
from kinozal_scraper.json_pipeline import _unwrap_records, run_json_pipeline
from kinozal_scraper.sheets_storage import InMemoryStorage
from kinozal_scraper.telegram_notifier import InMemoryNotifier

_GITHUB_RESPONSE: dict[str, Any] = {
    "total_count": 3,
    "items": [
        {
            "full_name": "user/repo-alpha",
            "html_url": "https://github.com/user/repo-alpha",
            "description": "A cool project",
            "stargazers_count": 500,
            "language": "Python",
        },
        {
            "full_name": "org/repo-beta",
            "html_url": "https://github.com/org/repo-beta",
            "description": None,
            "stargazers_count": 300,
            "language": None,
        },
        {
            "full_name": "dev/repo-gamma",
            "html_url": "https://github.com/dev/repo-gamma",
            "description": "Third project",
            "stargazers_count": 100,
            "language": "Rust",
        },
    ],
}

_GITHUB_SOURCE: dict[str, Any] = {
    "id": "github_new_popular",
    "enabled": True,
    "type": "json",
    "url": "https://api.github.com/search/repositories",
    "json_path": "items",
    "headers": {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": "Bearer test-token",
    },
    "params": {"q": "created:>=2026-04-26", "sort": "stars", "order": "desc", "per_page": "10"},
    "limit": 10,
    "sheet_tab": "github_projects",
    "dedupe_key": "full_name",
    "fields": {
        "title": "full_name",
        "url": "html_url",
        "description": "description",
        "metric": "stargazers_count",
        "image_url": None,
    },
    "message_template": "<b>{title}</b>\n{description}\n⭐ {metric} | {language}\n{url}",
}

_CONFIG: dict[str, Any] = {"version": 1, "sources": [_GITHUB_SOURCE]}


def _patch_fetch(response: Any) -> unittest.mock._patch[unittest.mock.MagicMock]:
    return unittest.mock.patch("kinozal_scraper.json_pipeline._fetch_json", return_value=response)


class TestUnwrapRecords(unittest.TestCase):
    def test_json_path_items(self) -> None:
        data = {"total_count": 2, "items": [{"a": 1}, {"b": 2}]}
        self.assertEqual(_unwrap_records(data, "items"), [{"a": 1}, {"b": 2}])

    def test_nested_json_path(self) -> None:
        data = {"response": {"data": [{"x": 1}]}}
        self.assertEqual(_unwrap_records(data, "response.data"), [{"x": 1}])

    def test_none_path_with_list(self) -> None:
        data = [{"a": 1}]
        self.assertEqual(_unwrap_records(data, None), [{"a": 1}])

    def test_none_path_with_dict_of_dicts(self) -> None:
        data = {"100": {"name": "Game A"}, "200": {"name": "Game B"}}
        result = _unwrap_records(data, None)
        self.assertEqual(len(result), 2)
        self.assertIn({"name": "Game A"}, result)

    def test_none_path_with_non_dict_values(self) -> None:
        data = {"key": "string_value"}
        self.assertEqual(_unwrap_records(data, None), [])

    def test_missing_key_returns_empty(self) -> None:
        data = {"other": [1, 2, 3]}
        self.assertEqual(_unwrap_records(data, "items"), [])

    def test_non_list_at_path_returns_empty(self) -> None:
        data = {"items": "not a list"}
        self.assertEqual(_unwrap_records(data, "items"), [])


class TestJsonPipelineHappyPath(unittest.TestCase):
    def test_items_extracted_notified_stored(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 3)
        self.assertEqual(len(storage.stored_rows("github_projects")), 3)
        self.assertEqual(storage.stored_rows("github_projects")[0][0], "user/repo-alpha")

    def test_notification_contains_language(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        text = notifier.sent[0].text
        self.assertIn("Python", text)
        self.assertIn("⭐ 500", text)


class TestJsonPipelineDeduplication(unittest.TestCase):
    def test_existing_keys_not_re_notified(self) -> None:
        storage = InMemoryStorage()
        storage.seed_existing("github_projects", ["user/repo-alpha", "org/repo-beta"])
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(notifier.sent[0].id, "dev/repo-gamma")


class TestJsonPipelineNullFields(unittest.TestCase):
    def test_null_description_and_language(self) -> None:
        response = {
            "items": [
                {
                    "full_name": "x/null-fields",
                    "html_url": "https://github.com/x/null-fields",
                    "description": None,
                    "stargazers_count": 10,
                    "language": None,
                }
            ]
        }
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(response):
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 1)
        self.assertNotIn("None", notifier.sent[0].text)


class TestJsonPipelineEmptyResponse(unittest.TestCase):
    def test_empty_items_no_crash(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch({"items": []}):
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 0)
        self.assertEqual(len(storage.stored_rows("github_projects")), 0)


class TestJsonPipelineFailedNotifications(unittest.TestCase):
    def test_failed_items_not_stored(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier(fail_ids={"user/repo-alpha", "org/repo-beta"})

        with _patch_fetch(_GITHUB_RESPONSE):
            results = run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(storage.stored_rows("github_projects")), 1)
        self.assertEqual(storage.stored_rows("github_projects")[0][0], "dev/repo-gamma")
        self.assertFalse(results[0].ok)
        self.assertTrue(any("notification(s) failed" in err for err in results[0].errors))


class TestJsonPipelineSourceIsolation(unittest.TestCase):
    def test_one_source_error_does_not_block_others(self) -> None:
        broken_source: dict[str, Any] = {
            **_GITHUB_SOURCE,
            "id": "broken_source",
            "url": "https://broken.example.com",
            "sheet_tab": "broken",
        }
        config = {"version": 1, "sources": [broken_source, _GITHUB_SOURCE]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        def side_effect(url: str, params: Any, headers: Any) -> Any:
            if "broken" in url:
                raise ConnectionError("network down")
            return _GITHUB_RESPONSE

        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", side_effect=side_effect
        ):
            run_json_pipeline(storage, notifier, sources_config=config)

        self.assertEqual(len(notifier.sent), 3)


# ── exit-code surface (issue #97) ─────────────────────────────────────────────


class TestJsonPipelineExitCodeSurface(unittest.TestCase):
    """run_json_pipeline must return list[PipelineResult] so __main__ can
    sys.exit(1) on failed source. Previously errors were silent — see #97."""

    def test_fetch_failure_returns_not_ok_result(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", side_effect=ConnectionError("network down")
        ):
            results = run_json_pipeline(storage, notifier, sources_config=_CONFIG)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], PipelineResult)
        self.assertFalse(results[0].ok)
        self.assertTrue(
            any("fetch failed" in err for err in results[0].errors),
            f"expected 'fetch failed' in errors, got: {results[0].errors}",
        )

    def test_successful_run_returns_all_ok_results(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with _patch_fetch(_GITHUB_RESPONSE):
            results = run_json_pipeline(storage, notifier, sources_config=_CONFIG)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual([r.source_id for r in results], ["github_new_popular"])

    def test_partial_failure_one_ok_one_not(self) -> None:
        broken_source: dict[str, Any] = {
            **_GITHUB_SOURCE,
            "id": "broken_source",
            "url": "https://broken.example.com",
            "sheet_tab": "broken",
        }
        config = {"version": 1, "sources": [broken_source, _GITHUB_SOURCE]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        def side_effect(url: str, params: Any, headers: Any) -> Any:
            if "broken" in url:
                raise ConnectionError("network down")
            return _GITHUB_RESPONSE

        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", side_effect=side_effect
        ):
            results = run_json_pipeline(storage, notifier, sources_config=config)

        self.assertEqual(len(results), 2)
        ok_by_id = {r.source_id: r.ok for r in results}
        self.assertFalse(ok_by_id["broken_source"])
        self.assertTrue(ok_by_id["github_new_popular"])


class TestEmptyAuthHeaderStripped(unittest.TestCase):
    def test_bearer_space_not_sent(self) -> None:
        source = {**_GITHUB_SOURCE, "headers": {"Authorization": "Bearer "}}
        config = {"version": 1, "sources": [source]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with unittest.mock.patch("kinozal_scraper.json_pipeline.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = _GITHUB_RESPONSE
            mock_get.return_value.raise_for_status = lambda: None
            run_json_pipeline(storage, notifier, sources_config=config)

            _, kwargs = mock_get.call_args
            self.assertNotIn("Authorization", kwargs.get("headers", {}))


class TestSorting(unittest.TestCase):
    def test_sort_by_descending(self) -> None:
        source = {**_GITHUB_SOURCE, "sort_by": "stargazers_count", "sort_reverse": True}
        config: dict[str, Any] = {"version": 1, "sources": [source]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_json_pipeline(storage, notifier, sources_config=config)

        stored_keys = [row[0] for row in storage.stored_rows("github_projects")]
        self.assertEqual(stored_keys, ["user/repo-alpha", "org/repo-beta", "dev/repo-gamma"])

    def test_no_sort_by_preserves_order(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 3)


# ── Enricher integration tests ─────────────────────────────────────────────────

_GITHUB_SOURCE_WITH_ENRICH: dict[str, Any] = {
    **_GITHUB_SOURCE,
    "enrich": {
        "field": "summary_ru",
        "prompt": "Describe $title in Russian",
        "parameters": {"temperature": 0.2, "max_tokens": 150},
        "on_error": "",
    },
    "message_template": "<b>{title}</b>\n{summary_ru}\n⭐ {metric} | {language}\n{url}",
}

_ENRICH_CONFIG: dict[str, Any] = {"version": 1, "sources": [_GITHUB_SOURCE_WITH_ENRICH]}


class _FakeEnricher:
    def enrich(self, item: Any, enrich_config: dict[str, Any]) -> str:
        return f"Описание: {item.title}"


class TestEnricherIntegration(unittest.TestCase):
    def test_null_enricher_sets_empty_field(self) -> None:
        from kinozal_scraper.gemini_enricher import NullEnricher

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_json_pipeline(
                storage, notifier, enricher=NullEnricher(), sources_config=_ENRICH_CONFIG
            )

        self.assertEqual(len(notifier.sent), 3)
        self.assertNotIn("None", notifier.sent[0].text)

    def test_fake_enricher_field_in_notification(self) -> None:
        import copy

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        fresh_response = copy.deepcopy(_GITHUB_RESPONSE)

        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", return_value=fresh_response
        ):
            run_json_pipeline(
                storage, notifier, enricher=_FakeEnricher(), sources_config=_ENRICH_CONFIG
            )

        self.assertIn("Описание: user/repo-alpha", notifier.sent[0].text)

    def test_no_enricher_skips_enrich_step(self) -> None:
        import copy

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        fresh_response = copy.deepcopy(_GITHUB_RESPONSE)

        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", return_value=fresh_response
        ):
            run_json_pipeline(storage, notifier, enricher=None, sources_config=_ENRICH_CONFIG)

        self.assertEqual(len(notifier.sent), 3)
        self.assertNotIn("Описание", notifier.sent[0].text)


class TestEnricherQuotaCircuitBreaker(unittest.TestCase):
    def test_quota_stops_enrichment_but_sends_all(self) -> None:
        import copy

        from kinozal_scraper.gemini_enricher import QuotaExhausted

        call_count = 0

        class _QuotaEnricher:
            def enrich(self, item: Any, enrich_config: dict[str, Any]) -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return f"OK: {item.title}"
                raise QuotaExhausted

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        fresh_response = copy.deepcopy(_GITHUB_RESPONSE)

        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", return_value=fresh_response
        ):
            run_json_pipeline(
                storage, notifier, enricher=_QuotaEnricher(), sources_config=_ENRICH_CONFIG
            )

        self.assertEqual(len(notifier.sent), 3)
        self.assertIn("OK: user/repo-alpha", notifier.sent[0].text)
        self.assertNotIn("OK:", notifier.sent[1].text)
        self.assertNotIn("OK:", notifier.sent[2].text)
        self.assertEqual(call_count, 2)

    def test_all_models_exhausted_from_start_uses_on_error_fallback(self) -> None:
        """When every Gemini model is exhausted, the very first enrich() raises.

        Caller (run_json_pipeline) must substitute `on_error` from sources.json
        into every item so notifications still go out — gap C in test-coverage.md.
        """
        import copy

        from kinozal_scraper.gemini_enricher import QuotaExhausted

        class _AlwaysExhaustedEnricher:
            def enrich(self, item: Any, enrich_config: dict[str, Any]) -> str:
                raise QuotaExhausted

        source = copy.deepcopy(_GITHUB_SOURCE_WITH_ENRICH)
        source["enrich"]["on_error"] = "[summary unavailable]"
        config = {"version": 1, "sources": [source]}

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        fresh_response = copy.deepcopy(_GITHUB_RESPONSE)

        with unittest.mock.patch(
            "kinozal_scraper.json_pipeline._fetch_json", return_value=fresh_response
        ):
            run_json_pipeline(
                storage, notifier, enricher=_AlwaysExhaustedEnricher(), sources_config=config
            )

        self.assertEqual(len(notifier.sent), 3)
        for notif in notifier.sent:
            self.assertIn("[summary unavailable]", notif.text)
        self.assertEqual(len(storage.stored_rows("github_projects")), 3)


if __name__ == "__main__":
    unittest.main()
