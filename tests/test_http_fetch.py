import unittest
import unittest.mock

from http_fetch import fetch_html


class TestFetchHtml(unittest.TestCase):
    """fetch_html is the single HTML transport: curl_cffi with a browser TLS
    fingerprint (impersonate) so Cloudflare-fronted sources (issue #217) don't
    403 on the JA3/JA4 handshake."""

    def test_passes_impersonate_chrome(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.text = "<html></html>"
        with unittest.mock.patch("http_fetch.requests.get", return_value=mock_resp) as mget:
            fetch_html("https://example.com")
        mget.assert_called_once()
        _, kwargs = mget.call_args
        self.assertEqual(kwargs.get("impersonate"), "chrome")
        self.assertEqual(kwargs.get("timeout"), 30)

    def test_returns_response_text(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.text = "<html>hi</html>"
        with unittest.mock.patch("http_fetch.requests.get", return_value=mock_resp):
            self.assertEqual(fetch_html("https://example.com"), "<html>hi</html>")

    def test_raises_on_http_error(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.raise_for_status.side_effect = RuntimeError("403")
        with unittest.mock.patch("http_fetch.requests.get", return_value=mock_resp):
            with self.assertRaises(RuntimeError):
                fetch_html("https://example.com")


if __name__ == "__main__":
    unittest.main()
