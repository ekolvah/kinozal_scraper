#!/usr/bin/env python3
"""Enforce the repository language policy for documentation text."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class LanguageCheckError(RuntimeError):
    """The language check could not obtain trustworthy evidence."""


@dataclass(frozen=True)
class Violation:
    """One Cyrillic fragment found in documentation text."""

    path: str
    line: int
    kind: str
    text: str


MIGRATION_ALLOWLIST = frozenset({"pending-translation"})


def markdown_violations(text: str, *, path: str = "sample.md") -> list[Violation]:
    """Return language-policy violations in Markdown prose."""
    return []


def python_violations(text: str, *, path: str = "sample.py") -> list[Violation]:
    """Return violations in Python comments and docstrings."""
    return []


def tracked_files(repo: Path) -> list[Path]:
    """Return tracked paths reported by Git."""
    return []


def scoped_files(paths: Iterable[Path]) -> list[Path]:
    """Return tracked Markdown and Python files in policy scope."""
    return []


def missing_expected_areas(paths: Iterable[Path]) -> list[str]:
    """Return expected repository areas absent from the scoped paths."""
    return []
