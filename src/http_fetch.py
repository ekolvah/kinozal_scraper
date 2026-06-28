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
