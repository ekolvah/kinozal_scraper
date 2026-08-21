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
`passed` (as observed in prior executions). The report yields outcome PER TEST, eliminating the
class of externally-green-only tests. Do not simplify this back to summary parsing.

**Output is budgeted.** The answer is one verdict, but it used to grow with the
suite: naming 96 green tests cost 7 699 characters, re-sent on every later call of the
agent session. Names are now sampled and the pytest dump is tailed; every cut states
what it dropped and names `--full`, which restores the whole thing. The cap is safe
because the count and the sample already decide the next action, and the invocation that
produces hundreds of names—a whole path instead of the new node ids—is the thing the
message asks the operator to fix.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Child `<testcase>` elements, each of which means the test is not green.
# `skipped` is included: a skipped test checked nothing, so treating it as green
# would block the RED step on nonexistent coverage.
_NOT_GREEN = frozenset({"failure", "error", "skipped"})

# ~12 names at the measured ~80 characters per node id: enough that the usual case
# (a handful of accidentally green new tests) is never sampled at all, while a whole-suite
# run costs ~1k characters instead of ~13k.
_NAME_LIMIT = 12

# Dump budgets per branch, because the diagnostics are not where the dump is longest.
# The gate runs pytest with `--tb=no`, so on a not-RED verdict the dump is a progress
# map whose informative end is a few lines (measured: 202 characters over two green
# files). When the report itself could not be evaluated, the pytest output is the ONLY
# evidence of what happened, so that branch keeps a much larger tail.
_TAIL_NOT_RED = 800
_TAIL_UNEVALUATED = 4000


def _sample(names: list[str], *, full: bool) -> str:
    """Name the tests, capped: the count is the verdict, the names are the example.

    The remainder is not silently dropped—it is counted, and the hint names the two
    ways to get it: re-run on the node ids that were actually written (which is what
    the operator usually wants), or ask for the whole list.
    """
    if full or len(names) <= _NAME_LIMIT:
        return ", ".join(names)
    hidden = len(names) - _NAME_LIMIT
    return (
        f"{', '.join(names[:_NAME_LIMIT])} (+{hidden} more; re-run on the node ids of your "
        "new tests, or add --full for the whole list)"
    )


def _tail(text: str, max_chars: int, *, full: bool) -> str:
    """Keep the end of the pytest output, announcing the cut before what survived.

    The tail, because with `-ra` the short summary that names the failing tests is at
    the end. The marker goes first: an anomaly placed after 800 characters of progress
    map is an anomaly nobody reads (§IV).
    """
    if full or len(text) <= max_chars:
        return text
    return f"[cut {len(text) - max_chars} chars of pytest output; re-run with --full]\n{text[-max_chars:]}"


def evaluate_report(xml_text: str, *, full: bool = False) -> tuple[bool, str]:
    """RED-step verdict from a junit report. Pure function: no I/O or exit.

    RED := no green tests, no test errored, AND at least one failed. For `not RED`,
    name the offending tests; otherwise “not RED: 1 passed” does not identify which
    test already passes or did not execute (§IV). Above `_NAME_LIMIT` the naming is a
    count plus a sample, and `full=True` restores the complete list.

    **`error` is not RED.** Collection/fixture error means the test did not run,
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
        also = f"; зелёные рядом: {_sample(green, full=full)}" if green else ""
        return False, (
            f"not RED: {len(errored)} тест(ов) не выполнились (ошибка сбора/фикстуры) "
            f"of {total}: {_sample(errored, full=full)}{also} — почини их, RED по ним не засчитан"
        )
    if green:
        return False, f"not RED: {len(green)} green test(s) of {total}: {_sample(green, full=full)}"
    if failed:
        return True, f"RED: {len(failed)} failed, 0 green (of {total} tests)"
    skipped = sum(1 for t in tags_by_test.values() if "skipped" in t)
    return False, f"not RED: 0 green, but nothing failed either ({skipped} skipped of {total})"


def main() -> None:
    # argparse rather than hand-parsed `sys.argv`: `--full` must not reach pytest as a
    # path, and a missing path must stay exit 2 ("usage error"), which `parser.error`
    # already produces. The 0/1/2 codes are this gate's carrier—do not widen them.
    parser = argparse.ArgumentParser(
        description="Confirm that the given pytest paths are all failing (RED step)."
    )
    parser.add_argument("paths", nargs="+", metavar="path", help="test path or node id")
    parser.add_argument(
        "--full",
        action="store_true",
        help="print every test name and the whole pytest output instead of a capped digest",
    )
    args = parser.parse_args(sys.argv[1:])
    paths = args.paths
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "red.xml"
        # No `-q` here: the verbosity of this run has one home, `addopts` in
        # pyproject.toml. `-q` is `action="count"`, so a second one would silently push this
        # subprocess to verbosity −2.
        cmd = [sys.executable, "-m", "pytest", "--tb=no", f"--junitxml={report}", *paths]
        completed = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        if completed.stdout is None or completed.stderr is None:
            # Capture failed. Code 2 means “gate broken,” not 1: replacing it
            # with an empty string would parse a report with no pytest output and print
            # empty diagnostics on failure.
            print(
                f"check_red: capture failed for pytest (rc={completed.returncode}): "
                f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        stdout = completed.stdout + completed.stderr
        try:
            ok, msg = evaluate_report(report.read_text(encoding="utf-8"), full=args.full)
        except (OSError, ValueError) as exc:
            # The gate could not compute. This is NOT RED: silently calling it “red”
            # would allow GREEN from an unread report (§IV/§VI). Code 2 distinguishes
            # “gate broken” from “tests are not red” (1).
            print(f"check_red: cannot evaluate the junit report: {exc}", file=sys.stderr)
            print("--- pytest output ---", file=sys.stderr)
            print(_tail(stdout, _TAIL_UNEVALUATED, full=args.full), file=sys.stderr)
            sys.exit(2)
    print(msg)
    if not ok:
        print("--- pytest output ---", file=sys.stderr)
        print(_tail(stdout, _TAIL_NOT_RED, full=args.full), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
