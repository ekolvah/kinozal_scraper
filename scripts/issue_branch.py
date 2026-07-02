#!/usr/bin/env python3
"""Create a fresh `issue-N-<slug>` branch from a GitHub issue title.

Usage: python scripts/issue_branch.py <issue-number>

Reads the issue title via `gh issue view`, derives a kebab-case ASCII
slug, and delegates to `scripts/new_branch.py` to do the actual checkout
(which itself guarantees branching from fresh origin/main HEAD).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

MAX_SLUG_WORDS = 4
FALLBACK_SLUG = "task"


def slugify(title: str) -> str:
    ascii_only = re.sub(r"[^a-zA-Z0-9\s-]", " ", title).lower()
    words = [w for w in re.split(r"[\s-]+", ascii_only) if w]
    if not words:
        return FALLBACK_SLUG
    return "-".join(words[:MAX_SLUG_WORDS])


def _new_branch_module() -> ModuleType:
    """Load the sibling `new_branch.py` by absolute path and return the module.

    Loaded by absolute file path — NOT `from scripts.new_branch import ...` —
    because the documented CLI `python scripts/issue_branch.py <N>` sets
    `sys.path[0]` to the script's dir (`scripts/`), and the repo root is never
    on `sys.path` (the editable install only adds `src/`). A package import
    would therefore raise `ModuleNotFoundError` at runtime even though
    `scripts/` IS a package (packageness is necessary but not sufficient; tests
    pass only because `python -m pytest` prepends the repo root). The path load
    is immune to `sys.path`, gives a single source of truth for
    `BRANCH_PREFIX`, and lets `main()` call `create_branch` in-process instead
    of re-spawning a second interpreter.
    """
    spec = importlib.util.spec_from_file_location(
        "scripts.new_branch", Path(__file__).with_name("new_branch.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_branch_name(issue_number: int, title: str) -> str:
    return f"{_new_branch_module().BRANCH_PREFIX}{issue_number}-{slugify(title)}"


def _fetch_title(issue_number: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "title,state"],
        check=True,
        text=True,
        capture_output=True,
        encoding="utf-8",
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
    _new_branch_module().create_branch(branch)


if __name__ == "__main__":
    main()
