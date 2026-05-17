#!/usr/bin/env python3
"""Start a new feature branch from a fresh main.

Usage: python scripts/new_branch.py codex-issue-N-short-slug

Steps: refuse if working tree is dirty → checkout main → pull --ff-only
→ checkout -b <name>. Ensures every codex-* branch starts at origin/main
HEAD so squash-merges don't cause history divergence (see #66).
"""

from __future__ import annotations

import subprocess
import sys


def _run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


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
    _run(["git", "checkout", "-b", name])
    print(f"ready: on {name}, branched from origin/main HEAD")


if __name__ == "__main__":
    main()
