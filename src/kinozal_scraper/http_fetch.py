"""Единая точка HTML-fetch для всех пайплайнов.

Использует curl_cffi с браузерным TLS-фингерпринтом (impersonate), чтобы
Cloudflare-fronted источники не отдавали 403 на JA3/JA4-handshake (issue #217).

**Замер #358 (2026-07-25) — что уже проверено, не переоткрывать.** Прод-крон
7 дней подряд ловил 403 на soldoutticketbox.com. Инструментальная проверка тем же
`curl_cffi`: локально (residential IP) `impersonate="chrome"` → 200, без него → 403,
`chrome124`/`chrome131` → те же 200; в CI (датацентровый IP GitHub Actions) → 403.
В теле блока нет ни `cdn-cgi/challenge-platform`, ни turnstile. Отсюда: TLS-фингерпринт
(#217) исправен, **пин свежей impersonate-версии ничего не даёт**, **Playwright бесполезен**
(решать нечего — это плоский WAF-отказ, а не JS-челлендж). Причина — репутация
датацентрового IP (bot score), лечится только другим egress'ом. Поэтому здесь живёт
`describe_block`: без заголовков/тела ответа выбор лечения был бы гаданием.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

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

    Сканирует `items()`, а не `.get(name)`: curl_cffi `Headers` регистронезависим
    сам (проверено на реальном `Response`), но `describe_block` типизирован под
    любой mapping, и его контракт не должен зависеть от того, в каком регистре
    ключи положил вызывающий. Total by construction (`getattr` + falsy-default),
    never `try/except` — see `describe_block`."""
    items = getattr(headers, "items", None)
    if items is None:
        return ""
    return next((str(v).strip() for k, v in items() if str(k).lower() == name), "")


def describe_block(status_code: int, headers: Any, body: str) -> str:
    """One-line operator-facing evidence for an HTTP failure (#358).

    Собирает то, по чему выбирается лечение анти-бот-блока: статус, `cf-ray`
    (идентификатор запроса на edge — разный на каждой попытке, поэтому строка
    пишется per-attempt), `cf-mitigated` (собственная классификация Cloudflare:
    `challenge` = managed challenge), Cloudflare error code, `<title>` ответа и
    размер тела.

    `<title>`, а не префикс тела: у настоящей блок-страницы первые ~200 символов —
    `<!DOCTYPE html> <!--[if lt IE 7]>…`, то есть диагностически пусты, а полезное
    (`Attention Required! | Cloudflare`) лежит в заголовке документа. Префикс тела
    остаётся fallback'ом для страниц без `<title>`.

    **Тотальна по конструкции и намеренно без `try/except`**: любой аргумент может
    быть пустым/странным, но все операции (`.get`, regex, слайсы) на этом не падают.
    Обёртка `except: return ""` здесь была бы §IV-нарушением — диагностика сбоя,
    которая сама молча глотает сбой.
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
    `scripts/probe.py` measures the probability of ONE attempt — the retry
    masks it, turning the measurement into "did we get lucky at least once".

    Диагностика висит на `except HTTPError`, а не на `if status >= 400`: «что есть
    ошибка» решает сам curl_cffi, happy-path не трогается, и лог привязан к точке
    отказа. `raise` без аргументов — исключение уходит наружу неизменным (#358: лог
    additive, не подменяет сбой)."""
    resp = requests.get(url, **kwargs)
    try:
        resp.raise_for_status()
    except HTTPError:
        # decode(errors="replace") вместо resp.text: тело блок-страницы уже прочитано
        # в память, а битая кодировка не должна ронять диагностику сбоя.
        body = resp.content.decode("utf-8", "replace") if resp.content else ""
        logger.warning(
            "[http_fetch] %s %s", url, describe_block(resp.status_code, resp.headers, body)
        )
        raise
    return resp


# The retrying transport every production call site uses. Applied as a plain call
# rather than `@_retry_transient_http` so the single-attempt function above stays
# importable and typed (#396).
_get = _retry_transient_http(_get_once)


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


# Request parameters live here, in one place, because a second caller now exists:
# the #396 measurement in `scripts/probe.py` is only meaningful while it hits the
# site exactly the way prod does. Copy-pasted values would let the two drift apart
# with nothing turning red — `tests/test_http_fetch.py::TestSharedRequestKwargs`
# pins them to the prod call sites.
_HTML_GET: dict[str, Any] = {"impersonate": "chrome", "timeout": 30}
_IMAGE_GET: dict[str, Any] = {
    **_HTML_GET,
    "headers": {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
}


def fetch_html(url: str) -> str:
    return _get(url, **_HTML_GET).text


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
