"""Shared home for the transient-HTTP response retry policy — two code sets (#365).

Three production transports raise **different** `HTTPError` classes (`curl_cffi` and
stdlib `requests` have hierarchies with no common ancestor), but they share one policy for
what is transient and it must change in one place. The predicate uses a tuple of classes,
not the presence of a `.response` attribute: duck typing here would catch unrelated
exceptions that happen to carry that attribute.

**Why two sets rather than one.** They differ exactly at 403/429:

- `ANTIBOT_TRANSIENT_CODES` is the Cloudflare-backed HTML transport (`http_fetch`). Here,
  403 is an anti-bot challenge, and its transience was **measured** (#306: 200 three minutes
  later on the same commit), so it is retried.
- `API_TRANSIENT_CODES` is for JSON APIs (GitHub Search, Steam Store). Here 403/429 are
  rate limits with their own reset window: GitHub
  [documents](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
  “You should not retry your request until after the time specified by the
  `x-ratelimit-reset` header” and explicitly warns that “continuing to make requests
  while you are rate limited may result in the banning of your integration”. A 1/2/4-second
  backoff does not close that window — it only adds three futile requests to the same
  counter. Honouring `Retry-After` is a separate task; while sources make one or two
  requests per run, it is cheaper to not retry at all and show the failure (§IV).
  **For Steam Store this decision is by analogy, not by source evidence:** `appdetails`
  has no public contract, its reset window is neither documented nor measured — the
  difference is recorded as **M2** in `coverage-gaps.md` so it is not read as a measurement.

**There are also two schedules, orthogonal to the code sets.** The anti-bot set operates in
two modes: fast (all sources) and patient (soldout only, #396) — attempts there are minutes
apart because the block is probabilistic and compressed retries hit the same Cloudflare
decision. The rationale for the numbers is with the policy itself below.

Both sets deliberately diverge from `sheets_storage._TRANSIENT_CODES`, which excludes 403 as
a fail-fast permission fault: three neighbouring layers treat 403 differently **by design**;
they must not be “unified”.

Only HTTP **responses** are retried. Network errors (`Timeout`/`ConnectionError`) do not reach
`raise_for_status`, so the predicate excludes them by construction — an accepted boundary,
recorded as gap **M** in `coverage-gaps-ingestion.md` (§V: do not retry what was not observed).
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
    wait_fixed,
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

# HTML transport, fast schedule — every source except soldout. `_get_once` already
# logs `describe_block` on every attempt (#358), and at 1/2/4 s sleeps a `before_sleep`
# line would land in the same second as that one, so it is omitted **here**; the
# patient sibling below sleeps for minutes and does need it.
retry_antibot_http = retry(
    retry=retry_if_exception(_transient_http_predicate(ANTIBOT_TRANSIENT_CODES)),
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, max=30),
    reraise=True,
)

# HTML transport, patient schedule — soldout only (#396). Same code set, different
# spacing: Cloudflare blocks GitHub Actions' datacenter ranges probabilistically, and
# what decides a run is not the NUMBER of attempts but their spread. Four attempts
# inside ~7 s (the schedule above) hit one and the same CF decision and amount to a
# single throw per run — that is what made 12 consecutive nightly runs red. Attempts
# 60 s apart were measured to behave independently.
#
# 24 × 720 s is the intersection of three constraints: the pause sits well above the
# measured 60 s, the window (~4.6 h) stays under the hard 6-hour GitHub Actions job
# ceiling, and the density equals the 24 requests/day the measurement itself ran at.
# Full reasoning: `docs/adr/0002-soldout-cloudflare-spread-retries.md`.
_PATIENT_ATTEMPTS = 24
_PATIENT_WAIT_S = 720

# `before_sleep` is required here and not above: twelve minutes of silence between
# `_get_once` lines is indistinguishable from a hung step (§IV).
retry_antibot_patient = retry(
    retry=retry_if_exception(_transient_http_predicate(ANTIBOT_TRANSIENT_CODES)),
    stop=stop_after_attempt(_PATIENT_ATTEMPTS),
    wait=wait_fixed(_PATIENT_WAIT_S),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


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
