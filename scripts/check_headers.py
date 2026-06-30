#!/usr/bin/env python3
"""Presence-lint: every source .py under src/ must carry a module docstring.

The docstring is the canonical answer to "what question does this file
answer" (it lives with the code, read just-in-time when the file is opened).
This gate guarantees that canon never gaps; it deliberately does NOT generate
or check the project-map (a generated copy of the docstring would be a second
source of drift). A syntactically broken file is a loud anomaly (raised, then
exit 1), never silently skipped (§IV).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _is_source(path: Path) -> bool:
    name = path.name
    return not (name.startswith("test_") or name == "conftest.py")


def source_files(root: Path) -> list[str]:
    """Source `*.py` paths under `root`, **recursively** (sans test_*/conftest),
    relative to root, sorted. Recursive so the scan descends into the
    `kinozal_scraper` package, not just the bare src/ top level — a
    non-recursive glob over src/ after the package move would scan nothing and
    pass silently (#237 B1). Pure: returns [] on an empty root; the no-op-gate
    guard lives in `run()`."""
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if _is_source(p) and "__pycache__" not in p.parts
    )


def missing_docstrings(root: Path) -> list[str]:
    """Return source `*.py` files under `root` lacking a non-empty module
    docstring, sorted. Raises SyntaxError on an unparseable file — a loud
    anomaly, not a silent skip (§IV)."""
    missing: list[str] = []
    for rel in source_files(root):
        path = root / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        doc = ast.get_docstring(tree)
        if not (doc and doc.strip()):
            missing.append(rel)
    return missing


def run(root: Path) -> int:
    """Gate: 0 if every source file carries a docstring, 1 otherwise. An empty
    scan is a loud failure, not a clean pass — guards against the gate going
    silently green on an empty/mis-pointed root (§IV no-op gate, #237 B1)."""
    if not source_files(root):
        print(f"header gate scanned no source .py under {root} — silent no-op (§IV)")
        return 1
    try:
        missing = missing_docstrings(root)
    except SyntaxError as exc:
        print(f"unparseable file (fix the syntax error first): {exc.filename}: {exc.msg}")
        return 1
    if missing:
        print("source .py files missing a module docstring (the canonical answer):")
        for name in missing:
            print(f"  {name}")
        return 1
    print(f"ok: all source .py under {root} carry a module docstring")
    return 0


def main() -> None:
    sys.exit(run(Path("src")))


if __name__ == "__main__":
    main()
