"""RED-step signature stub for the shared transient-HTTP retry policy (#365).

Encodes today's behaviour — no retry at all — so the tests in
`tests/test_http_retry.py` execute against the real gap instead of dying on an
import error (`commands/implement.md` step 3, the #402 contract). Replaced by the
real policy in the GREEN commit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

ANTIBOT_TRANSIENT_CODES: frozenset[int] = frozenset()
API_TRANSIENT_CODES: frozenset[int] = frozenset()


def transient_http_predicate(codes: Iterable[int]) -> Callable[[BaseException], bool]:
    """Stub: nothing is transient yet."""
    _ = codes
    return lambda exc: False


def retry_antibot_http[F: Callable[..., Any]](fn: F) -> F:
    """Stub: hands the function back unwrapped."""
    return fn


def retry_api_http[F: Callable[..., Any]](fn: F) -> F:
    """Stub: hands the function back unwrapped."""
    return fn
