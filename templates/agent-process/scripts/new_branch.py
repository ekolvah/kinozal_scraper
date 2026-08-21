#!/usr/bin/env python3
"""Start a new feature branch from a fresh main.

Usage: python scripts/new_branch.py issue-N-short-slug

Steps: refuse if working tree is dirty → checkout main → pull --ff-only
→ prune merged [gone] branches → checkout -b <name>. Ensures every
issue-* branch starts at origin/main HEAD so squash-merges don't cause
history divergence, and prunes local `[gone]` branches left by merged and
deleted PRs.
"""

from __future__ import annotations

import subprocess
import sys

PROTECTED_BRANCHES = frozenset({"main", "master"})

BRANCH_PREFIX = "issue-"


def is_valid_branch_name(name: str) -> bool:
    """A new branch must carry the project prefix (canon: `agent-process.md`)."""
    return name.startswith(BRANCH_PREFIX)


def _run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, check=True, text=True, capture_output=capture, encoding="utf-8")
    # `stdout=None` used to normalize to `""` as a Windows quirk. It is actually
    # a symptom of a decoding reader that died, so preserve it as capture failure.
    # With requested capture, None now means genuine capture failure,
    # and normalization would replace it with emptiness: `_prune_gone_branches`
    # would report “pruned: 0 merged branches,” indistinguishable from an honest “nothing
    # to delete,” although the branch list was never obtained.
    # Without `capture`, None is normal: output goes to the console and needs no decoding.
    if capture and result.stdout is None:
        raise RuntimeError(f"capture failed for `{' '.join(cmd)}` (rc={result.returncode})")
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
            # Check here rather than in `_run`: this call intentionally bypasses its seam,
            # which fixes `check=True`, while `git branch -d` may legitimately
            # fail for an unmerged branch. Without it, failed capture would cause
            # `AttributeError` instead of diagnostics.
            detail = "capture failed" if result.stderr is None else result.stderr.strip()
            skipped += 1
            print(f"warn: kept {branch} ({detail})", file=sys.stderr)
    print(f"pruned: {pruned} merged branches (skipped {skipped} unmerged)")


def create_branch(name: str) -> None:
    """Create branch `name` from a fresh origin/main HEAD.

    Validates the prefix, refuses a dirty tree or an already-existing branch,
    syncs main (checkout + ff-only pull), prunes merged `[gone]` branches, then
    branches. Every failure path raises (SystemExit via `sys.exit`, or
    CalledProcessError from `_run(check=True)`), so a caller driving this
    in-process — `issue_branch.py` — surfaces the failure as a non-zero exit
    instead of silently continuing (§IV visibility).
    """
    if not is_valid_branch_name(name):
        print(
            f"error: branch name must start with {BRANCH_PREFIX!r} (got {name!r})", file=sys.stderr
        )
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


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/new_branch.py <branch-name>", file=sys.stderr)
        sys.exit(2)
    create_branch(sys.argv[1])


if __name__ == "__main__":
    main()
