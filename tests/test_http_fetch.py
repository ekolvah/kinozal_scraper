import unittest
import unittest.mock

from curl_cffi.requests.exceptions import HTTPError

from kinozal_scraper.http_fetch import NotAnImageError, fetch_bytes, fetch_html


def _transient_resp(status_code: int) -> unittest.mock.Mock:
    """A response whose raise_for_status raises a real curl_cffi HTTPError
    carrying an int .response.status_code — the shape the retry predicate reads."""
    resp = unittest.mock.Mock()
    resp.status_code = status_code
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
        self, _sleep: unittest.mock.Mock
    ) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.http_fetch.requests.get",
                side_effect=lambda *a, **k: _transient_resp(503),
            ) as mget,
            self.assertRaises(HTTPError),
        ):
            fetch_html("https://example.com")
        self.assertEqual(mget.call_count, 4)

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

    def test_predicate_matches_real_curl_cffi_httperror(self) -> None:
        # Reality-anchor (SHOULD-FIX #1, sibling of TestGspreadRetryIntegrationAnchor):
        # the predicate must fire on a HTTPError produced by REAL curl_cffi
        # raise_for_status, not a hand-built Mock — otherwise it can silently
        # mis-read the attr path and 403 crashes prod again (the #298 class).
        from curl_cffi.requests.models import Response

        from kinozal_scraper.http_fetch import _is_transient_http_error

        resp = Response()
        resp.status_code = 403
        resp.ok = False
        with self.assertRaises(HTTPError) as ctx:
            resp.raise_for_status()
        self.assertTrue(_is_transient_http_error(ctx.exception))


if __name__ == "__main__":
    unittest.main()
