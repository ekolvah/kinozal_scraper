"""Authenticated access to the kinozal.guru mirror (issue #227).

kinozal.tv periodically returns HTTP 522 (Cloudflare — origin down); the
kinozal.guru mirror stays up but gates all listing pages (top.php, browse.php,
…) behind a login. This module logs in via takelogin.php and fetches pages
through the authenticated session.

Recon (2026-06-30): POST /takelogin.php with {username, password}, no CSRF
token, no captcha. Bad creds → the login page is returned with no Set-Cookie
(empty cookie jar). A logged-out request to a gated page 302-redirects to
login.php. We therefore detect success cookie-name-agnostically: a non-empty
jar after takelogin AND a top.php probe that does not redirect to login. This
also catches the m=5 / VIP gate (a valid login that still can't see top.php).
"""

from __future__ import annotations

from curl_cffi import requests

_BASE = "https://kinozal.guru"
_TIMEOUT = 30


class KinozalLoginError(Exception):
    """Raised when a Kinozal login fails or a session is not (or no longer)
    authenticated for a gated page. Distinct from a transport/HTTP failure so
    callers surface it as its own visible anomaly (§IV), not a generic fetch
    error or a silent 0-items run."""


def _is_login_redirect(resp: requests.Response) -> bool:
    """A 3xx whose Location points at login.php — the mirror's "not authorised"
    signal for gated pages."""
    location = resp.headers.get("Location", "") or ""
    return 300 <= resp.status_code < 400 and "login.php" in location.lower()


def login(username: str, password: str, *, base: str = _BASE) -> requests.Session:
    """Log into the Kinozal mirror and return an authenticated session.

    Raises KinozalLoginError if credentials are rejected (empty jar) or the
    account cannot reach top.php (probe still redirects to login — e.g. a VIP
    gate)."""
    session: requests.Session = requests.Session(impersonate="chrome", timeout=_TIMEOUT)
    session.post(
        f"{base}/takelogin.php",
        data={"username": username, "password": password},
        allow_redirects=False,
    )
    if not dict(session.cookies):
        raise KinozalLoginError(
            "login rejected — empty cookie jar after takelogin (bad credentials?)"
        )
    probe = session.get(f"{base}/top.php", allow_redirects=False)
    if _is_login_redirect(probe):
        raise KinozalLoginError(
            "logged in but top.php still redirects to login "
            "(account lacks access — VIP gate / m=5?)"
        )
    return session


def fetch_authenticated(session: requests.Session, url: str) -> str:
    """Fetch a page through an authenticated session.

    Raises KinozalLoginError if the response redirects to login (session not
    authenticated / expired) instead of silently returning the login-page HTML,
    which would extract 0 items and read as "no new films" (§IV silent skip)."""
    resp = session.get(url, allow_redirects=False)
    if _is_login_redirect(resp):
        raise KinozalLoginError(f"session not authenticated for {url} (redirected to login)")
    resp.raise_for_status()
    return str(resp.text)
