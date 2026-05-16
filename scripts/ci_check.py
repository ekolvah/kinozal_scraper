#!/usr/bin/env python3
"""Mirrors the CI quality job. Run before every commit: python scripts/ci_check.py"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_LEGACY = {"telegram_summarizer.py", "TelegramChannelSummarizer.py", "crypto.py"}
_EXCLUDE_DIRS = {".venv", ".git", "__pycache__", ".audit-tmp", ".claude"}


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

    print("==> gen_test_coverage")
    _run([sys.executable, "scripts/gen_test_coverage.py"])
    if (
        subprocess.run(
            ["git", "diff", "--exit-code", "docs/architecture/test-coverage.md"]
        ).returncode
        != 0
    ):
        print("docs/architecture/test-coverage.md is out of date — stage it and re-run")
        sys.exit(1)

    print("==> requirements consistency")

    def _parse_pins(path: Path) -> dict[str, str]:
        pins: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([a-zA-Z0-9_.\-\[\]]+)==(.+)$", line.strip())
            if m:
                name = re.sub(r"\[.*\]", "", m.group(1)).lower().replace("-", "_")
                pins[name] = m.group(2)
        return pins

    req = _parse_pins(Path("requirements.txt"))
    dev = _parse_pins(Path("requirements-dev.txt"))
    bad = [(p, req[p], dev[p]) for p in req if p in dev and req[p] != dev[p]]
    if bad:
        print("requirements version mismatch — edit .in files and run pip-compile:")
        for p, rv, dv in bad:
            print(f"  {p}: requirements.txt={rv}, requirements-dev.txt={dv}")
        sys.exit(1)

    print("==> mypy")
    modules = _find_modules()
    if modules:
        _run([sys.executable, "-m", "mypy"] + modules)
    else:
        print("No modules to type-check; skipping.")

    print("==> all checks passed")


if __name__ == "__main__":
    main()
