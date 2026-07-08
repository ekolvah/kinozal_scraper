"""Factories for real ``requests.Response`` doubles — never hand-stubbed MagicMocks.

A ``MagicMock`` HTTP response idealises the response in exactly the dimensions that
bite in prod: ``.headers`` becomes a plain case-*sensitive* ``dict`` (a real
``Response.headers`` is a case-*insensitive* dict, so ``.get("Retry-After")`` finds
a lowercase ``retry-after``), and ``.json()`` returns a hand-fed object instead of
parsing the body. Building a genuine ``Response`` lets the same code paths run as in
prod. This is the generic sibling of ``_gspread_doubles`` (#298 → #302 → #304).
"""

from __future__ import annotations

import json as _json
from collections.abc import Mapping

import requests


def make_response(
    status_code: int,
    *,
    body: bytes = b"",
    content_type: str = "application/octet-stream",
    headers: Mapping[str, str] | None = None,
) -> requests.Response:
    """Build a real ``requests.Response`` with the given status/body/headers.

    ``headers`` land in the real case-insensitive ``Response.headers``, so a
    lookup by any casing resolves the way it does against a live server.
    """
    resp = requests.models.Response()
    resp.status_code = status_code
    resp._content = body
    resp.headers["Content-Type"] = content_type
    if headers:
        resp.headers.update(headers)
    return resp


def make_json_response(
    status_code: int,
    json_body: object,
    *,
    headers: Mapping[str, str] | None = None,
) -> requests.Response:
    """A real ``Response`` whose body is ``json_body`` serialised as JSON.

    ``resp.json()`` actually parses it back — no ``.json.return_value`` stubbing.
    """
    return make_response(
        status_code,
        body=_json.dumps(json_body).encode("utf-8"),
        content_type="application/json",
        headers=headers,
    )
