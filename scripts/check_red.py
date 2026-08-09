#!/usr/bin/env python3
"""Confirm that a set of pytest paths are all failing (RED step).

Usage: python scripts/check_red.py <path-or-nodeid> [<path-or-nodeid> ...]

Exits 0 only when the given tests are RED: no test is green AND at least one
failed. Used by the implementer adapter to gate the RED→GREEN transition: if the
freshly-written tests already pass, the test plan does not cover the intended
behaviour change and implementation must abort.

Outcome comes from the **junit report** (`--junitxml`, pytest core plus stdlib parsing),
not run-summary counts: `N failed, M passed` lacks test identity, and `unittest.subTest`
desynchronizes them—a failed subtest appears in `failed` while its parent appears in
`passed` (#400, observed in #380). The report yields outcome PER TEST, eliminating the
class of externally-green-only tests. Do not simplify this back to summary parsing.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Child `<testcase>` elements, each of which means the test is not green.
# `skipped` is included: a skipped test checked nothing, so treating it as green
# would block the RED step on nonexistent coverage.
_NOT_GREEN = frozenset({"failure", "error", "skipped"})


def evaluate_report(xml_text: str) -> tuple[bool, str]:
    """RED-step verdict from a junit report. Pure function: no I/O or exit.

    RED := no green tests, no test errored, AND at least one failed. For `not RED`,
    name the offending tests; otherwise “not RED: 1 passed” does not identify which
    test already passes or did not execute (§IV).

    **`error` is not RED (#402).** Collection/fixture error means the test did not run,
    while RED must prove the test catches behavior; accepting it would let `/implement`
    enter GREEN on an unverified test. It also fixes false RED from a green test with
    broken teardown (which also emits `<error>`).

    **`error` outweighs `failure` within one test.** pytest writes one record PER PHASE,
    so failure plus broken teardown arrives as two `<testcase>` records with one
    `classname`/`name`; group them, then fix the broken harness instead of accepting RED.
    Do not simplify this back.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"junit report is not parsable XML: {exc}") from exc
    # Group by (classname, name): otherwise phase records count as separate tests,
    # misreporting counts and breaking the “error outweighs” classification.
    tags_by_test: dict[tuple[str, str], set[str]] = {}
    for case in root.iter("testcase"):
        key = (case.get("classname", ""), case.get("name", ""))
        tags_by_test.setdefault(key, set()).update(child.tag for child in case)
    if not tags_by_test:
        return False, "no tests collected (0 testcases in the junit report)"

    def name_of(key: tuple[str, str]) -> str:
        return f"{key[0]}::{key[1]}".lstrip(":")

    errored = [name_of(k) for k, t in tags_by_test.items() if "error" in t]
    failed = [name_of(k) for k, t in tags_by_test.items() if "error" not in t and "failure" in t]
    green = [name_of(k) for k, t in tags_by_test.items() if not t & _NOT_GREEN]
    total = len(tags_by_test)
    if errored:
        also = f"; зелёные рядом: {', '.join(green)}" if green else ""
        return False, (
            f"not RED: {len(errored)} тест(ов) не выполнились (ошибка сбора/фикстуры) "
            f"of {total}: {', '.join(errored)}{also} — почини их, RED по ним не засчитан"
        )
    if green:
        return False, f"not RED: {len(green)} green test(s) of {total}: {', '.join(green)}"
    if failed:
        return True, f"RED: {len(failed)} failed, 0 green (of {total} tests)"
    skipped = sum(1 for t in tags_by_test.values() if "skipped" in t)
    return False, f"not RED: 0 green, but nothing failed either ({skipped} skipped of {total})"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_red.py <path> [<path> ...]", file=sys.stderr)
        sys.exit(2)
    paths = sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "red.xml"
        cmd = [sys.executable, "-m", "pytest", "--tb=no", "-q", f"--junitxml={report}", *paths]
        completed = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        if completed.stdout is None or completed.stderr is None:
            # Capture failed (#364). Code 2 means “gate broken,” not 1: replacing it
            # with an empty string would parse a report with no pytest output and print
            # empty diagnostics on failure (#410).
            print(
                f"check_red: capture failed for pytest (rc={completed.returncode}): "
                f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        stdout = completed.stdout + completed.stderr
        try:
            ok, msg = evaluate_report(report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # The gate could not compute. This is NOT RED: silently calling it “red”
            # would allow GREEN from an unread report (§IV/§VI). Code 2 distinguishes
            # “gate broken” from “tests are not red” (1).
            print(f"check_red: cannot evaluate the junit report: {exc}", file=sys.stderr)
            print("--- pytest output ---", file=sys.stderr)
            print(stdout, file=sys.stderr)
            sys.exit(2)
    print(msg)
    if not ok:
        print("--- pytest output ---", file=sys.stderr)
        print(stdout, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
