"""Общий дом политики ретрая транзиентных HTTP-ответов — два набора кодов (#365).

Три прод-транспорта поднимают **разные** классы `HTTPError` (`curl_cffi` и stdlib
`requests` — иерархии без общего предка), но политика «что считать транзиентным»
у них одна и должна меняться в одном месте. Предикат строится по кортежу классов,
а не по наличию атрибута `.response`: duck-typing здесь ловил бы чужие исключения,
которые случайно несут такой атрибут.

**Почему наборов два, а не один.** Разница ровно в 403/429:

- `ANTIBOT_TRANSIENT_CODES` — HTML-транспорт за Cloudflare (`http_fetch`). Здесь 403 —
  это анти-бот-челлендж, и его транзиентность **замерена** (#306: 200 три минуты
  спустя на том же коммите), поэтому он ретраится.
- `API_TRANSIENT_CODES` — JSON-API (GitHub Search, Steam Store). Здесь 403/429 — это
  rate limit, у которого есть собственное окно сброса: GitHub
  [документирует](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
  «You should not retry your request until after the time specified by the
  `x-ratelimit-reset` header» и прямо предупреждает, что «continuing to make requests
  while you are rate limited may result in the banning of your integration».
  Backoff в 1/2/4 s это окно не закрывает — он лишь добавляет три холостых запроса в
  тот же счётчик. Уважение `Retry-After` — отдельная задача; пока источники делают
  один-два запроса за прогон, дешевле не ретраить вовсе и показать отказ (§IV).
  **Для Steam Store это решение по аналогии, а не по источнику:** публичного
  контракта у `appdetails` нет, окно сброса не документировано и не замерено —
  разница записана в `coverage-gaps.md` **M2**, чтобы не читаться как замер.

Оба набора намеренно расходятся с `sheets_storage._TRANSIENT_CODES`, который
исключает 403 как fail-fast permission-fault: три соседних слоя трактуют 403
по-разному **осознанно**, «унифицировать» их нельзя.

Ретраятся только HTTP-**ответы**. Сетевые ошибки (`Timeout`/`ConnectionError`) не
доходят до `raise_for_status`, поэтому предикат пропускает их по конструкции —
принятая граница, записанная пробелом **M** в `coverage-gaps.md` (§V: не ретраим
то, чего не наблюдали).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

import requests
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# The two HTTPError hierarchies in use. `curl_cffi` mirrors requests' exception
# names but derives from its own `CurlError`, so neither isinstance check covers
# the other — a single-class predicate would silently retry one transport only.
_HTTP_ERRORS = (CurlHTTPError, requests.HTTPError)

ANTIBOT_TRANSIENT_CODES = frozenset({403, 429, 500, 502, 503, 504})
API_TRANSIENT_CODES = frozenset({500, 502, 503, 504})

# 4 attempts / max=30 (vs sheets' 5 / max=60): every source runs in the same CI job,
# so the worst-case stall stays capped. With `wait_exponential(multiplier=1)` the
# sleeps are 1 s, 2 s, 4 s — three of them for four attempts, so ~7 s per call site,
# NOT the ~14 s the pre-#365 comment claimed (the 8 s wait belongs to a fifth attempt
# that `stop_after_attempt(4)` never makes). Pinned by
# `test_http_retry.py::TestPolicyParity::test_give_up_sleeps_one_two_four`, so the
# figure the M2 argument rests on cannot drift back into prose-only.
# After give-up `reraise=True` hands the error to the per-source guard → §IV.
_MAX_ATTEMPTS = 4


def _transient_http_predicate(codes: Iterable[int]) -> Callable[[BaseException], bool]:
    """Build the "is this worth retrying" predicate for a given set of statuses.

    Keys off the authoritative HTTP status carried by the raised `HTTPError`. An
    exception without a usable `.response` (a hand-raised `HTTPError`, a network
    error) is **not** transient — reading the status must degrade to False, never
    to an AttributeError inside tenacity's retry decision.
    """
    allowed = frozenset(codes)

    def _is_transient(exc: BaseException) -> bool:
        if not isinstance(exc, _HTTP_ERRORS):
            return False
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status in allowed

    return _is_transient


# The two decorators are spelled out rather than built by a shared factory: a
# factory has to declare its own return type, and any annotation broad enough to
# hold tenacity's decorator erases the wrapped function's signature (mypy then
# reports `no-any-return` at every call site). The repeated kwargs are the price of
# keeping `_get(...)` typed as returning a `Response` — same reason `_get_once`
# stays split out in `http_fetch` (#396).

# HTML transport: `_get_once` already logs `describe_block` on every attempt (#358),
# so a second per-attempt line here would only duplicate it.
retry_antibot_http = retry(
    retry=retry_if_exception(_transient_http_predicate(ANTIBOT_TRANSIENT_CODES)),
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, max=30),
    reraise=True,
)


def retry_antibot_patient(fn: Callable[..., object]) -> Callable[..., object]:
    """RED-заглушка (#396) — реализация приходит GREEN-коммитом."""
    raise NotImplementedError


# JSON APIs: nothing else logs an attempt, and without a line per retry a flapping
# source stays invisible until it dies outright — the retry would hide exactly the
# degradation it was added to survive (§IV).
retry_api_http = retry(
    retry=retry_if_exception(_transient_http_predicate(API_TRANSIENT_CODES)),
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, max=30),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
