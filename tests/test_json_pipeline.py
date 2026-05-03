from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

from json_pipeline import _unwrap_records, run_json_pipeline
from sheets_storage import InMemoryStorage
from telegram_notifier import InMemoryNotifier

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
    return unittest.mock.patch("json_pipeline._fetch_json", return_value=response)


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
        storage._keys["github_projects"].add("user/repo-alpha")
        storage._keys["github_projects"].add("org/repo-beta")
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
            run_json_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(storage.stored_rows("github_projects")), 1)
        self.assertEqual(storage.stored_rows("github_projects")[0][0], "dev/repo-gamma")


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

        with unittest.mock.patch("json_pipeline._fetch_json", side_effect=side_effect):
            run_json_pipeline(storage, notifier, sources_config=config)

        self.assertEqual(len(notifier.sent), 3)


class TestEmptyAuthHeaderStripped(unittest.TestCase):
    def test_bearer_space_not_sent(self) -> None:
        source = {**_GITHUB_SOURCE, "headers": {"Authorization": "Bearer "}}
        config = {"version": 1, "sources": [source]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with unittest.mock.patch("json_pipeline.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = _GITHUB_RESPONSE
            mock_get.return_value.raise_for_status = lambda: None
            run_json_pipeline(storage, notifier, sources_config=config)

            _, kwargs = mock_get.call_args
            self.assertNotIn("Authorization", kwargs.get("headers", {}))


if __name__ == "__main__":
    unittest.main()
