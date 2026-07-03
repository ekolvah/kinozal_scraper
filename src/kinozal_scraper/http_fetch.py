"""Единая точка HTML-fetch для всех пайплайнов.

Использует curl_cffi с браузерным TLS-фингерпринтом (impersonate), чтобы
Cloudflare-fronted источники не отдавали 403 на JA3/JA4-handshake (issue #217).
"""

from __future__ import annotations

from curl_cffi import requests


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
    resp = requests.get(url, impersonate="chrome", timeout=30)
    resp.raise_for_status()
    return resp.text


def fetch_bytes(url: str) -> bytes:
    """Binary sibling of fetch_html for downloading assets (e.g. posters).

    Same browser TLS fingerprint, so Cloudflare-fronted image hosts (issue
    #225, same gating as #217) return 200 instead of 403. We download posters
    our side and upload them to Telegram as multipart, because `sendPhoto`-by-URL
    is fetched by Telegram's own servers, which Cloudflare blocks.

    Guards against the #265 anti-hotlink trap: a 200 `text/html` response is NOT
    image bytes → `NotAnImageError`. This is a **blocklist** (`text/html`), not an
    `image/*` allowlist — posters live on a long tail of uploader hosts that may
    serve exotic content-types, and blocking the one observed garbage class
    (viewer page) avoids false-positives on a valid image with an odd type.
    """
    resp = requests.get(url, impersonate="chrome", timeout=30)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "text/html":
        raise NotAnImageError(url, content_type, resp.content)
    return resp.content
