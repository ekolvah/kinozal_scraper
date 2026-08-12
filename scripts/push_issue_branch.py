#!/usr/bin/env python3
"""Push only the current clean issue branch to this repository's origin.

Usage: python scripts/push_issue_branch.py

This no-argument entry point is the only push command in the opt-in Codex
delivery allowlist. It derives every sensitive value from Git, validates it,
and never accepts a caller-provided remote, refspec, force flag, or hook bypass.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence

CANONICAL_REMOTES = frozenset(
    {
        "https://github.com/ekolvah/kinozal_scraper.git",
        "git@github.com:ekolvah/kinozal_scraper.git",
        "ssh://git@github.com/ekolvah/kinozal_scraper.git",
    }
)
ISSUE_BRANCH = re.compile(r"^issue-\d+-[a-z0-9]+(?:-[a-z0-9]+)*$")


def push_command(*, remote_url: str, branch: str, worktree_status: str) -> list[str]:
    """Return the sole permitted push command, or reject unsafe local state."""
    if worktree_status.strip():
        raise ValueError("working tree is dirty; commit the issue changes before publication")
    if remote_url not in CANONICAL_REMOTES:
        raise ValueError("origin is not the canonical repository")
    if ISSUE_BRANCH.fullmatch(branch) is None:
        raise ValueError("current branch must match issue-N-<slug>")
    return ["git", "push", "--set-upstream", "origin", branch]


def _capture(command: list[str]) -> str:
    """Run one read-only Git probe and return its captured stdout."""
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.stdout is None or result.stderr is None:
        raise RuntimeError(f"capture failed for `{' '.join(command)}` (rc={result.returncode})")
    if result.returncode != 0:
        detail = result.stderr.strip() or "no stderr"
        raise RuntimeError(f"`{' '.join(command)}` failed (rc={result.returncode}): {detail}")
    return result.stdout.strip()


def main(argv: Sequence[str] | None = None) -> None:
    """Validate repository state and push the derived issue branch."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("Usage: python scripts/push_issue_branch.py", file=sys.stderr)
        raise SystemExit(2)
    try:
        command = push_command(
            remote_url=_capture(["git", "remote", "get-url", "origin"]),
            branch=_capture(["git", "branch", "--show-current"]),
            worktree_status=_capture(["git", "status", "--porcelain"]),
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    result = subprocess.run(command, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
