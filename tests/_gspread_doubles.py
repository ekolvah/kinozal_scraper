"""Factories for gspread error doubles built from *real* ``requests.Response``
objects — never hand-stubbed response fields.

Why a dedicated module instead of ad-hoc ``MagicMock`` per test: the retry fix
in #289 went green in tests yet crashed in prod (#298) because its double set
``exc.code`` from a hand-stubbed JSON body, while a real Google edge/load-balancer
5xx ships an **HTML** body → gspread can't parse JSON and sets ``exc.code = -1``.
The double idealised the failure shape in exactly the dimension that broke.

These factories build a genuine ``requests.Response`` and let *gspread itself*
compute ``code``/``status`` from it (don't-mock-what-you-don't-own): the double
structurally cannot diverge from real gspread behaviour. Making the realistic
double the path of least resistance is the point — a forcing mechanism, not a
prose reminder (#302).
"""

from __future__ import annotations

import json as _json

import gspread.exceptions

# make_response lives in the generic _http_doubles module (it builds a plain
# requests.Response, nothing gspread-specific); re-exported here so callers that
# already import it from _gspread_doubles keep working.
from _http_doubles import make_response

__all__ = ["api_error_html", "api_error_json", "make_response"]


def api_error_json(status_code: int, message: str = "") -> gspread.exceptions.APIError:
    """A Sheets *API* error carrying a structured JSON body (its own 4xx/5xx).

    gspread parses the body → ``exc.code == status_code`` and
    ``exc.response.status_code == status_code`` (the two agree, as for a real API
    error). Built from a real Response, not a MagicMock, so the parse is gspread's.
    """
    body = _json.dumps({"error": {"code": status_code, "message": message}}).encode("utf-8")
    return gspread.exceptions.APIError(
        make_response(status_code, body=body, content_type="application/json")
    )


def api_error_html(status_code: int, html: str = "") -> gspread.exceptions.APIError:
    """An edge/load-balancer error whose body is **HTML**, not JSON.

    gspread's ``response.json()`` raises → ``exc.code == -1``, but
    ``exc.response.status_code`` holds the real transport status (the shape that
    slipped past #289's code-based retry filter).
    """
    return gspread.exceptions.APIError(
        make_response(
            status_code, body=html.encode("utf-8"), content_type="text/html; charset=UTF-8"
        )
    )
