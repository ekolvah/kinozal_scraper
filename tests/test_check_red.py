"""Tests for `scripts/check_red.py` — the RED gate of `/implement`.

Covers the junit verdict rules (subtest failures redden the parent; a collection
error, a skip-only run or an empty report is NOT red) and the exit code taken
when the subprocess capture itself breaks.
"""

from __future__ import annotations

import subprocess
import sys

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

    def test_malformed_report_raises(self) -> None:
        # Inability to count must not open the way to GREEN (§IV/§VI).
        with pytest.raises(ValueError):
            evaluate_report("not xml at all")


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
