"""Tests for `scripts/check_red.py` — the RED gate of `/implement`.

Covers the junit verdict rules (subtest failures redden the parent; a collection
error, a skip-only run or an empty report is NOT red), the exit code taken
when the subprocess capture itself breaks, and the output budget: the gate's
answer must stay a fixed size while the suite it runs grows (#533).
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.check_red import evaluate_report, main


def _report(*cases: str) -> str:
    """Wrap prepared `<testcase>` elements in captured pytest 9.0.3 JUnit XML.

    The gate must be testable without running pytest recursively inside pytest.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite name="pytest" tests="{len(cases)}">{"".join(cases)}'
        "</testsuite></testsuites>"
    )


def _case(name: str, *children: str, classname: str = "tests.mod.T") -> str:
    body = "".join(f'<{tag} message="m">text</{tag}>' for tag in children)
    return f'<testcase classname="{classname}" name="{name}">{body}</testcase>'


def _collection_error(module: str) -> str:
    """Return pytest 9.0.3's measured collection-error XML shape.

    Collection errors have an empty `classname`, a dotted module `name`, and an
    `<error>` child. A fictional class would miss the real rendering branch.
    """
    return (
        f'<testcase classname="" name="{module}">'
        '<error message="collection failure">ImportError</error></testcase>'
    )


class TestEvaluateReport:
    def test_subtest_failures_make_parent_not_green(self) -> None:
        # Repro #400: pytest counts the parent as passed, but two `<failure>`
        # children mean the test is not green.
        ok, msg = evaluate_report(_report(_case("test_all_subtests_fail", "failure", "failure")))
        assert ok, msg

    def test_genuinely_green_test_blocks_red(self) -> None:
        # False green is the costliest outcome: name any genuinely green test.
        ok, msg = evaluate_report(
            _report(_case("test_subtests_failed", "failure"), _case("test_really_green"))
        )
        assert not ok
        assert "test_really_green" in msg
        assert "test_subtests_failed" not in msg

    def test_plain_failure_is_red(self) -> None:
        ok, msg = evaluate_report(_report(_case("test_plain_red", "failure")))
        assert ok, msg

    def test_collection_error_alone_is_not_red(self) -> None:
        # #402: a suite that never collected proves nothing and cannot be RED.
        ok, msg = evaluate_report(_report(_collection_error("tests.broken_module")))
        assert not ok
        assert "tests.broken_module" in msg
        assert "0 failed" not in msg

    def test_error_beside_real_failure_is_not_red(self) -> None:
        # “The others are red” is not an argument: nothing is still known about the
        # unexecuted portion of the suite.
        ok, msg = evaluate_report(
            _report(_collection_error("tests.broken_module"), _case("test_plain_red", "failure"))
        )
        assert not ok
        assert "tests.broken_module" in msg

    def test_phase_split_entries_are_one_test(self) -> None:
        # pytest writes one record PER PHASE: a failed test with broken teardown produces
        # TWO <testcase> entries with the same classname/name (measured). Without grouping,
        # one test is counted twice; `error` outweighs `failure`—broken teardown is a defect
        # to fix, not confirmed RED.
        ok, msg = evaluate_report(_report(_case("test_x", "failure"), _case("test_x", "error")))
        assert not ok
        assert "test_x" in msg

    def test_errored_and_green_are_both_named(self) -> None:
        ok, msg = evaluate_report(
            _report(_collection_error("tests.broken_module"), _case("test_really_green"))
        )
        assert not ok
        assert "tests.broken_module" in msg
        assert "test_really_green" in msg

    def test_skipped_only_is_not_red(self) -> None:
        # A skipped test is not green but not failed either: calling it RED would let
        # `/implement` enter GREEN on a suite that checked nothing.
        ok, msg = evaluate_report(_report(_case("test_skipme", "skipped")))
        assert not ok
        assert "skip" in msg.lower()

    def test_no_testcases_is_not_red(self) -> None:
        ok, msg = evaluate_report(_report())
        assert not ok
        assert "0" in msg or "no test" in msg.lower()

    def test_sampled_branch_names_the_total(self) -> None:
        # Every other node here carries at most two names, so they only ever exercise
        # the under-the-cap path. Above the cap the message must still answer the
        # question the operator asked: how many, and which ones as an example (#533).
        ok, msg = evaluate_report(_many_cases(163))
        assert not ok
        assert "163" in msg
        assert len(msg) <= 1_200, f"verdict line is {len(msg)} chars"
        assert "test_green_0" in msg
        assert "test_green_162" not in msg

    def test_malformed_report_raises(self) -> None:
        # Inability to count must not open the way to GREEN (§IV/§VI).
        with pytest.raises(ValueError):
            evaluate_report("not xml at all")


def _many_cases(green: int, failed: int = 12) -> str:
    """A report of a realistic shape: a large green suite around a few failing tests."""
    cases = [
        _case(f"test_green_{i}", classname="tests.test_generated.TestBig") for i in range(green)
    ]
    cases += [
        _case(f"test_fail_{i}", "failure", classname="tests.test_generated.TestBig")
        for i in range(failed)
    ]
    return _report(*cases)


def _fake_pytest(
    report_xml: str, stdout: str = "", seen: list[str] | None = None
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Stand in for the pytest subprocess: write the junit report, return its output.

    A process boundary, like the double in `TestCaptureFailureExitCode` — running
    pytest inside pytest is what this double exists to avoid.
    """

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if seen is not None:
            seen.extend(cmd)
        junit = next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("--junitxml="))
        Path(junit).write_text(report_xml, encoding="utf-8")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=stdout, stderr="")

    return fake_run


def _run_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    report_xml: str,
    stdout: str = "",
    seen: list[str] | None = None,
) -> str:
    """Run `main()` against a prepared report and return everything it emitted."""
    monkeypatch.setattr(subprocess, "run", _fake_pytest(report_xml, stdout, seen))
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        main()
    captured = capsys.readouterr()
    return captured.out + captured.err


class TestOutputBudget:
    """The gate's answer must not grow with the suite it runs (#533).

    Measured before the cap: a not-RED run over two green files printed 7 901
    characters, 7 699 of them a single line naming 96 green tests — ~4.9k tokens
    re-sent on every later call of the session, for a verdict of one line.
    """

    def test_many_green_tests_stay_within_budget(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        combined = _run_gate(
            monkeypatch,
            capsys,
            ["check_red.py", "tests/"],
            _many_cases(163),
            "x" * 20_000,
        )
        assert len(combined) <= 3_000, f"gate emitted {len(combined)} chars"
        # The count is the part that changes the next action; it survives the cap (§IV).
        assert "163" in combined

    def test_budget_is_flat_in_test_count(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The property, not a second absolute threshold: a suite six times larger
        # may only add the digits of the counts it reports.
        small = _run_gate(monkeypatch, capsys, ["check_red.py", "tests/"], _many_cases(163))
        large = _run_gate(monkeypatch, capsys, ["check_red.py", "tests/"], _many_cases(1_000))
        assert len(large) - len(small) <= 32

    def test_full_flag_restores_what_the_default_cuts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Stated as the difference between the two runs on purpose: "--full prints
        # everything" is trivially true while nothing is cut, and would pass on the
        # very code this issue exists to change.
        stdout = "HEAD_MARKER" + "x" * 20_000 + "TAIL_MARKER"
        capped = _run_gate(
            monkeypatch, capsys, ["check_red.py", "tests/"], _many_cases(163), stdout
        )
        full = _run_gate(
            monkeypatch, capsys, ["check_red.py", "--full", "tests/"], _many_cases(163), stdout
        )
        assert "test_green_162" not in capped and "HEAD_MARKER" not in capped
        assert "test_green_162" in full, "--full must list every green test"
        assert "HEAD_MARKER" in full and "TAIL_MARKER" in full

    def test_truncation_marker_precedes_the_tail(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stdout = "HEAD_MARKER" + "x" * 20_000 + "TAIL_MARKER"
        combined = _run_gate(
            monkeypatch, capsys, ["check_red.py", "tests/"], _many_cases(163), stdout
        )
        # Truncation is an anomaly, so it is announced where it will be read —
        # before the retained tail, not after it (§IV).
        marker = re.search(r"^\[[^\n]*\d+[^\n]*--full[^\n]*\]$", combined, re.M)
        assert marker, "a cut dump must name the dropped size and the flag that restores it"
        assert marker.start() < combined.index("TAIL_MARKER")
        assert "HEAD_MARKER" not in combined


class TestCli:
    """The argument contract of the gate: paths in, exit codes 0/1/2 out."""

    def test_no_paths_exits_with_usage_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["check_red.py"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2, "no paths is a usage error, not 'tests are not red'"

    def test_full_flag_is_not_taken_as_a_path(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        seen: list[str] = []
        _run_gate(
            monkeypatch,
            capsys,
            ["check_red.py", "--full", "tests/test_x.py"],
            _many_cases(1),
            seen=seen,
        )
        assert "tests/test_x.py" in seen
        assert "--full" not in seen, "the gate's own flag must not reach pytest as a path"


class TestCaptureFailureExitCode:
    """Broken pytest output capture is code 2 (“gate broken”), not 1 (#410).

    Pins the **distinguishing** decision, not checking itself: today `sys.exit(2)` differs
    from `sys.exit(1)` only in prose, while `/implement` step 3 treats them differently—1
    means “tests are not red, fix the plan,” 2 means “the gate could not count.” Confusing
    them sends the implementer to fix the wrong thing."""

    def test_none_stdout_exits_with_gate_broken_code(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=None, stderr=None)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["check_red.py", "tests/whatever.py"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2, "capture failure must not read as 'tests are not red'"
        assert "capture failed" in capsys.readouterr().err
