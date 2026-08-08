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

The mapping below is not divination: `AGENTS.md` § Code Review Rules tells the
reviewer to request changes only for a blocking finding and to comment otherwise,
so the review state is the severity the reviewer was asked to express.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence

from scripts.check_claude_review_outcome import VALID_OUTCOMES

# Verified against the live API (`gh api apps/chatgpt-codex-connector` → owner
# `openai`), not inferred from the product name: a wrong login here would read
# every Codex review as absent and time the carrier out on every run.
CODEX_REVIEWER = "chatgpt-codex-connector[bot]"
REVIEW_REQUEST = "@codex review"
STATE_OUTCOMES = {
    "APPROVED": "clean",
    "COMMENTED": "rework",
    "CHANGES_REQUESTED": "blocking",
}
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_POLL_SECONDS = 20


def _gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() if result.stderr else "no stderr captured"
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    if result.stdout is None:
        raise RuntimeError(f"gh {' '.join(args)}: no stdout captured (broken decoding)")
    return result.stdout


def _records(reviews: object) -> list[Mapping[str, object]]:
    """Flatten `--slurp` pages and refuse any other shape (§IV, fail loud)."""
    if not isinstance(reviews, list):
        raise RuntimeError(f"unexpected reviews payload shape: {type(reviews).__name__}")
    pages = reviews if all(isinstance(page, list) for page in reviews) else [reviews]
    records = [record for page in pages for record in page]
    if not all(isinstance(record, Mapping) for record in records):
        raise RuntimeError("unexpected reviews payload shape: a review is not an object")
    return records


def find_verdict(reviews: object, head_sha: str, reviewer: str) -> str | None:
    """Return the outcome carried by `reviewer`'s review of `head_sha`, if any.

    The last matching review wins: Codex re-reviews on request, and its latest
    word on this head is the one the maintainer sees.
    """
    verdict: str | None = None
    for record in _records(reviews):
        user = record.get("user")
        login = user.get("login") if isinstance(user, Mapping) else None
        if login != reviewer or record.get("commit_id") != head_sha:
            continue
        outcome = STATE_OUTCOMES.get(str(record.get("state")))
        if outcome is not None:
            verdict = outcome
    return verdict


def _fetch_reviews(repository: str, pr_number: str) -> object:
    endpoint = f"repos/{repository}/pulls/{pr_number}/reviews?per_page=100"
    raw = _gh(["api", endpoint, "--paginate", "--slurp"])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api {endpoint} returned invalid JSON: {exc}") from exc


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
    """Request a review once, then wait for it until `timeout_seconds` elapses.

    The first read happens before the request: automatic review is a repository
    setting, so the carrier may have already answered for this head, and asking
    again would spend a review of the subscription's budget to learn nothing.
    """
    wait = sleep or time.sleep
    clock = monotonic or time.monotonic

    verdict = find_verdict(_fetch_reviews(repository, pr_number), head_sha, reviewer)
    if verdict is not None:
        return verdict

    _gh(["pr", "comment", pr_number, "--repo", repository, "--body", REVIEW_REQUEST])
    print(f"requested a review from {reviewer} on {head_sha}")

    deadline = clock() + timeout_seconds
    while clock() < deadline:
        wait(poll_seconds)
        verdict = find_verdict(_fetch_reviews(repository, pr_number), head_sha, reviewer)
        if verdict is not None:
            return verdict
    return None


class _Options:
    def __init__(self) -> None:
        self.repository = ""
        self.pr_number = ""
        self.head_sha = ""
        self.reviewer = CODEX_REVIEWER
        self.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.poll_seconds = DEFAULT_POLL_SECONDS


def _parse_options(argv: Sequence[str]) -> _Options:
    options = _Options()
    args = list(argv)
    while args:
        option = args.pop(0)
        if not args:
            print(f"error: expected a value after {option}", file=sys.stderr)
            raise SystemExit(2)
        value = args.pop(0)
        if option == "--repo":
            options.repository = value
        elif option == "--pr":
            options.pr_number = value
        elif option == "--head-sha":
            options.head_sha = value
        elif option == "--reviewer":
            options.reviewer = value
        elif option == "--timeout-seconds":
            options.timeout_seconds = int(value)
        elif option == "--poll-seconds":
            options.poll_seconds = int(value)
        else:
            print(f"error: unexpected argument {option}", file=sys.stderr)
            raise SystemExit(2)
    missing = [
        name
        for name, value in (
            ("--repo", options.repository),
            ("--pr", options.pr_number),
            ("--head-sha", options.head_sha),
        )
        if not value
    ]
    if missing:
        print(f"error: missing required argument(s): {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)
    return options


def _publish(payload: str) -> None:
    line = f"payload={payload}"
    print(line)
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    try:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except OSError as exc:
        print(f"error: cannot write GITHUB_OUTPUT {destination!r}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main(argv: Sequence[str] | None = None) -> None:
    """Publish carrier 2's verdict as the payload the enforcement step reads.

    Exit code 0 either way, on purpose: the enforcement step is the single place
    that turns an outcome into a check result, and an empty payload already means
    «no verdict» there. Failing here as well would split one verdict across two
    steps and leave the log unable to answer who reviewed this head.
    """
    options = _parse_options(sys.argv[1:] if argv is None else argv)
    verdict = poll_for_verdict(
        options.repository,
        options.pr_number,
        options.head_sha,
        reviewer=options.reviewer,
        timeout_seconds=options.timeout_seconds,
        poll_seconds=options.poll_seconds,
    )
    if verdict is None:
        print(
            f"::warning::{options.reviewer} left no review of {options.head_sha} within "
            f"{options.timeout_seconds}s. Carrier 2 produced no verdict; the enforcement "
            "step below reds the check. Check that Codex code review is enabled for this "
            "repository and that its subscription quota is not exhausted."
        )
        _publish("")
        return
    if verdict not in VALID_OUTCOMES:  # pragma: no cover - guarded by the mapping test
        raise RuntimeError(f"carrier 2 produced an outcome the gate does not know: {verdict!r}")
    _publish(json.dumps({"outcome": verdict}))


if __name__ == "__main__":
    main()
