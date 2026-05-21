#!/usr/bin/env python3
"""Start a new feature branch from a fresh main.

Usage: python scripts/new_branch.py codex-issue-N-short-slug

Steps: refuse if working tree is dirty → checkout main → pull --ff-only
→ prune merged [gone] branches → checkout -b <name>. Ensures every
codex-* branch starts at origin/main HEAD so squash-merges don't cause
history divergence (see #66), and that local `[gone]` branches from
already-merged-and-deleted PRs don't pile up (see #72).
"""

from __future__ import annotations

import subprocess
import sys

PROTECTED_BRANCHES = frozenset({"main", "master"})


def _run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, check=True, text=True, capture_output=capture, encoding="utf-8")
    # Under some Windows + git-bash + pipe-handle combinations, subprocess.run
    # has been observed returning CompletedProcess(stdout=None) despite
    # capture_output=True. Normalize so callers can call `.splitlines()` /
    # `.strip()` without an `if x is None` dance (see #109).
    if capture and result.stdout is None:
        result.stdout = ""
    return result


def _prune_gone_branches() -> None:
    """Delete local branches whose remote-tracking ref is gone (merged & deleted)."""
    _run(["git", "fetch", "--prune"])
    output = _run(["git", "branch", "-vv"], capture=True).stdout

    gone: list[str] = []
    for raw in output.splitlines():
        if ": gone]" not in raw:
            continue
        line = raw.lstrip()
        if line.startswith("* "):
            continue  # current branch — never delete
        parts = line.split()
        if parts:
            gone.append(parts[0])

    pruned = 0
    skipped = 0
    for branch in gone:
        if branch in PROTECTED_BRANCHES:
            continue
        result = subprocess.run(
            ["git", "branch", "-d", branch],
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        if result.returncode == 0:
            pruned += 1
        else:
            skipped += 1
            print(f"warn: kept {branch} ({result.stderr.strip()})", file=sys.stderr)
    print(f"pruned: {pruned} merged branches (skipped {skipped} unmerged)")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/new_branch.py <branch-name>", file=sys.stderr)
        sys.exit(2)
    name = sys.argv[1]
    if not name.startswith("codex-"):
        print(f"error: branch name must start with 'codex-' (got {name!r})", file=sys.stderr)
        sys.exit(2)

    status = _run(["git", "status", "--porcelain"], capture=True).stdout
    if status.strip():
        print("error: working tree is dirty — commit or stash first", file=sys.stderr)
        print(status, file=sys.stderr)
        sys.exit(1)

    existing = _run(["git", "branch", "--list", name], capture=True).stdout.strip()
    if existing:
        print(f"error: branch {name!r} already exists", file=sys.stderr)
        sys.exit(1)

    _run(["git", "checkout", "main"])
    _run(["git", "pull", "--ff-only"])
    _prune_gone_branches()
    _run(["git", "checkout", "-b", name])
    print(f"ready: on {name}, branched from origin/main HEAD")


if __name__ == "__main__":
    main()
