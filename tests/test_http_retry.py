"""Транспортно-агностичная политика ретрая транзиентных HTTP-ответов (#365).

Гард на два именованных набора кодов и на то, что все прод-call site'а живут по одному
бюджету попыток. Ключевое утверждение здесь — **не** «ретрай есть», а «403/429 ретраятся
только у HTML-транспорта за Cloudflare»: у JSON-API это rate limit со своим окном сброса,
и долбёжка в него противоречит документации GitHub (см. `http_retry` docstring).

Предикат проверяется на исключениях, поднятых **настоящим** `raise_for_status()` настоящего
`Response` обоих транспортов — у `curl_cffi` и stdlib `requests` разные иерархии `HTTPError`
без общего предка, и ошибка в пути к статусу тихо превратила бы ретрай в его отсутствие
(жанр реалити-анкера из #298/#306).
"""

from __future__ import annotations

import contextlib
import unittest
import unittest.mock
from collections.abc import Callable
from typing import Any

import requests

# HTTP-response doubles are real requests.Response objects — see tests/_http_doubles.py.
from _http_doubles import make_json_response, make_response
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from curl_cffi.requests.models import Response as CurlResponse

from kinozal_scraper.http_retry import (
    ANTIBOT_TRANSIENT_CODES,
    API_TRANSIENT_CODES,
    transient_http_predicate,
)

_GITHUB_URL = "https://api.github.com/search/repositories"
_STEAM_CHARTS_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"


def _curl_error(status: int) -> CurlHTTPError:
    """A real curl_cffi HTTPError, raised by the library's own raise_for_status."""
    resp = CurlResponse()
    resp.status_code = status
    resp.ok = False
    try:
        resp.raise_for_status()
    except CurlHTTPError as exc:
        return exc
    raise AssertionError(f"curl_cffi did not raise for status {status}")


def _requests_error(status: int) -> requests.HTTPError:
    """A real stdlib requests.HTTPError, raised by the library's own raise_for_status."""
    resp = make_response(status)
    resp.url = _GITHUB_URL
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        return exc
    raise AssertionError(f"requests did not raise for status {status}")


class TestTransientPredicate(unittest.TestCase):
    def setUp(self) -> None:
        self.is_antibot = transient_http_predicate(ANTIBOT_TRANSIENT_CODES)
        self.is_api = transient_http_predicate(API_TRANSIENT_CODES)

    def test_curl_cffi_http_error_is_transient(self) -> None:
        # Reality-anchor moved here from test_http_fetch.py together with the
        # predicate it guards: the attr path to the status must survive the move.
        self.assertTrue(self.is_antibot(_curl_error(403)))
        self.assertTrue(self.is_antibot(_curl_error(503)))

    def test_requests_http_error_is_transient(self) -> None:
        # Sibling anchor for the second transport — a separate HTTPError hierarchy
        # (no common ancestor with curl_cffi's), so it needs its own evidence.
        self.assertTrue(self.is_api(_requests_error(503)))
        self.assertTrue(self.is_antibot(_requests_error(503)))

    def test_permanent_status_not_transient(self) -> None:
        self.assertFalse(self.is_antibot(_curl_error(404)))
        self.assertFalse(self.is_api(_requests_error(404)))

    def test_rate_limit_not_transient_for_api_set(self) -> None:
        # The whole point of two sets: 403/429 is a proven-transient anti-bot
        # challenge for the Cloudflare-fronted HTML transport (#358) and a rate
        # limit with its own reset window for JSON APIs.
        for status in (403, 429):
            with self.subTest(status=status):
                self.assertTrue(self.is_antibot(_requests_error(status)))
                self.assertFalse(self.is_api(_requests_error(status)))

    def test_non_http_exception_not_transient(self) -> None:
        # Boundary of accepted gap M: network-level errors never reach
        # raise_for_status, so they are out of the predicate by construction.
        for exc in (requests.ConnectionError("dns"), requests.Timeout("slow"), ValueError("x")):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(self.is_api(exc))

    def test_missing_response_not_transient(self) -> None:
        # A bare HTTPError carries no `.response`; reading the status must degrade
        # to "not transient", never to an AttributeError inside the retry predicate.
        self.assertFalse(self.is_api(requests.HTTPError("no response attached")))


class TestPolicyParity(unittest.TestCase):
    """Every production call site runs on the same attempt budget (#365 AC3).

    Behavioural, not introspective: an "it holds no private copy of the constants"
    assertion cannot be written (assert-absence), but a per-call-site attempt count
    catches exactly the drift the single-home requirement exists to prevent.
    """

    def _call_sites(self) -> list[tuple[str, str, Callable[[], Any]]]:
        from kinozal_scraper.github_popular_pipeline import _fetch_json
        from kinozal_scraper.steam_pipeline import _fetch_appdetails, _fetch_charts

        return [
            (
                "github_popular._fetch_json",
                "kinozal_scraper.github_popular_pipeline.requests.get",
                lambda: _fetch_json(_GITHUB_URL, {}, {}),
            ),
            (
                "steam._fetch_charts",
                "kinozal_scraper.steam_pipeline.requests.get",
                lambda: _fetch_charts(_STEAM_CHARTS_URL),
            ),
            (
                "steam._fetch_appdetails",
                "kinozal_scraper.steam_pipeline.requests.get",
                lambda: _fetch_appdetails(730),
            ),
        ]

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_all_call_sites_retry_5xx_four_times(self, _sleep: unittest.mock.Mock) -> None:
        for name, target, call in self._call_sites():
            with (
                self.subTest(call_site=name),
                unittest.mock.patch(target, side_effect=lambda *a, **k: make_response(503)) as get,
                self.assertRaises(requests.HTTPError),
            ):
                call()
            self.assertEqual(get.call_count, 4, name)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_all_call_sites_fail_fast_on_404(self, _sleep: unittest.mock.Mock) -> None:
        for name, target, call in self._call_sites():
            with (
                self.subTest(call_site=name),
                unittest.mock.patch(target, side_effect=lambda *a, **k: make_response(404)) as get,
                self.assertRaises(requests.HTTPError),
            ):
                call()
            self.assertEqual(get.call_count, 1, name)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_all_call_sites_fail_fast_on_rate_limit(self, _sleep: unittest.mock.Mock) -> None:
        # AC2: hammering a rate limit is what GitHub warns gets an integration
        # banned — the API set must not turn one 403 into four.
        for name, target, call in self._call_sites():
            with (
                self.subTest(call_site=name),
                unittest.mock.patch(target, side_effect=lambda *a, **k: make_response(403)) as get,
                self.assertRaises(requests.HTTPError),
            ):
                call()
            self.assertEqual(get.call_count, 1, name)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_successful_retry_is_logged(self, _sleep: unittest.mock.Mock) -> None:
        # §IV: without a line per attempt a flapping source is invisible until it
        # dies completely — the retry would hide exactly what it was added to survive.
        from kinozal_scraper.github_popular_pipeline import _fetch_json

        with (
            unittest.mock.patch(
                "kinozal_scraper.github_popular_pipeline.requests.get",
                side_effect=[make_response(503), make_json_response(200, {"items": []})],
            ),
            self.assertLogs("kinozal_scraper.http_retry", level="WARNING") as logs,
            # Recovery itself is asserted by the pipeline's own TestFetchRetry; this
            # test owns only the breadcrumb, and a failure to recover must surface as
            # "no log line", not as an exception that never reaches the assertion.
            contextlib.suppress(requests.HTTPError),
        ):
            _fetch_json(_GITHUB_URL, {}, {})

        self.assertEqual(len(logs.output), 1)


if __name__ == "__main__":
    unittest.main()
