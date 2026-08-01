"""Turn Claude's explicit PR-review outcome into a deterministic GitHub check."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

_OUTCOME = re.compile(
    r"<!-- claude-review-outcome: run=(?P<run_id>[^ ]+) "
    r"outcome=(?P<outcome>clean|rework|blocking) -->"
)
_AUTHOR = "claude"


# Temporary interfaces for the RED test commit. The implementation follows in
# the next commit, after the new contract has proved it fails against the old gate.
def bootstrap_approved(
    comments: Sequence[Mapping[str, Any]], head_sha: str
) -> bool:
    """Return whether trusted bootstrap evidence exists for this controller head."""
    return False


def decision_from_evidence(
    comments: Sequence[Mapping[str, Any]], changed_paths: Sequence[str], head_sha: str
) -> str | None:
    """Return the review decision for the current head and changed paths."""
    return outcome_from_comments(comments, head_sha)


def fetch_changed_paths(repo: str, pr: int) -> list[str]:
    """Read changed PR paths for the controller-bootstrap decision."""
    return []


def outcome_from_comments(comments: Sequence[Mapping[str, Any]], run_id: str) -> str | None:
    """Return this run's Claude outcome, rejecting missing or malformed evidence."""
    matched: list[str] = []
    for comment in comments:
        author = comment.get("user")
        body = comment.get("body")
        if not isinstance(author, Mapping) or author.get("login") != _AUTHOR:
            continue
        if not isinstance(body, str):
            continue
        for marker in _OUTCOME.finditer(body):
            if marker["run_id"] == run_id:
                matched.append(marker["outcome"])
    return matched[-1] if matched else None


def fetch_comments(repo: str, pr: int) -> list[Mapping[str, Any]]:
    """Read PR conversation comments through gh, failing visibly on transport errors."""
    endpoint = f"repos/{repo}/issues/{pr}/comments?per_page=100"
    result = subprocess.run(
        ["gh", "api", endpoint, "--paginate", "--slurp"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0 or result.stdout is None:
        detail = result.stderr.strip() if result.stderr else "no stderr captured"
        raise RuntimeError(f"gh api {endpoint} failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api {endpoint} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")
    pages = payload if all(isinstance(page, list) for page in payload) else [payload]
    comments = [comment for page in pages for comment in page]
    if not all(isinstance(comment, Mapping) for comment in comments):
        raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")
    return comments


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        outcome = outcome_from_comments(fetch_comments(args.repo, args.pr), args.run_id)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if outcome == "clean":
        print("ok: Claude review reported clean")
        return
    if outcome in {"rework", "blocking"}:
        print(
            f"error: Claude review reported {outcome} findings; resolve and re-run review.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        "error: Claude review did not post a valid outcome marker for this workflow run.",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
