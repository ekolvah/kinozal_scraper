"""Tests for the `github_new_popular` source, driven end-to-end through doubles.

Covers extraction and sorting, dedupe, empty and null-field responses, source
isolation, the exit-code surface, and enricher integration including the quota
circuit breaker.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

# HTTP-response doubles are real requests.Response objects — see tests/_http_doubles.py.
from _http_doubles import make_json_response, make_response

from kinozal_scraper.generic_pipeline import PipelineResult
from kinozal_scraper.github_popular_pipeline import _unwrap_records, run_github_popular_pipeline
from kinozal_scraper.sheets_storage import InMemoryStorage
from kinozal_scraper.telegram_notifier import InMemoryNotifier

_SOURCES_JSON = Path(__file__).resolve().parent.parent / "sources.json"

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
    "type": "github_popular",
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
    return unittest.mock.patch(
        "kinozal_scraper.github_popular_pipeline._fetch_json", return_value=response
    )


class TestConfigSourceType(unittest.TestCase):
    def test_enabled_github_source_uses_dedicated_type(self) -> None:
        # #275: the github source must carry a dedicated `github_popular` type,
        # not the generic format-keyed `json` bucket, so no other JSON source can
        # silently join it (grain of steam's `steam_charts`).
        config = json.loads(_SOURCES_JSON.read_text(encoding="utf-8"))
        github = next(s for s in config["sources"] if s["id"] == "github_new_popular")
        self.assertEqual(github["type"], "github_popular")


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


class TestGithubPopularHappyPath(unittest.TestCase):
    def test_items_extracted_notified_stored(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 3)
        self.assertEqual(len(storage.stored_rows("github_projects")), 3)
        self.assertEqual(storage.stored_rows("github_projects")[0][0], "user/repo-alpha")

    def test_notification_contains_language(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        text = notifier.sent[0].text
        self.assertIn("Python", text)
        self.assertIn("⭐ 500", text)


class TestGithubPopularDeduplication(unittest.TestCase):
    def test_existing_keys_not_re_notified(self) -> None:
        storage = InMemoryStorage()
        storage.seed_existing("github_projects", ["user/repo-alpha", "org/repo-beta"])
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(notifier.sent[0].id, "dev/repo-gamma")


class TestGithubPopularNullFields(unittest.TestCase):
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
            run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 1)
        self.assertNotIn("None", notifier.sent[0].text)


class TestGithubPopularEmptyResponse(unittest.TestCase):
    def test_empty_items_no_crash(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch({"items": []}):
            run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(notifier.sent), 0)
        self.assertEqual(len(storage.stored_rows("github_projects")), 0)


class TestGithubPopularFailedNotifications(unittest.TestCase):
    def test_failed_items_not_stored(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier(fail_ids={"user/repo-alpha", "org/repo-beta"})

        with _patch_fetch(_GITHUB_RESPONSE):
            results = run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        self.assertEqual(len(storage.stored_rows("github_projects")), 1)
        self.assertEqual(storage.stored_rows("github_projects")[0][0], "dev/repo-gamma")
        self.assertFalse(results[0].ok)
        self.assertTrue(any("notification(s) failed" in err for err in results[0].errors))


class TestGithubPopularSourceIsolation(unittest.TestCase):
    def test_one_source_error_does_not_block_others(self) -> None:
        # Guards the retained per-source loop-isolation mechanism (§IV): a failed
        # source must not block siblings. Only one live github_popular source
        # exists today; the fake multi-source config exercises the real loop code.
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
            "kinozal_scraper.github_popular_pipeline._fetch_json", side_effect=side_effect
        ):
            run_github_popular_pipeline(storage, notifier, sources_config=config)

        self.assertEqual(len(notifier.sent), 3)


# ── exit-code surface (issue #97) ─────────────────────────────────────────────


class TestGithubPopularExitCodeSurface(unittest.TestCase):
    """run_github_popular_pipeline must return list[PipelineResult] so __main__
    can sys.exit(1) on failed source. Previously errors were silent — see #97."""

    def test_fetch_failure_returns_not_ok_result(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_popular_pipeline._fetch_json",
            side_effect=ConnectionError("network down"),
        ):
            results = run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)
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
            results = run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)
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
            "kinozal_scraper.github_popular_pipeline._fetch_json", side_effect=side_effect
        ):
            results = run_github_popular_pipeline(storage, notifier, sources_config=config)

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

        with unittest.mock.patch(
            "kinozal_scraper.github_popular_pipeline.requests.get"
        ) as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = _GITHUB_RESPONSE
            mock_get.return_value.raise_for_status = lambda: None
            run_github_popular_pipeline(storage, notifier, sources_config=config)

            _, kwargs = mock_get.call_args
            self.assertNotIn("Authorization", kwargs.get("headers", {}))

    def _warnings_for(self, header_value: str) -> list[str]:
        source = {**_GITHUB_SOURCE, "headers": {"Authorization": header_value}}
        config = {"version": 1, "sources": [source]}

        with (
            unittest.mock.patch(
                "kinozal_scraper.github_popular_pipeline.requests.get",
                return_value=make_json_response(200, _GITHUB_RESPONSE),
            ) as mock_get,
            self.assertLogs("kinozal_scraper.github_popular_pipeline", level="WARNING") as logs,
        ):
            run_github_popular_pipeline(
                InMemoryStorage(), InMemoryNotifier(), sources_config=config
            )

        self.assertNotIn("Authorization", mock_get.call_args.kwargs.get("headers", {}))
        return logs.output

    def test_unset_token_is_not_reported_as_empty(self) -> None:
        # §IV: dropping the header is right, dropping it *silently* is not — an unset
        # GITHUB_TOKEN expands to "Bearer " → unauthenticated search → 403, and with
        # retry in place that is several 403s and no cause at all. The message must
        # NOT say "empty": the filter cannot tell an unset secret from one pasted
        # with a stray space, and an operator sent to check whether the secret exists
        # would see that it does and rule out the real cause.
        output = self._warnings_for("Bearer ")

        self.assertEqual(len(output), 1)
        self.assertIn("ends with a space", output[0])
        self.assertIn("Authorization", output[0])

    def test_padded_token_is_reported_without_leaking_the_value(self) -> None:
        output = self._warnings_for("Bearer ghp_xxx ")

        self.assertEqual(len(output), 1)
        self.assertIn("ends with a space", output[0])
        self.assertNotIn("ghp_xxx", output[0])

    def test_blank_header_is_reported_as_blank(self) -> None:
        # The other drop reason, and the other fix: add the secret, don't edit it.
        output = self._warnings_for("")

        self.assertEqual(len(output), 1)
        self.assertIn("blank value", output[0])
        self.assertIn("Authorization", output[0])


class TestFetchRetry(unittest.TestCase):
    """A transient 5xx from the GitHub Search API must not kill the source (#365).

    Patched at the transport boundary (`requests.get`) with real `requests.Response`
    objects, so `raise_for_status` / `.json()` run the same code as in prod.
    Backoff neutralised by patching tenacity's sleep. Sibling of
    `test_http_fetch.py::TestFetchRetry`, one transport over.
    """

    def _run(self, get_mock: unittest.mock.Mock) -> tuple[list[PipelineResult], InMemoryNotifier]:
        notifier = InMemoryNotifier()
        with unittest.mock.patch("kinozal_scraper.github_popular_pipeline.requests.get", get_mock):
            results = run_github_popular_pipeline(
                InMemoryStorage(), notifier, sources_config=_CONFIG
            )
        return results, notifier

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_retries_transient_503_then_succeeds(self, _sleep: unittest.mock.Mock) -> None:
        get = unittest.mock.Mock(
            side_effect=[make_response(503), make_json_response(200, _GITHUB_RESPONSE)]
        )
        results, notifier = self._run(get)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(results[0].errors, [])
        self.assertEqual(len(notifier.sent), 3)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_no_retry_on_rate_limit_403(self, _sleep: unittest.mock.Mock) -> None:
        # AC2: 403 here is a rate limit / bad token, not the anti-bot challenge the
        # HTML transport retries. GitHub documents that continuing to request while
        # rate limited risks banning the integration — one attempt, then surface it.
        get = unittest.mock.Mock(side_effect=lambda *a, **k: make_response(403))
        results, _ = self._run(get)

        self.assertEqual(get.call_count, 1)
        self.assertTrue(results[0].errors)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_gives_up_after_max_attempts_and_reports_error(
        self, _sleep: unittest.mock.Mock
    ) -> None:
        # §IV: give-up reraises, the per-source guard turns it into a visible
        # result.errors entry — never a quietly empty run.
        get = unittest.mock.Mock(side_effect=lambda *a, **k: make_response(503))
        results, notifier = self._run(get)

        self.assertEqual(get.call_count, 4)
        self.assertTrue(results[0].errors)
        self.assertEqual(notifier.sent, [])


class TestOrdering(unittest.TestCase):
    """Candidate order is the API's own (`sort`/`order` search params) — the
    pipeline never reorders, so the delivery cap applies to GitHub's ranking.

    The former `sort_by`/`sort_reverse` config knob was removed in #459: paging
    turned it into a *per-page* sort, which is not a meaningful order for anything
    downstream, and no source ever set it (§VII — a dead knob whose contract had
    quietly changed is worse than no knob)."""

    def test_api_order_is_preserved(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)

        stored_keys = [row[0] for row in storage.stored_rows("github_projects")]
        self.assertEqual(stored_keys, ["user/repo-alpha", "org/repo-beta", "dev/repo-gamma"])

    def test_sort_by_key_is_no_longer_honoured(self) -> None:
        # Ascending `sort_by` would have reversed the response order; it must not.
        source = {**_GITHUB_SOURCE, "sort_by": "stargazers_count", "sort_reverse": False}
        config: dict[str, Any] = {"version": 1, "sources": [source]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        with _patch_fetch(_GITHUB_RESPONSE):
            run_github_popular_pipeline(storage, notifier, sources_config=config)

        self.assertEqual(
            [n.id for n in notifier.sent],
            ["user/repo-alpha", "org/repo-beta", "dev/repo-gamma"],
        )


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
            run_github_popular_pipeline(
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
            "kinozal_scraper.github_popular_pipeline._fetch_json", return_value=fresh_response
        ):
            run_github_popular_pipeline(
                storage, notifier, enricher=_FakeEnricher(), sources_config=_ENRICH_CONFIG
            )

        self.assertIn("Описание: user/repo-alpha", notifier.sent[0].text)

    def test_no_enricher_skips_enrich_step(self) -> None:
        import copy

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        fresh_response = copy.deepcopy(_GITHUB_RESPONSE)

        with unittest.mock.patch(
            "kinozal_scraper.github_popular_pipeline._fetch_json", return_value=fresh_response
        ):
            run_github_popular_pipeline(
                storage, notifier, enricher=None, sources_config=_ENRICH_CONFIG
            )

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
            "kinozal_scraper.github_popular_pipeline._fetch_json", return_value=fresh_response
        ):
            run_github_popular_pipeline(
                storage, notifier, enricher=_QuotaEnricher(), sources_config=_ENRICH_CONFIG
            )

        self.assertEqual(len(notifier.sent), 3)
        self.assertIn("OK: user/repo-alpha", notifier.sent[0].text)
        self.assertNotIn("OK:", notifier.sent[1].text)
        self.assertNotIn("OK:", notifier.sent[2].text)
        self.assertEqual(call_count, 2)

    def test_all_models_exhausted_from_start_uses_on_error_fallback(self) -> None:
        """When every Gemini model is exhausted, the very first enrich() raises.

        Caller (run_github_popular_pipeline) must substitute `on_error` from
        sources.json into every item so notifications still go out — bug taxonomy
        category C (testing.md).
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
            "kinozal_scraper.github_popular_pipeline._fetch_json", return_value=fresh_response
        ):
            run_github_popular_pipeline(
                storage, notifier, enricher=_AlwaysExhaustedEnricher(), sources_config=config
            )

        self.assertEqual(len(notifier.sent), 3)
        for notif in notifier.sent:
            self.assertIn("[summary unavailable]", notif.text)
        self.assertEqual(len(storage.stored_rows("github_projects")), 3)


# ── #459: search depth is decoupled from the delivery cap ────────────────────


def _repo(name: str, stars: int = 100) -> dict[str, Any]:
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": "desc",
        "stargazers_count": stars,
        "language": "Python",
    }


def _page(*names: str) -> dict[str, Any]:
    return {"total_count": len(names), "items": [_repo(n) for n in names]}


class _Pager:
    """Serves one search page per call and records the `page` params requested."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.requested_pages: list[str] = []

    def __call__(self, url: str, params: dict[str, str], headers: dict[str, str]) -> Any:  # noqa: ARG002
        self.requested_pages.append(params.get("page", ""))
        index = len(self.requested_pages) - 1
        return self.pages[index] if index < len(self.pages) else _page()


def _run_paged(
    pager: _Pager,
    *,
    existing: set[str] | None = None,
    limit: int = 10,
    per_page: int = 3,
    ceiling: int = 9,
) -> tuple[InMemoryStorage, InMemoryNotifier, list[PipelineResult]]:
    """Run the source with small page/ceiling constants so the paging contract is
    readable in the test instead of needing 100-record fixtures."""
    storage = InMemoryStorage()
    if existing:
        storage.seed_existing("github_projects", existing)
    notifier = InMemoryNotifier()
    config = {"version": 1, "sources": [{**_GITHUB_SOURCE, "limit": limit}]}
    module = "kinozal_scraper.github_popular_pipeline"
    with (
        unittest.mock.patch(f"{module}._PER_PAGE", per_page),
        unittest.mock.patch(f"{module}._SEARCH_RESULT_CEILING", ceiling),
        unittest.mock.patch(f"{module}._fetch_json", side_effect=pager),
    ):
        results = run_github_popular_pipeline(storage, notifier, sources_config=config)
    return storage, notifier, results


class TestGithubPopularPaginatesPastKnownItems(unittest.TestCase):
    """#459 root cause: `limit` used to truncate *candidates* before dedup ran, so
    once the whole top-N sat in Sheets the source went permanently silent while
    green. `limit` now caps delivered new items; candidates are paged through."""

    def test_constants_match_documented_api_limits(self) -> None:
        # Sourced from the REST Search API docs (max per_page 100, max 1000
        # results per search), not from probing the live endpoint.
        from kinozal_scraper import github_popular_pipeline as mod

        self.assertEqual(mod._PER_PAGE, 100)
        self.assertEqual(mod._SEARCH_RESULT_CEILING, 1000)

    def test_new_repo_on_second_page_is_notified(self) -> None:
        pager = _Pager([_page("a/1", "a/2", "a/3"), _page("b/new")])
        _, notifier, results = _run_paged(
            pager, existing={"a/1", "a/2", "a/3"}, limit=2, per_page=3
        )
        self.assertEqual([n.id for n in notifier.sent], ["b/new"])
        self.assertTrue(results[0].ok, results[0].errors)

    def test_stops_when_limit_new_collected(self) -> None:
        pager = _Pager([_page("a/1", "a/2", "a/3"), _page("b/new")])
        _, notifier, _ = _run_paged(pager, limit=2, per_page=3)
        self.assertEqual(len(pager.requested_pages), 1)
        self.assertEqual(len(notifier.sent), 2)

    def test_stops_when_page_shorter_than_per_page(self) -> None:
        pager = _Pager([_page("a/1", "a/2")])
        _, notifier, results = _run_paged(pager, existing={"a/1", "a/2"}, limit=5, per_page=3)
        self.assertEqual(len(pager.requested_pages), 1)
        self.assertEqual(notifier.sent, [])
        self.assertTrue(results[0].ok, results[0].errors)

    def test_never_requests_beyond_api_result_ceiling(self) -> None:
        known = {f"a/{i}" for i in range(99)}
        pages = [_page(f"a/{3 * p}", f"a/{3 * p + 1}", f"a/{3 * p + 2}") for p in range(20)]
        pager = _Pager(pages)
        _run_paged(pager, existing=known, limit=5, per_page=3, ceiling=9)
        self.assertEqual(pager.requested_pages, ["1", "2", "3"])

    def test_ceiling_reached_logs_warning(self) -> None:
        known = {f"a/{i}" for i in range(99)}
        pages = [_page(f"a/{3 * p}", f"a/{3 * p + 1}", f"a/{3 * p + 2}") for p in range(20)]
        with self.assertLogs("kinozal_scraper.github_popular_pipeline", level="WARNING") as caplog:
            _run_paged(_Pager(pages), existing=known, limit=5, per_page=3, ceiling=9)
        self.assertIn("ceiling", "\n".join(caplog.output).lower())

    def test_intra_run_duplicate_counted_once(self) -> None:
        pager = _Pager([_page("a/1", "a/2", "b/dup"), _page("b/dup", "a/3", "b/new")])
        storage, notifier, results = _run_paged(
            pager, existing={"a/1", "a/2", "a/3"}, limit=2, per_page=3
        )
        self.assertEqual([n.id for n in notifier.sent], ["b/dup", "b/new"])
        self.assertEqual(len(storage.stored_rows("github_projects")), 2)
        metrics = results[0].metrics
        assert metrics is not None
        self.assertEqual(metrics.extracted, metrics.existing + metrics.new)

    def test_non_positive_limit_means_no_cap_not_one_page(self) -> None:
        # `select_new_items` documents `limit <= 0` as "no cap"; without the
        # `limit > 0` guard the paging stop condition (`len(selected) >= limit`)
        # is trivially true and silently turns that into "one page".
        # `_validate_limit` rejects such a config, so this is defence for the
        # sentinel itself, reachable only by handing the config in directly.
        pager = _Pager([_page("b/1", "b/2", "b/3"), _page("b/4")])
        _, notifier, _ = _run_paged(pager, limit=0, per_page=3)
        self.assertEqual(len(pager.requested_pages), 2)
        self.assertEqual(len(notifier.sent), 4)

    def test_later_page_failure_discards_the_run(self) -> None:
        # Fail-closed on purpose: `@retry_api_http` has already exhausted its
        # attempts by the time we get here, and the run reddens. Nothing is lost —
        # dedup is permanent, so the next run re-collects page 1 and delivers it.
        # Delivering a partial candidate set under a red result would make
        # "how deep did we look" unanswerable from the metrics line.
        def side_effect(url: str, params: dict[str, str], headers: dict[str, str]) -> Any:  # noqa: ARG001
            if params.get("page") == "2":
                raise ConnectionError("network down")
            return _page("b/1", "b/2", "b/3")

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        config = {"version": 1, "sources": [{**_GITHUB_SOURCE, "limit": 10}]}
        module = "kinozal_scraper.github_popular_pipeline"
        with (
            unittest.mock.patch(f"{module}._PER_PAGE", 3),
            unittest.mock.patch(f"{module}._SEARCH_RESULT_CEILING", 9),
            unittest.mock.patch(f"{module}._fetch_json", side_effect=side_effect),
        ):
            results = run_github_popular_pipeline(storage, notifier, sources_config=config)

        self.assertFalse(results[0].ok)
        self.assertEqual(notifier.sent, [])
        self.assertEqual(storage.stored_rows("github_projects"), [])
        # The summary is published on red runs too, so the counters must describe
        # what actually happened: page 1 extracted three items. Reporting
        # `extracted=0` here would diagnose "extraction produced nothing" instead
        # of "the second fetch died" (§IV — a wrong number is worse than none).
        metrics = results[0].metrics
        assert metrics is not None
        self.assertEqual((metrics.fetched, metrics.extracted), (3, 3))
        # `extracted=3 existing=0 new=0` would describe three candidates that were
        # neither known nor new — a state the model does not allow. The invariant
        # holds over whatever was examined, aborted scan included.
        self.assertEqual(metrics.extracted, metrics.existing + metrics.new)

    def test_ceiling_warning_reaches_the_run_summary(self) -> None:
        # The ceiling caveat is what makes `new=0` less reassuring than it looks, so
        # it has to travel on the surface that reports `new=0`, not only in the log.
        known = {f"a/{i}" for i in range(99)}
        pages = [_page(f"a/{3 * p}", f"a/{3 * p + 1}", f"a/{3 * p + 2}") for p in range(20)]
        _, _, results = _run_paged(_Pager(pages), existing=known, limit=5, per_page=3, ceiling=9)
        self.assertTrue(
            any("ceiling" in w for w in results[0].warnings),
            f"expected a ceiling warning on the result, got: {results[0].warnings}",
        )

    def test_one_bad_record_among_many_voids_the_source(self) -> None:
        # Pins the deliberate fail-closed choice in the direction where it costs
        # something: a versioned JSON API handing back a record without `full_name`
        # means the response contract changed, so no item's identity is trustworthy.
        good = _page("b/1", "b/2", "b/3")["items"]
        page = {"total_count": 4, "items": [*good, {"html_url": "x"}]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with _patch_fetch(page):
            results = run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)
        self.assertFalse(results[0].ok)
        self.assertEqual(notifier.sent, [])

    def test_delivers_at_most_limit_new_items(self) -> None:
        pager = _Pager([_page("b/1", "b/2", "b/3")])
        _, notifier, results = _run_paged(pager, limit=2, per_page=3)
        self.assertEqual(len(notifier.sent), 2)
        metrics = results[0].metrics
        assert metrics is not None
        # `new` reports what was found, `sent` what fitted under the cap — the
        # deferred remainder is visible in the operator line, not swallowed (§IV).
        self.assertEqual((metrics.new, metrics.sent), (3, 2))


class TestGithubPopularMetrics(unittest.TestCase):
    """Metrics must survive every exit path — a summary that reports zeros for a
    run that actually failed is the same silent-success defect one level up."""

    def test_metrics_reported_on_success(self) -> None:
        pager = _Pager([_page("a/1", "a/2", "b/new")])
        storage, _, results = _run_paged(pager, existing={"a/1", "a/2"}, limit=5, per_page=3)
        metrics = results[0].metrics
        assert metrics is not None
        self.assertEqual(
            (metrics.fetched, metrics.extracted, metrics.existing, metrics.new),
            (3, 3, 2, 1),
        )
        self.assertEqual((metrics.sent, metrics.stored), (1, 1))
        self.assertEqual(len(storage.stored_rows("github_projects")), 1)

    def test_metrics_reported_when_fetch_fails(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_popular_pipeline._fetch_json",
            side_effect=ConnectionError("network down"),
        ):
            results = run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)
        self.assertFalse(results[0].ok)
        self.assertIsNotNone(results[0].metrics)

    def test_metrics_reported_when_extraction_fails(self) -> None:
        broken = {"total_count": 2, "items": [{"html_url": "x"}, {"html_url": "y"}]}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with _patch_fetch(broken):
            results = run_github_popular_pipeline(storage, notifier, sources_config=_CONFIG)
        self.assertFalse(results[0].ok)
        metrics = results[0].metrics
        assert metrics is not None
        self.assertEqual((metrics.fetched, metrics.extracted), (2, 0))


if __name__ == "__main__":
    unittest.main()
