"""Tests for authenticated Kinozal mirror access (issue #227).

External boundary mocked: the curl_cffi HTTP session (same pattern as
tests/test_http_fetch.py). The login-success / redirect-to-login detection
logic is NOT mocked — that is the internal behaviour under test (§II).
"""

import unittest
import unittest.mock

from kinozal_auth import KinozalLoginError, fetch_authenticated, login


def _resp(
    status: int = 200, location: str | None = None, text: str = "<html>listing</html>"
) -> unittest.mock.Mock:
    r = unittest.mock.Mock()
    r.status_code = status
    r.headers = {"Location": location} if location else {}
    r.text = text
    r.raise_for_status = unittest.mock.Mock()
    return r


def _session(
    cookies: dict,
    post_resp: unittest.mock.Mock | None = None,
    get_resp: unittest.mock.Mock | None = None,
) -> unittest.mock.Mock:
    """A fake curl_cffi Session. `cookies` is a plain dict (dict(session.cookies)
    is how the production code reads the jar — verified in recon)."""
    s = unittest.mock.Mock()
    s.cookies = cookies
    s.post = unittest.mock.Mock(return_value=post_resp if post_resp is not None else _resp())
    s.get = unittest.mock.Mock(return_value=get_resp if get_resp is not None else _resp())
    return s


class TestKinozalLogin(unittest.TestCase):
    def test_successful_login_returns_session(self) -> None:
        # Non-empty jar after takelogin AND probe top.php returns a listing (no
        # redirect to login). Detect is cookie-name-agnostic — any cookie name.
        sess = _session(cookies={"someauth": "v"}, get_resp=_resp(status=200))
        with unittest.mock.patch("kinozal_auth.requests.Session", return_value=sess):
            result = login("user", "pass")
        self.assertIs(result, sess)

    def test_empty_jar_after_takelogin_raises(self) -> None:
        # Bad creds → server returns the login page with no Set-Cookie → jar empty.
        sess = _session(cookies={})
        with (
            unittest.mock.patch("kinozal_auth.requests.Session", return_value=sess),
            self.assertRaises(KinozalLoginError),
        ):
            login("user", "wrong")

    def test_probe_redirect_to_login_raises(self) -> None:
        # Jar non-empty, but the top.php probe still 302s to login.php (e.g. the
        # m=5 VIP gate). Must NOT be reported as a successful login.
        sess = _session(
            cookies={"someauth": "v"},
            get_resp=_resp(status=302, location="//kinozal.guru/login.php?m=5"),
        )
        with (
            unittest.mock.patch("kinozal_auth.requests.Session", return_value=sess),
            self.assertRaises(KinozalLoginError),
        ):
            login("user", "pass")

    def test_login_posts_credentials_to_takelogin(self) -> None:
        sess = _session(cookies={"someauth": "v"}, get_resp=_resp(status=200))
        with unittest.mock.patch("kinozal_auth.requests.Session", return_value=sess):
            login("alice", "secret")
        sess.post.assert_called_once()
        args, kwargs = sess.post.call_args
        url = args[0] if args else kwargs.get("url", "")
        self.assertTrue(url.endswith("/takelogin.php"), url)
        self.assertEqual(kwargs["data"]["username"], "alice")
        self.assertEqual(kwargs["data"]["password"], "secret")


class TestAuthenticatedFetch(unittest.TestCase):
    def test_redirect_to_login_raises_login_error(self) -> None:
        # A gated response must raise a distinct error, NOT silently return the
        # login-page HTML (which would extract 0 items — §IV silent skip).
        sess = _session(
            get_resp=_resp(status=302, location="//kinozal.guru/login.php?m=5"), cookies={}
        )
        with self.assertRaises(KinozalLoginError):
            fetch_authenticated(sess, "https://kinozal.guru/top.php")

    def test_authenticated_fetch_returns_listing_html(self) -> None:
        sess = _session(get_resp=_resp(status=200, text="<html>top listing</html>"), cookies={})
        html = fetch_authenticated(sess, "https://kinozal.guru/top.php")
        self.assertEqual(html, "<html>top listing</html>")

    def test_unexpected_non_login_redirect_raises(self) -> None:
        # A 3xx that is NOT a login redirect (e.g. maintenance) must not slip
        # through raise_for_status (which only fires on 4xx/5xx) and return an
        # empty body silently → 0 items (§IV).
        sess = _session(
            get_resp=_resp(status=302, location="//kinozal.guru/maintenance.php"), cookies={}
        )
        with self.assertRaises(KinozalLoginError):
            fetch_authenticated(sess, "https://kinozal.guru/top.php")


if __name__ == "__main__":
    unittest.main()
