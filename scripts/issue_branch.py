#!/usr/bin/env python3
"""Create a fresh `codex-issue-N-<slug>` branch from a GitHub issue title.

Usage: python scripts/issue_branch.py <issue-number>

Reads the issue title via `gh issue view`, derives a kebab-case ASCII
slug, and delegates to `scripts/new_branch.py` to do the actual checkout
(which itself guarantees branching from fresh origin/main HEAD).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

MAX_SLUG_WORDS = 4
FALLBACK_SLUG = "task"
_PREFIX_TAG_RE = re.compile(r"^\s*\[[^\]]+\]\s*")


def slugify(title: str) -> str:
    no_tag = _PREFIX_TAG_RE.sub("", title)
    ascii_only = re.sub(r"[^a-zA-Z0-9\s-]", " ", no_tag).lower()
    words = [w for w in re.split(r"[\s-]+", ascii_only) if w]
    if not words:
        return FALLBACK_SLUG
    return "-".join(words[:MAX_SLUG_WORDS])


def build_branch_name(issue_number: int, title: str) -> str:
    return f"codex-issue-{issue_number}-{slugify(title)}"


def _fetch_title(issue_number: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "title,state"],
        check=True,
        text=True,
        capture_output=True,
    )
    stdout = result.stdout or ""
    data = json.loads(stdout)
    if data.get("state") != "OPEN":
        print(
            f"error: issue #{issue_number} is not OPEN (state={data.get('state')})", file=sys.stderr
        )
        sys.exit(2)
    return data.get("title") or ""


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/issue_branch.py <issue-number>", file=sys.stderr)
        sys.exit(2)
    try:
        n = int(sys.argv[1])
    except ValueError:
        print(f"error: issue number must be int (got {sys.argv[1]!r})", file=sys.stderr)
        sys.exit(2)
    title = _fetch_title(n)
    branch = build_branch_name(n, title)
    new_branch = Path(__file__).with_name("new_branch.py")
    subprocess.run([sys.executable, str(new_branch), branch], check=True)


if __name__ == "__main__":
    main()
