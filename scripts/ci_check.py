#!/usr/bin/env python3
"""Mirrors the CI quality job. Run before every commit: python scripts/ci_check.py"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_LEGACY = {"scraper.py", "TelegramChannelSummarizer.py", "crypto.py"}
_EXCLUDE_DIRS = {".venv", ".git", "__pycache__", ".audit-tmp"}


def _run(cmd: list[str]) -> None:
    if subprocess.run(cmd).returncode != 0:
        sys.exit(1)


def _find_modules() -> list[str]:
    return [
        str(p)
        for p in Path(".").rglob("*.py")
        if not (_EXCLUDE_DIRS & set(p.parts))
        and not any(part.startswith("pytest-cache-files-") for part in p.parts)
        and p.name not in _LEGACY
    ]


def main() -> None:
    print("==> ruff format")
    _run([sys.executable, "-m", "ruff", "format", "--check", "."])

    print("==> ruff lint")
    _run([sys.executable, "-m", "ruff", "check", "."])

    print("==> pytest")
    _run([sys.executable, "-m", "pytest"])

    print("==> mypy")
    modules = _find_modules()
    if modules:
        _run([sys.executable, "-m", "mypy"] + modules)
    else:
        print("No modules to type-check; skipping.")

    print("==> all checks passed")


if __name__ == "__main__":
    main()
