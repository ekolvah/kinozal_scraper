#!/usr/bin/env python3
"""Confirm that a set of pytest paths are all failing (RED step).

Usage: python scripts/check_red.py <path-or-nodeid> [<path-or-nodeid> ...]

Exits 0 only when the given tests are RED: 0 passed AND (failed >= 1 OR
errors >= 1). Used by `/implement` to gate the RED→GREEN transition: if
the freshly-written tests already pass, the test plan does not cover the
intended behaviour change and `/implement` must abort.
"""

from __future__ import annotations

import re
import subprocess
import sys


def parse_pytest_summary(output: str) -> tuple[int, int, int]:
    """Extract (passed, failed, errors) from pytest stdout summary line."""
    passed = failed = errors = 0
    for line in reversed(output.splitlines()):
        if "passed" not in line and "failed" not in line and "error" not in line:
            continue
        if not line.lstrip().startswith("="):
            continue
        for count, label in re.findall(r"(\d+)\s+(passed|failed|errors?|error)", line):
            n = int(count)
            if label == "passed":
                passed = n
            elif label == "failed":
                failed = n
            elif label.startswith("error"):
                errors = n
        break
    return passed, failed, errors


def red_status(passed: int, failed: int, errors: int) -> tuple[bool, str]:
    if passed == 0 and (failed >= 1 or errors >= 1):
        return True, f"RED: {failed} failed, {errors} errors, 0 passed"
    if passed == 0 and failed == 0 and errors == 0:
        return False, "no tests collected (0 passed, 0 failed, 0 errors)"
    return False, f"not RED: {passed} passed (expected 0), {failed} failed, {errors} errors"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_red.py <path> [<path> ...]", file=sys.stderr)
        sys.exit(2)
    paths = sys.argv[1:]
    cmd = [sys.executable, "-m", "pytest", "--tb=no", "-q", *paths]
    completed = subprocess.run(cmd, text=True, capture_output=True)
    stdout = (completed.stdout or "") + (completed.stderr or "")
    passed, failed, errors = parse_pytest_summary(stdout)
    ok, msg = red_status(passed, failed, errors)
    print(msg)
    if not ok:
        print("--- pytest output ---", file=sys.stderr)
        print(stdout, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
