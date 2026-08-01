"""Tests for `http_fetch.py` — the HTTP boundary shared by every scraper.

Covers browser impersonation and shared request kwargs, image content-type
rules, the transient-vs-permanent retry policy, and the single-line block
diagnostics printed on a 403.
"""

import pathlib
import unittest
import unittest.mock

from curl_cffi.requests.exceptions import HTTPError

from kinozal_scraper.http_fetch import (
    _HTML_GET,
    _IMAGE_GET,
    NotAnImageError,
    describe_block,
    fetch_bytes,
    fetch_html,
    fetch_html_patient,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _transient_resp(
    status_code: int,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> unittest.mock.Mock:
    """A response whose raise_for_status raises a real curl_cffi HTTPError
    carrying an int .response.status_code — the shape the retry predicate reads.

    `headers`/`body` are real values (not Mocks) because the #358 block
    diagnostics read them on the failure path."""
    resp = unittest.mock.Mock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {}
    resp.content = body
    resp.raise_for_status.side_effect = HTTPError(f"HTTP Error {status_code}", 0, resp)
    return resp


def _ok_html(text: str = "<html>ok</html>") -> unittest.mock.Mock:
    resp = unittest.mock.Mock()
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def _ok_image() -> unittest.mock.Mock:
    resp = unittest.mock.Mock()
    resp.content = b"\x89PNG\r\n"
    resp.headers = {"content-type": "image/png"}
    resp.raise_for_status.return_value = None
    return resp


class TestFetchHtml(unittest.TestCase):
    """fetch_html is the single HTML transport: curl_cffi with a browser TLS
    fingerprint (impersonate) so Cloudflare-fronted sources (issue #217) don't
    403 on the JA3/JA4 handshake."""

    def test_passes_impersonate_chrome(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.text = "<html></html>"
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=mock_resp
        ) as mget:
            fetch_html("https://example.com")
        mget.assert_called_once()
        _, kwargs = mget.call_args
        self.assertEqual(kwargs.get("impersonate"), "chrome")
        self.assertEqual(kwargs.get("timeout"), 30)

    def test_returns_response_text(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.text = "<html>hi</html>"
        with unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", return_value=mock_resp):
            self.assertEqual(fetch_html("https://example.com"), "<html>hi</html>")

    def test_raises_on_http_error(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.raise_for_status.side_effect = RuntimeError("403")
        with (
            unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", return_value=mock_resp),
            self.assertRaises(RuntimeError),
        ):
            fetch_html("https://example.com")


class TestFetchBytes(unittest.TestCase):
    """fetch_bytes is the binary sibling of fetch_html: same curl_cffi browser
    TLS fingerprint so Cloudflare-fronted image hosts (issue #225, same gating
    as #217) return 200 instead of 403. Used to download posters our side and
    upload them to Telegram as multipart, because sendPhoto-by-URL is fetched
    by Telegram's own servers, which Cloudflare blocks."""

    def test_passes_impersonate_chrome_and_returns_content(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.content = b"\x89PNG\r\n"
        mock_resp.headers = {"content-type": "image/png"}
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=mock_resp
        ) as mget:
            result = fetch_bytes("https://example.com/poster.jpg")
        mget.assert_called_once()
        _, kwargs = mget.call_args
        self.assertEqual(kwargs.get("impersonate"), "chrome")
        self.assertEqual(kwargs.get("timeout"), 30)
        self.assertEqual(result, b"\x89PNG\r\n")

    def test_raises_on_http_error(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.raise_for_status.side_effect = RuntimeError("403")
        with (
            unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", return_value=mock_resp),
            self.assertRaises(RuntimeError),
        ):
            fetch_bytes("https://example.com/poster.jpg")

    def test_sends_image_accept_header(self) -> None:
        # #296: fetch_bytes downloads an IMAGE, so it must request one — an
        # `<img>`-style `Accept: image/*`. curl_cffi's chrome-impersonate default
        # sends a *navigation* Accept (text/html,...), and content-negotiating
        # hosts (imageban.ru, fastpic) answer that with a 200 text/html landing
        # page instead of the JPEG (→ poster dropped). Load-bearing invariant:
        # Accept prefers image/*, no text/html priority — assert the prefix, not
        # a byte-exact q-value string.
        mock_resp = unittest.mock.Mock()
        mock_resp.content = b"\xff\xd8\xff\xe0JPEG"
        mock_resp.headers = {"content-type": "image/jpeg"}
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=mock_resp
        ) as mget:
            fetch_bytes("https://i4.imageban.ru/out/2026/07/04/x.jpg")
        _, kwargs = mget.call_args
        self.assertTrue(kwargs["headers"]["Accept"].startswith("image/"))

    def test_raises_not_an_image_on_text_html(self) -> None:
        # #265: a fastpic anti-hotlink viewer page returns 200 text/html (~300 KB).
        # fetch_bytes must NOT hand that HTML back as "poster bytes" — it raises a
        # typed NotAnImageError carrying url + content-type + the already-downloaded
        # body (so the resolver reuses it without a second GET).
        body = b"<html><title>FastPic viewer</title></html>"
        url = "https://i126.fastpic.org/big/x.jpg"
        mock_resp = unittest.mock.Mock()
        mock_resp.content = body
        mock_resp.headers = {"content-type": "text/html"}
        with (
            unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", return_value=mock_resp),
            self.assertRaises(NotAnImageError) as ctx,
        ):
            fetch_bytes(url)
        err = ctx.exception
        self.assertEqual(err.url, url)
        self.assertEqual(err.content_type, "text/html")
        self.assertEqual(err.body, body)

    def test_returns_content_for_image_content_type(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.content = b"\xff\xd8\xff\xe0JPEG"
        mock_resp.headers = {"content-type": "image/jpeg"}
        with unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", return_value=mock_resp):
            result = fetch_bytes("https://example.com/poster.jpg")
        self.assertEqual(result, b"\xff\xd8\xff\xe0JPEG")

    def test_content_type_match_is_case_and_param_insensitive(self) -> None:
        # "text/html; charset=UTF-8" must normalize (strip params, lowercase) to
        # text/html and raise — a real server sends the charset param.
        mock_resp = unittest.mock.Mock()
        mock_resp.content = b"<html></html>"
        mock_resp.headers = {"content-type": "text/html; charset=UTF-8"}
        with (
            unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", return_value=mock_resp),
            self.assertRaises(NotAnImageError),
        ):
            fetch_bytes("https://i126.fastpic.org/big/x.jpg")


class TestBlockDiagnostics(unittest.TestCase):
    """#358: an anti-bot 403 must reach the operator as evidence, not as a bare
    `HTTP Error 403:`. `describe_block` is the pure formatter behind that WARNING —
    what it extracts decides whether the follow-up fix is «pace the requests»
    (1015), «different egress IP» (1020) or «solve a challenge» (cf-mitigated)."""

    def test_reports_status_ray_and_mitigated(self) -> None:
        out = describe_block(
            403,
            {"cf-ray": "a2085f48a9fad625-LCA", "cf-mitigated": "challenge"},
            "<html><title>Just a moment...</title></html>",
        )
        self.assertIn("403", out)
        self.assertIn("a2085f48a9fad625-LCA", out)
        self.assertIn("challenge", out)

    def test_reads_case_insensitive_headers(self) -> None:
        # In prod the mapping is curl_cffi `Headers`, which normalises casing
        # itself (verified against a real Response). This pins the *pure*
        # function's contract instead: it takes `Any` mapping, so the diagnostic
        # must not depend on the casing the caller happened to use.
        out = describe_block(403, {"CF-Ray": "abc123-LCA"}, "")
        self.assertIn("abc123-LCA", out)

    def test_extracts_title_from_real_cloudflare_page(self) -> None:
        # Reality-anchor over the real block page captured 2026-07-25 from
        # soldoutticketbox.com (IP/Ray-ID scrubbed). Load-bearing: the first ~200
        # chars of this page are `<!DOCTYPE html> <!--[if lt IE 7]>…`, i.e. a naive
        # body prefix is diagnostically EMPTY — the signal lives in <title>.
        body = (_FIXTURES / "cloudflare_block_403.html").read_text(encoding="utf-8")
        out = describe_block(403, {"cf-ray": "aaaa-LCA"}, body)
        self.assertIn("Attention Required! | Cloudflare", out)
        self.assertNotIn("DOCTYPE", out)
        self.assertIn(str(len(body)), out)

    def test_extracts_cloudflare_error_code(self) -> None:
        # 1020 (firewall rule → needs a different IP) and 1015 (rate limit → needs
        # pacing) lead to opposite follow-up fixes, so the code must survive into
        # the log line.
        self.assertIn("1020", describe_block(403, {}, "<p>Error code: 1020</p>"))
        self.assertIn("1015", describe_block(429, {}, "<p>error code 1015</p>"))

    def test_falls_back_to_body_prefix_without_title(self) -> None:
        out = describe_block(403, {}, "  Sorry, you have\nbeen blocked  ")
        self.assertIn("Sorry, you have been blocked", out)

    def test_survives_missing_headers_and_empty_body(self) -> None:
        # §IV: the diagnostic is total by construction (.get()/slices), never
        # wrapped in try/except — a non-Cloudflare host with no body must still
        # produce a line instead of masking the failure with a second exception.
        headers: dict[str, str] | None
        for headers in ({}, None):
            with self.subTest(headers=headers):
                out = describe_block(503, headers, "")
                self.assertIn("503", out)

    def test_output_is_single_line_and_bounded(self) -> None:
        out = describe_block(403, {"cf-ray": "x-LCA"}, "line\n" * 60000)
        self.assertNotIn("\n", out)
        self.assertLessEqual(len(out), 400)


class TestFetchRetry(unittest.TestCase):
    """http_fetch retries transient HTTP responses (403 anti-bot / 429 / 5xx)
    instead of crashing the source on the first blip — the HTTP-transport sibling
    of the sheets_storage._net retry layer (#298/#299). Prod incident #306: a
    sporadic WAF 403 from soldoutticketbox.com (proven transient: 200 three
    minutes later) took down the whole soldout pipeline. backoff neutralised via
    patching tenacity's sleep."""

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_html_retries_transient_403_then_succeeds(
        self, _sleep: unittest.mock.Mock
    ) -> None:
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get",
            side_effect=[_transient_resp(403), _ok_html("<html>hi</html>")],
        ) as mget:
            result = fetch_html("https://example.com")
        self.assertEqual(result, "<html>hi</html>")
        self.assertEqual(mget.call_count, 2)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_html_retries_5xx_then_succeeds(self, _sleep: unittest.mock.Mock) -> None:
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get",
            side_effect=[_transient_resp(502), _ok_html("<html>hi</html>")],
        ) as mget:
            result = fetch_html("https://example.com")
        self.assertEqual(result, "<html>hi</html>")
        self.assertEqual(mget.call_count, 2)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_html_no_retry_on_permanent_404(self, _sleep: unittest.mock.Mock) -> None:
        # Preservation guard: a permanent 4xx (not in the transient set) must fail
        # fast — one GET, no retry — so a real config/permission fault surfaces.
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get", side_effect=[_transient_resp(404)]
            ) as mget,
            self.assertRaises(HTTPError),
        ):
            fetch_html("https://example.com")
        self.assertEqual(mget.call_count, 1)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_html_gives_up_after_max_attempts_reraises(
        self, sleep: unittest.mock.Mock
    ) -> None:
        # Характеризация быстрой политики (#396). Расписание пиньется здесь, а не
        # только число попыток: рядом теперь живёт медленный режим, и «поправил
        # заодно и общий» — самая дешёвая по опечатке и самая дорогая по последствиям
        # ошибка. 1+2+4 = ~7 с — то самое окно, из-за которого 4 попытки бьют в одно
        # решение Cloudflare и считаются за один бросок.
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get",
                side_effect=lambda *a, **k: _transient_resp(503),
            ) as mget,
            self.assertRaises(HTTPError),
        ):
            fetch_html("https://example.com")
        self.assertEqual(mget.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_bytes_retries_transient_then_succeeds(self, _sleep: unittest.mock.Mock) -> None:
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get",
            side_effect=[_transient_resp(429), _ok_image()],
        ) as mget:
            result = fetch_bytes("https://example.com/poster.jpg")
        self.assertEqual(result, b"\x89PNG\r\n")
        self.assertEqual(mget.call_count, 2)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_bytes_not_an_image_not_retried(self, _sleep: unittest.mock.Mock) -> None:
        # Preservation guard: a 200 text/html (anti-hotlink viewer, #265) is a
        # content problem, not a transient — NotAnImageError, exactly one GET.
        resp = unittest.mock.Mock()
        resp.content = b"<html></html>"
        resp.headers = {"content-type": "text/html"}
        resp.raise_for_status.return_value = None
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get", return_value=resp
            ) as mget,
            self.assertRaises(NotAnImageError),
        ):
            fetch_bytes("https://i126.fastpic.org/big/x.jpg")
        self.assertEqual(mget.call_count, 1)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_html_logs_diagnostics_on_each_403_attempt(
        self, _sleep: unittest.mock.Mock
    ) -> None:
        # #358: one WARNING per attempt, not one per give-up. Cloudflare answers
        # from a different edge PoP each try, so per-attempt cf-ray is the only
        # way to see whether attempt 4 differed from attempt 1 — which is exactly
        # the evidence the retry-policy follow-up needs.
        attempts = [
            _transient_resp(403, {"cf-ray": f"ray{n}-LCA"}, b"<title>Attention Required!</title>")
            for n in range(4)
        ]
        with (
            unittest.mock.patch("kinozal_scraper.http_fetch.requests.get", side_effect=attempts),
            self.assertLogs("kinozal_scraper.http_fetch", level="WARNING") as logs,
            self.assertRaises(HTTPError),
        ):
            fetch_html("https://www.soldoutticketbox.com/x")
        self.assertEqual(len(logs.output), 4)
        for n in range(4):
            self.assertIn(f"ray{n}-LCA", logs.output[n])

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_diagnostics_does_not_swallow_http_error(self, _sleep: unittest.mock.Mock) -> None:
        # §IV: diagnostics must be additive. A 403 with no cf-* headers and an
        # empty body (non-Cloudflare host) still logs, still raises, still retries
        # the same number of times — the log must not become a silent skip.
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get",
                side_effect=lambda *a, **k: _transient_resp(403),
            ) as mget,
            self.assertLogs("kinozal_scraper.http_fetch", level="WARNING") as logs,
            self.assertRaises(HTTPError),
        ):
            fetch_html("https://example.com")
        self.assertEqual(mget.call_count, 4)
        self.assertEqual(len(logs.output), 4)


class TestPatientHtml(unittest.TestCase):
    """soldout тянет HTML разнесёнными попытками; постеры остаются на быстром пути (#396).

    Разделение существенно: медленная политика умножается на число items, и перевод
    постеров на неё оборвал бы прогон по таймауту вместо того, чтобы его спасти.
    """

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_html_patient_makes_24_attempts_on_403(self, _sleep: unittest.mock.Mock) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get",
                side_effect=lambda *a, **k: _transient_resp(403),
            ) as mget,
            self.assertRaises(HTTPError),
        ):
            fetch_html_patient("https://www.soldoutticketbox.com/x")
        self.assertEqual(mget.call_count, 24)

    def test_fetch_html_patient_uses_the_shared_kwargs(self) -> None:
        # Анти-дрейф: медленный путь обязан ходить теми же kwargs, что быстрый,
        # иначе «то же самое, только терпеливее» тихо перестанет быть правдой.
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=_ok_html()
        ) as mget:
            fetch_html_patient("https://example.com")

        self.assertEqual(mget.call_args.kwargs, _HTML_GET)

    @unittest.mock.patch("tenacity.nap.time.sleep")
    def test_fetch_bytes_stays_on_the_fast_transport(self, _sleep: unittest.mock.Mock) -> None:
        # Preservation guard: самая вероятная ошибка при правке — «перевести заодно
        # и постеры». 24 попытки × 12 мин на каждый item съели бы job целиком.
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get",
                side_effect=lambda *a, **k: _transient_resp(403),
            ) as mget,
            self.assertRaises(HTTPError),
        ):
            fetch_bytes("https://example.com/poster.jpg")
        self.assertEqual(mget.call_count, 4)


class TestSharedRequestKwargs(unittest.TestCase):
    """The request parameters live in one place so a second caller cannot drift
    from prod silently (#396).

    `fetch_html` and `fetch_html_patient` differ ONLY in retry schedule — that is
    the whole claim the patient path rests on, and a copy-pasted
    `impersonate`/`timeout`/`Accept` in either of them would quietly make it false
    with nothing turning red.
    """

    def test_fetch_html_uses_shared_kwargs(self) -> None:
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=_ok_html()
        ) as mget:
            fetch_html("https://example.com")

        self.assertEqual(mget.call_args.kwargs, _HTML_GET)

    def test_fetch_bytes_uses_shared_kwargs(self) -> None:
        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=_ok_image()
        ) as mget:
            fetch_bytes("https://example.com/x.png")

        self.assertEqual(mget.call_args.kwargs, _IMAGE_GET)


if __name__ == "__main__":
    unittest.main()
