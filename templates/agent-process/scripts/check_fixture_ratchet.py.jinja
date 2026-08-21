#!/usr/bin/env python3
"""Reject new parser tests that construct external HTML inline."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_HTML_MARKERS = ("<!doctype", "<html", "<body", "<div", "<a ", "<img", "<table", "<select")
_PARSER_MARKERS = ("parse", "extract", "beautifulsoup")

# Nodes that predate this ratchet go here. The ratchet freezes existing debt
# without forcing a repository-wide migration unrelated to the incident that
# motivated it — add an entry only for a pre-existing test, never for a new one.
GRANDFATHERED_NODES: frozenset[str] = frozenset()


def _call_leaf(call: ast.Call) -> str:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id.casefold()
    if isinstance(target, ast.Attribute):
        return target.attr.casefold()
    return ""


def _is_inline_external_parser_test(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    has_inline_html = any(
        isinstance(part, ast.Constant)
        and isinstance(part.value, str)
        and any(marker in part.value.casefold() for marker in _HTML_MARKERS)
        for part in ast.walk(node)
    )
    calls_parser = any(
        isinstance(part, ast.Call) and any(marker in _call_leaf(part) for marker in _PARSER_MARKERS)
        for part in ast.walk(node)
    )
    return has_inline_html and calls_parser


def inline_external_data_test_nodes(source: str, *, path: Path) -> list[str]:
    """Return pytest node IDs for inline-HTML parser tests in one source file."""
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []
    for top_level in tree.body:
        if isinstance(top_level, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if top_level.name.startswith("test_") and _is_inline_external_parser_test(top_level):
                found.append(f"{path.as_posix()}::{top_level.name}")
        elif isinstance(top_level, ast.ClassDef):
            for child in top_level.body:
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name.startswith("test_")
                    and _is_inline_external_parser_test(child)
                ):
                    found.append(f"{path.as_posix()}::{top_level.name}::{child.name}")
    return found


def scan_repository(repo_root: Path) -> list[str]:
    """Report inline external-data tests not present when the ratchet landed."""
    found: list[str] = []
    for path in sorted((repo_root / "tests").glob("test_*.py")):
        relative = path.relative_to(repo_root)
        nodes = inline_external_data_test_nodes(path.read_text(encoding="utf-8"), path=relative)
        found.extend(node for node in nodes if node not in GRANDFATHERED_NODES)
    return found


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    violations = scan_repository(repo_root)
    if violations:
        print("new external-data parsing tests must read captured fixtures:", file=sys.stderr)
        for node in violations:
            print(f"  - {node}", file=sys.stderr)
        raise SystemExit(1)
    print("ok: no new inline external-data parsing tests")


if __name__ == "__main__":
    main()
