"""Ask carrier 2 for a verdict on the reviewed head and publish it (#478).

Carrier 2 is the Codex code review that runs on GitHub through the ChatGPT
subscription: it is triggered by `@codex review` on the pull request and answers
by posting a review of its own. Unlike carrier 1 it does not execute inside this
runner, so this module is the whole adapter — it asks, waits for a review the
declared reviewer left on *this* head, and translates the review state into the
outcome vocabulary the enforcement step already understands.

Two rules carry the design. A review left on an earlier head is not a verdict on
this one: the diff it read is not the diff being merged. And a carrier that never
answered must leave nothing behind — the enforcement step reds the check on an
empty payload, which is exactly what «no review happened» has to look like (§IV).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

CODEX_REVIEWER = ""
REVIEW_REQUEST = ""
STATE_OUTCOMES: dict[str, str] = {}
DEFAULT_TIMEOUT_SECONDS = 0
DEFAULT_POLL_SECONDS = 0


def find_verdict(reviews: object, head_sha: str, reviewer: str) -> str | None:
    """Return the outcome carried by `reviewer`'s review of `head_sha`, if any."""
    raise NotImplementedError


def poll_for_verdict(
    repository: str,
    pr_number: str,
    head_sha: str,
    *,
    reviewer: str = CODEX_REVIEWER,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> str | None:
    """Request a review once, then wait for it until `timeout_seconds` elapses."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> None:
    """Publish carrier 2's verdict as the payload the enforcement step reads."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
