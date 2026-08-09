"""Single HTML-fetch entry point for every pipeline.

Uses curl_cffi with a browser TLS fingerprint (`impersonate`) so Cloudflare-fronted
sources do not return 403 for the JA3/JA4 handshake (issue #217).

**Measurement #358 (2026-07-25)—do not reopen verified work.** Production cron
received 403 from soldoutticketbox.com for seven days. With the same `curl_cffi`, a
residential IP with `impersonate="chrome"` returned 200 and without it 403;
`chrome124`/`chrome131` also returned 200, while GitHub Actions datacenter IPs returned
403. The block body has neither `cdn-cgi/challenge-platform` nor Turnstile. Therefore
the TLS fingerprint fix (#217) works, pinning a newer impersonation does not help, and
Playwright is useless: this is a flat WAF rejection, not a JS challenge. Datacenter IP
reputation (bot score) requires different egress. `describe_block` records headers/body
because choosing a remedy without them would be guesswork.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError

from kinozal_scraper.http_retry import retry_antibot_http, retry_antibot_patient

logger = logging.getLogger(__name__)

# Headers worth logging on a block, by name — an explicit whitelist, NOT a raw
# header dump: a dump would push `set-cookie` (session ids) into public CI logs.
_DIAGNOSTIC_HEADERS = ("cf-ray", "cf-mitigated")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# Cloudflare footers spell it "Error code: 1020" / "error code 1015". The code is
# the one field that discriminates the fix: 1015 = rate limit (pace the requests),
# 1020 = firewall rule and 1006/1007 = IP ban (only a different egress IP helps).
_CF_CODE_RE = re.compile(r"error\s+code:?\s*(\d{4})", re.IGNORECASE)
_TITLE_LIMIT = 120
_BODY_PREFIX_LIMIT = 200


def _header(headers: Any, name: str) -> str:
    """Case-insensitive header read that tolerates a missing/odd mapping.

    Scan `items()` rather than `.get(name)`: curl_cffi `Headers` is case-insensitive
    itself (verified on a real `Response`), but `describe_block` accepts any mapping,
    so its contract must not depend on the key case supplied by its caller. Total by
    construction (`getattr` + falsy default), never `try/except`; see `describe_block`."""
    items = getattr(headers, "items", None)
    if items is None:
        return ""
    return next((str(v).strip() for k, v in items() if str(k).lower() == name), "")


def describe_block(status_code: int, headers: Any, body: str) -> str:
    """One-line operator-facing evidence for an HTTP failure (#358).

    Collects evidence used to choose an anti-bot-block remedy: status, `cf-ray`
    (an edge request identifier that varies per attempt, hence per-attempt logging),
    `cf-mitigated` (Cloudflare's own classification: `challenge` = managed challenge),
    Cloudflare error code, response `<title>`, and body size.

    Use `<title>`, not body prefix: a real block page starts with roughly 200
    diagnostically empty characters such as `<!DOCTYPE html> <!--[if lt IE 7]>…`,
    while useful text (`Attention Required! | Cloudflare`) is in its document title.
    The body prefix remains fallback for pages without `<title>`.

    **Total by construction and deliberately without `try/except`**: every argument
    may be empty or odd, but all operations (`.get`, regex, slices) tolerate that.
    An `except: return ""` wrapper would violate §IV: failure diagnostics must not
    silently swallow their own failure.
    """
    parts = [str(status_code)]
    parts.extend(
        f"{name}={value}" for name in _DIAGNOSTIC_HEADERS if (value := _header(headers, name))
    )
    text = body or ""
    if cf_code := _CF_CODE_RE.search(text):
        parts.append(f"cf-code={cf_code.group(1)}")
    parts.append(f"len={len(text)}")
    if title := _TITLE_RE.search(text):
        parts.append(f"title={' '.join(title.group(1).split())[:_TITLE_LIMIT]!r}")
    elif prefix := " ".join(text.split())[:_BODY_PREFIX_LIMIT]:
        parts.append(f"body={prefix!r}")
    return " ".join(parts)


def _get_once(url: str, **kwargs: Any) -> requests.Response:
    """Single curl_cffi GET + raise_for_status, WITHOUT the retry wrapper.

    Split out of `_get` (#396) so a single attempt is a plain typed function
    rather than tenacity's dynamic `retry_with(...)`, which mypy cannot see
    (`attr-defined`) and which would need this repo's first `type: ignore`.
    Two retry schedules now wrap this same body — the fast one and soldout's
    patient one — and each wraps it as a plain decorator call for that reason.

    Diagnostics attach to `except HTTPError`, not `if status >= 400`: curl_cffi decides
    what is an error, the happy path remains untouched, and logging is tied to failure.
    Bare `raise` propagates the original exception unchanged (#358: logging is additive,
    not a replacement failure)."""
    resp = requests.get(url, **kwargs)
    try:
        resp.raise_for_status()
    except HTTPError:
        # Use `decode(errors="replace")` rather than `resp.text`: the block-page body is
        # already in memory, and broken encoding must not crash failure diagnostics.
        body = resp.content.decode("utf-8", "replace") if resp.content else ""
        logger.warning(
            "[http_fetch] %s %s", url, describe_block(resp.status_code, resp.headers, body)
        )
        raise
    return resp


# The retrying transport every production call site uses. Applied as a plain call
# rather than `@retry_antibot_http` so the single-attempt function above stays
# importable and typed (#396). The policy itself lives in `http_retry` (#365).
_get = retry_antibot_http(_get_once)

# Same body, patient schedule — soldout only (#396). Kept a separate module-level
# object rather than a per-call `retry_with(...)`: the dynamic form is invisible to
# mypy, and a policy chosen at the call site is a policy nobody can grep for.
_get_patient = retry_antibot_patient(_get_once)


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


# Request parameters live here, in one place, because more than one caller exists:
# `fetch_html` and `fetch_html_patient` differ ONLY in retry schedule, and that
# claim stops being true the moment someone copy-pastes an `impersonate`/`timeout`
# into one of them. Nothing would turn red — hence
# `tests/test_http_fetch.py::TestSharedRequestKwargs` pins them to the call sites.
_HTML_GET: dict[str, Any] = {"impersonate": "chrome", "timeout": 30}
_IMAGE_GET: dict[str, Any] = {
    **_HTML_GET,
    "headers": {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
}


def fetch_html(url: str) -> str:
    return _get(url, **_HTML_GET).text


def fetch_html_patient(url: str) -> str:
    """`fetch_html` for a source behind a probabilistic Cloudflare block (#396).

    Identical request, attempts spread over hours instead of seconds. Used by
    `soldout_pipeline` alone: the block keys off the datacenter IP range, not off
    anything we send, so the only lever left is time. Do NOT reach for this as a
    general "more reliable fetch" — it costs a job slot for ~4.6 h and its numbers
    were measured against one host (`docs/adr/0002-soldout-cloudflare-spread-retries.md`).
    """
    return _get_patient(url, **_HTML_GET).text


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
    resp = _get(url, **_IMAGE_GET)
    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "text/html":
        raise NotAnImageError(url, content_type, resp.content)
    return resp.content
