"""Единая точка HTML-fetch для всех пайплайнов.

Использует curl_cffi с браузерным TLS-фингерпринтом (impersonate), чтобы
Cloudflare-fronted источники не отдавали 403 на JA3/JA4-handshake (issue #217).
"""

from __future__ import annotations

from curl_cffi import requests


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
    """
    resp = requests.get(url, impersonate="chrome", timeout=30)
    resp.raise_for_status()
    return resp.content
