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


def missing_docstrings(root: Path) -> list[str]:
    """Return root-level `*.py` files (sans test_*/conftest) lacking a non-empty
    module docstring, sorted. Raises SyntaxError on an unparseable file —
    a broken file is a loud anomaly, not a silent skip (§IV)."""
    missing: list[str] = []
    for path in sorted(root.glob("*.py")):
        if not _is_source(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        doc = ast.get_docstring(tree)
        if not (doc and doc.strip()):
            missing.append(str(path.relative_to(root)))
    return missing


def main() -> None:
    root = Path("src")
    try:
        missing = missing_docstrings(root)
    except SyntaxError as exc:
        print(f"unparseable file (fix the syntax error first): {exc.filename}: {exc.msg}")
        sys.exit(1)
    if missing:
        print("src/ source .py files missing a module docstring (the canonical answer):")
        for name in missing:
            print(f"  {name}")
        sys.exit(1)
    print(f"ok: all src/ source .py carry a module docstring ({root.resolve().name})")


if __name__ == "__main__":
    main()
