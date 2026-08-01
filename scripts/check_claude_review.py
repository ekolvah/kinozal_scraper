#!/usr/bin/env python3
"""Turn trusted PR-review evidence into the required GitHub check.

The workflow invoking this script is `pull_request_target` and checks out the
default branch.  Never move that verifier back onto the PR head: a review
controller cannot safely decide that its own edited implementation is valid.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

_OUTCOME = re.compile(
    r"<!-- claude-review-outcome: sha=(?P<head_sha>[0-9a-f]{40}) "
    r"outcome=(?P<outcome>clean|rework|blocking) -->"
)
_BOOTSTRAP = re.compile(r"<!-- review-controller-bootstrap: sha=(?P<head_sha>[0-9a-f]{40}) -->")
_CLAUDE_AUTHOR = "claude"
# The current repository owner performs the exceptional, visible controller
# review. Changing this set is itself a review-controller change.
_BOOTSTRAP_MAINTAINERS = frozenset({"ekolvah"})
_REVIEW_CONTROLLER_PATHS = frozenset(
    {
        ".github/workflows/claude-review.yml",
        ".github/workflows/agent-review-gate.yml",
        "scripts/check_claude_review.py",
    }
)


def outcome_from_comments(comments: Sequence[Mapping[str, Any]], head_sha: str) -> str | None:
    """Return the latest Claude outcome bound to exactly ``head_sha``."""
    matched: list[str] = []
    for comment in comments:
        author = comment.get("user")
        body = comment.get("body")
        if not isinstance(author, Mapping) or author.get("login") != _CLAUDE_AUTHOR:
            continue
        if not isinstance(body, str):
            continue
        for marker in _OUTCOME.finditer(body):
            if marker["head_sha"] == head_sha:
                matched.append(marker["outcome"])
    return matched[-1] if matched else None


def bootstrap_approved(comments: Sequence[Mapping[str, Any]], head_sha: str) -> bool:
    """Return whether a configured maintainer approved this exact controller head."""
    for comment in comments:
        author = comment.get("user")
        body = comment.get("body")
        if not isinstance(author, Mapping) or author.get("login") not in _BOOTSTRAP_MAINTAINERS:
            continue
        if isinstance(body, str) and any(
            marker["head_sha"] == head_sha for marker in _BOOTSTRAP.finditer(body)
        ):
            return True
    return False


def controller_changed(changed_paths: Sequence[str]) -> bool:
    """Whether this PR changes the trusted review-controller surface."""
    return bool(_REVIEW_CONTROLLER_PATHS.intersection(changed_paths))


def decision_from_evidence(
    comments: Sequence[Mapping[str, Any]], changed_paths: Sequence[str], head_sha: str
) -> str | None:
    """Return ``bootstrap`` or the current-head Claude outcome; otherwise ``None``."""
    if controller_changed(changed_paths):
        return "bootstrap" if bootstrap_approved(comments, head_sha) else None
    return outcome_from_comments(comments, head_sha)


def _fetch_api(repo: str, endpoint_suffix: str) -> list[Mapping[str, Any]]:
    endpoint = f"repos/{repo}/{endpoint_suffix}?per_page=100"
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
    records = [record for page in pages for record in page]
    if not all(isinstance(record, Mapping) for record in records):
        raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")
    return records


def fetch_comments(repo: str, pr: int) -> list[Mapping[str, Any]]:
    """Read PR conversation comments, failing visibly on transport errors."""
    return _fetch_api(repo, f"issues/{pr}/comments")


def fetch_changed_paths(repo: str, pr: int) -> list[str]:
    """Read every changed path so controller changes cannot hide in pagination."""
    records = _fetch_api(repo, f"pulls/{pr}/files")
    paths: list[str] = []
    for record in records:
        path = record.get("filename")
        if not isinstance(path, str):
            raise RuntimeError(
                f"gh api repos/{repo}/pulls/{pr}/files returned an unexpected payload shape"
            )
        paths.append(path)
    return paths


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    try:
        comments = fetch_comments(args.repo, args.pr)
        changed_paths = fetch_changed_paths(args.repo, args.pr)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    decision = decision_from_evidence(comments, changed_paths, args.head_sha)
    if decision in {"clean", "bootstrap"}:
        print(f"ok: trusted review evidence is {decision}")
        return
    if decision in {"rework", "blocking"}:
        print(
            f"error: Claude review reported {decision} findings; resolve and re-run review.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if controller_changed(changed_paths):
        print(
            "error: review-controller change needs a configured maintainer's "
            "exact-head bootstrap marker.",
            file=sys.stderr,
        )
    else:
        print(
            "error: Claude review did not post a valid current-head outcome marker.",
            file=sys.stderr,
        )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
