"""Unit tests for the session-level PostToolUse hook (`scripts/hooks.py`, #281).

The hook fires after every Edit/Write and dispatches two cheap checks in one
process: ruff (check-only) on `*.py`, and a pip-compile reminder on
`requirements*.in`. The deterministic decision logic lives in pure functions
(`plan_checks`, `classify_ruff_result`, `pipcompile_signal`, `exit_code`) so it
can be tested without spawning real ruff — the subprocess call is a thin I/O
wrapper (mirrors the `scripts/check_red.py` pure-function + thin-`main` split).

§IV note: a malformed/empty payload is a silent no-op (do not red every edit on
a payload bug), but a *ruff exec failure* (not installed / internal error) must
be a VISIBLE marker — otherwise the agent believes instant-lint is running when
it is not (a silent setup degradation).
"""

from __future__ import annotations

from scripts.hooks import (
    classify_ruff_result,
    exit_code,
    pipcompile_signal,
    plan_checks,
    run_on_edit,
)


def _payload(path: str | None) -> dict:
    if path is None:
        return {"tool_input": {}}
    return {"tool_input": {"file_path": path}}


class TestOnEditDispatch:
    def test_python_file_plans_ruff_check(self) -> None:
        assert plan_checks(_payload("src/kinozal_scraper/soldout_pipeline.py")) == ["ruff"]

    def test_non_python_skips_ruff(self) -> None:
        assert plan_checks(_payload("docs/architecture/ci.md")) == []
        assert plan_checks(_payload(".claude/settings.json")) == []

    def test_malformed_payload_silent_noop(self) -> None:
        # No file_path (and empty payload) → nothing planned, exit 0, no stderr.
        assert plan_checks(_payload(None)) == []
        assert plan_checks({}) == []
        code, stderr = run_on_edit({}, ruff_runner=_never_called)
        assert code == 0
        assert stderr == ""


class TestRuffSignal:
    def test_lint_findings_surface_exit_2(self) -> None:
        # ruff returncode 1 = lint findings → visible marker, exit 2 (feedback to agent).
        sig = classify_ruff_result(1, "src/x.py:1:1: F401 unused import")
        assert sig is not None
        assert sig.kind == "lint"
        assert exit_code([sig]) == 2

    def test_ruff_exec_failure_is_visible_not_silent(self) -> None:
        # ruff returncode >=2 = ruff itself broke (bad config / not runnable).
        # Must be a VISIBLE, DISTINCT marker — not swallowed as "lint clean".
        sig = classify_ruff_result(2, "error: unknown option")
        assert sig is not None
        assert sig.kind == "setup_broken"
        assert sig.kind != "lint"
        assert exit_code([sig]) == 2

    def test_clean_returns_no_marker(self) -> None:
        assert classify_ruff_result(0, "") is None
        assert exit_code([]) == 0


class TestPipCompileGuard:
    def test_requirements_in_flagged(self) -> None:
        assert plan_checks(_payload("requirements.in")) == ["pipcompile"]
        assert plan_checks(_payload("requirements-dev.in")) == ["pipcompile"]
        sig = pipcompile_signal("requirements.in")
        assert "pip-compile" in sig.message
        assert exit_code([sig]) == 2

    def test_requirements_txt_ignored(self) -> None:
        # .txt is the generated lockfile, not the source — no reminder.
        assert plan_checks(_payload("requirements.txt")) == []
        assert plan_checks(_payload("requirements-dev.txt")) == []


def _never_called(_file: str) -> tuple[int, str]:
    raise AssertionError("ruff_runner must not run when nothing is planned")
