"""Единая точка HTML-fetch для всех пайплайнов.

Использует curl_cffi с браузерным TLS-фингерпринтом (impersonate), чтобы
Cloudflare-fronted источники не отдавали 403 на JA3/JA4-handshake (issue #217).
"""

from __future__ import annotations

from typing import Any

from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# Transient HTTP responses worth retrying rather than crashing the source on the
# first blip. NOTE the deliberate divergence from sheets_storage._TRANSIENT_CODES
# (sheets_storage.py:18-21), which EXCLUDES 403 as a fail-fast permission fault:
# here 403 is an anti-bot / WAF challenge (soldoutticketbox.com, #306) — proven
# transient (a 200 three minutes later on the same commit), NOT a permission
# fault — so it IS retried. The two sibling layers treat 403 oppositely on
# purpose; don't "unify" them.
_TRANSIENT_HTTP_CODES = frozenset({403, 429, 500, 502, 503, 504})


def _is_transient_http_error(exc: BaseException) -> bool:
    # Key off the authoritative HTTP status carried by the raised HTTPError.
    # curl_cffi raise_for_status raises HTTPError(msg, 0, response), so exc.response
    # is the Response and exc.response.status_code is a real int (reality-anchored
    # in test_http_fetch.py::test_predicate_matches_real_curl_cffi_httperror).
    return (
        isinstance(exc, HTTPError)
        and getattr(exc.response, "status_code", None) in _TRANSIENT_HTTP_CODES
    )


# 4 attempts / max=30 (vs sheets' 5 / max=60): every curl_cffi source runs in the
# same CI job, so cap total worst-case stall — ~2+4+8≈14 s per source on a full
# outage, still surfaced red after give-up (reraise=True → §IV visible anomaly).
_retry_transient_http = retry(
    retry=retry_if_exception(_is_transient_http_error),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=30),
    reraise=True,
)


@_retry_transient_http
def _get(url: str, **kwargs: Any) -> requests.Response:
    """Single curl_cffi GET + raise_for_status, retrying transient 403/429/5xx."""
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp


class NotAnImageError(Exception):
    """A 200 response that is HTML, not the image we asked for (issue #265).

    Anti-hotlink hosts (e.g. fastpic.org) answer a bare image URL with a 200
    `text/html` viewer page instead of the JPEG. `raise_for_status()` passes it,
    so without a content-type check `fetch_bytes` would hand ~300 KB of HTML back
    as "poster bytes" (→ Telegram `sendPhoto` 400 → poster silently dropped).

    Carries `url`, the actual `content_type`, and the already-downloaded `body`
    so a resolver can extract the direct signed image link from the viewer page
    WITHOUT a second GET of the same 300 KB page (runtime tokens/traffic; keeps
    the signed-link `expires` window tight)."""

    def __init__(self, url: str, content_type: str, body: bytes) -> None:
        super().__init__(f"{url} returned {content_type!r}, not an image")
        self.url = url
        self.content_type = content_type
        self.body = body


def fetch_html(url: str) -> str:
    return _get(url, impersonate="chrome", timeout=30).text


def fetch_bytes(url: str) -> bytes:
    """Binary sibling of fetch_html for downloading assets (e.g. posters).

    Same browser TLS fingerprint, so Cloudflare-fronted image hosts (issue
    #225, same gating as #217) return 200 instead of 403. We download posters
    our side and upload them to Telegram as multipart, because `sendPhoto`-by-URL
    is fetched by Telegram's own servers, which Cloudflare blocks.

    Requests the asset AS an image — an `<img>`-style `Accept: image/*` (#296).
    curl_cffi's chrome-impersonate default sends a *navigation* Accept
    (`text/html,...`), and content-negotiating hosts (imageban.ru, fastpic) answer
    that with a 200 `text/html` landing page instead of the JPEG → the poster is
    lost. The header is passed to `requests.get` as an override: curl_cffi merges
    it by key over the impersonate profile, so UA / Sec-Ch-Ua / TLS fingerprint
    (the #217/#225 403-avoidance) stay intact — only `Accept` changes.

    Guards against the #265 anti-hotlink trap: a 200 `text/html` response is NOT
    image bytes → `NotAnImageError`. With the image `Accept` above this is now
    **defense-in-depth** for the rare host that still returns HTML — a blocklist
    (`text/html`), not an `image/*` allowlist, since posters live on a long tail
    of uploader hosts that may serve valid images with exotic content-types.
    """
    # NotAnImageError is raised BELOW, after _get returns — outside the retry
    # wrapper: a 200 text/html anti-hotlink page is a content problem, not a
    # transient, so it must not be retried.
    resp = _get(
        url,
        impersonate="chrome",
        timeout=30,
        headers={"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
    )
    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "text/html":
        raise NotAnImageError(url, content_type, resp.content)
    return resp.content
