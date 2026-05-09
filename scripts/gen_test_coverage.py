#!/usr/bin/env python3
"""Regenerate docs/architecture/test-coverage.md from pytest --co -q output."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_OUT = Path("docs/architecture/test-coverage.md")

_MODULES_WITHOUT_TESTS = """\
## Modules without dedicated tests

| Module | Reason | Mitigation |
|---|---|---|
| `youtube.py` | No Protocol boundary, requires live YouTube API | Indirect coverage via `test_kinozal_pipeline.py` trailer tests |
| `text_utils.py` | Small utility | Indirect coverage via `test_kinozal_pipeline.py::TestTitleYearMatches` |
| `TelegramChannelSummarizer.py` | Legacy, excluded from linting | None |
| `crypto.py` | Legacy, excluded from linting | None |
| `telegram_summarizer.py` | Legacy entry point (wiring only) | None |
| `scripts/ci_check.py` | Meta-tooling | None |"""

_TEST_PATTERNS = """\
## Test patterns

- **Protocol doubles**: `InMemoryStorage`, `InMemoryNotifier`, `NullEnricher` — inject via constructor, assert on state after pipeline run
- **No mocks of internal functions**: call `run_*_pipeline()` directly with Protocol doubles — see [testing.md](testing.md)
- **Class naming**: `Test<Feature>` groups related assertions (e.g., `TestDeduplication`, `TestWriteBeforeNotify`)
- **Pipeline test structure**: build config dict → inject in-memory doubles → call `run_*_pipeline()` → assert on doubles' recorded calls"""


def _pytest_cmd() -> list[str]:
    probe = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True)
    if probe.returncode == 0:
        return [sys.executable, "-m", "pytest"]
    binary = shutil.which("pytest")
    if binary:
        return [binary]
    raise RuntimeError("pytest not found on PATH or as a Python module")


def _collect() -> list[str]:
    result = subprocess.run(
        _pytest_cmd() + ["--co", "-q"],
        capture_output=True,
        text=True,
    )
    return (result.stdout + result.stderr).splitlines()


def _parse(lines: list[str]) -> list[tuple[str, str, int, int]]:
    by_file: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"tests": set(), "classes": set()}
    )
    for line in lines:
        line = line.strip()
        if "::" not in line:
            continue
        parts = line.split("::")
        if len(parts) < 2:
            continue
        file_path = parts[0]
        if not file_path.endswith(".py"):
            continue
        by_file[file_path]["tests"].add("::".join(parts[1:]))
        if len(parts) >= 3:
            by_file[file_path]["classes"].add(parts[1])

    rows = []
    for file_path, data in by_file.items():
        name = Path(file_path).name
        module = re.sub(r"^test_", "", name)
        rows.append((name, module, len(data["tests"]), len(data["classes"])))

    return sorted(rows, key=lambda r: (-r[2], r[0]))


def _inventory(rows: list[tuple[str, str, int, int]], total: int) -> str:
    lines = [
        f"## Inventory ({total} tests)",
        "",
        "| Test file | Module under test | Tests | Classes |",
        "|---|---|---|---|",
    ]
    for name, module, tests, classes in rows:
        lines.append(f"| `{name}` | `{module}` | {tests} | {classes} |")
    return "\n".join(lines)


def main() -> None:
    raw = _collect()
    test_ids = [ln.strip() for ln in raw if "::" in ln and not ln.strip().startswith("=")]
    total = len(test_ids)
    rows = _parse(test_ids)

    content = "\n\n".join(
        [
            "# Test coverage map",
            _inventory(rows, total),
            _MODULES_WITHOUT_TESTS,
            _TEST_PATTERNS,
        ]
    )
    _OUT.write_text(content + "\n")
    print(f"Updated {_OUT} ({total} tests)")


if __name__ == "__main__":
    main()
