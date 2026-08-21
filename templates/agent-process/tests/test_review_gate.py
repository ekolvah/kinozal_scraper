"""Tests for the review/fix loop stop gate.

The gate answers one question deterministically — «does the loop continue or is
the PR the maintainer's call» — so the rule stops being prose an agent skips.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.agent_orchestrator import load_catalog
from scripts.check_branch_protection import (
    REQUIRED_CONTEXTS,
)
from scripts.check_branch_protection import (
    REVIEW_CONTEXT as PROTECTION_REVIEW_CONTEXT,
)
from scripts.review_gate import (
    REVIEW_CONTEXT,
    VERDICT_EXIT_CODES,
    CheckRun,
    ReviewEvidence,
    collect_evidence,
    evaluate,
    fixer_budget,
    main,
)

# The four heads in this synthetic sequence: round 1 was `blocking`, rounds 2-4
# green — the run the gate would have stopped two rounds earlier. Only the
# short prefixes are the real ones; the tails are padding to 40 hex chars,
# because nothing here depends on a SHA resolving.
_ROUND_1 = "3312ff63a4b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"  # pragma: allowlist secret
_ROUND_2 = "a54549ac1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e"  # pragma: allowlist secret
_ROUND_3 = "38a03bfc2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f"  # pragma: allowlist secret
_ROUND_4 = "58f1c071012eac15bb122ffdcbbaa19de4d08942"  # pragma: allowlist secret
_PR_URL = ""
_RUN_URL = "https://github.com/example-org/example-repo/actions/runs/31105364746"


def _checks(overrides: dict[str, tuple[str, str]] | None = None) -> tuple[CheckRun, ...]:
    """Every required context COMPLETED/SUCCESS unless overridden."""
    graded = overrides or {}
    return tuple(
        CheckRun(name, *graded.get(name, ("COMPLETED", "SUCCESS"))) for name in REQUIRED_CONTEXTS
    )


def _evidence(**overrides: Any) -> ReviewEvidence:
    values: dict[str, Any] = {
        "head_sha": _ROUND_2,
        "checks": _checks(),
        "reviewed_heads": frozenset({_ROUND_1, _ROUND_2}),
        "pr_url": _PR_URL,
        "review_run_url": _RUN_URL,
    }
    values.update(overrides)
    return ReviewEvidence(**values)


class TestVerdict:
    def test_green_required_checks_are_ready_for_human(self) -> None:
        """No blocking finding and two should-fix findings end the loop."""
        verdict = evaluate(_evidence(), fixer_budget=3)

        assert verdict.name == "ready-for-human"
        assert verdict.exit_code == 0

    def test_red_review_is_fix_blocking_and_names_the_ambiguity(self) -> None:
        evidence = _evidence(checks=_checks({REVIEW_CONTEXT: ("COMPLETED", "FAILURE")}))

        verdict = evaluate(evidence, fixer_budget=3)

        assert verdict.name == "fix-blocking"
        assert verdict.exit_code == 10
        assert "blocking findings" in verdict.reason
        assert "review unavailable" in verdict.reason

    def test_red_deterministic_check_is_fix_blocking_and_names_it(self) -> None:
        evidence = _evidence(checks=_checks({"quality": ("COMPLETED", "FAILURE")}))

        verdict = evaluate(evidence, fixer_budget=3)

        assert verdict.name == "fix-blocking"
        assert "quality" in verdict.reason

    @pytest.mark.parametrize(
        "checks",
        [
            pytest.param(_checks({REVIEW_CONTEXT: ("IN_PROGRESS", "")}), id="pending"),
            pytest.param(
                tuple(check for check in _checks() if check.name != REVIEW_CONTEXT), id="absent"
            ),
        ],
    )
    def test_pending_or_absent_required_context_is_review_pending(
        self, checks: tuple[CheckRun, ...]
    ) -> None:
        verdict = evaluate(_evidence(checks=checks), fixer_budget=3)

        assert verdict.name == "review-pending"
        assert verdict.exit_code == 30
        assert REVIEW_CONTEXT in verdict.reason

    def test_exhausted_fixer_budget_escalates(self) -> None:
        """Three spent fixer revisions prevent routing a fourth revision."""
        evidence = _evidence(
            head_sha=_ROUND_4,
            checks=_checks({REVIEW_CONTEXT: ("COMPLETED", "FAILURE")}),
            reviewed_heads=frozenset({_ROUND_1, _ROUND_2, _ROUND_3, _ROUND_4}),
        )

        verdict = evaluate(evidence, fixer_budget=3)

        assert verdict.name == "escalate"
        assert verdict.exit_code == 20
        assert "3/3" in verdict.reason

    def test_exhausted_budget_with_green_checks_is_still_ready(self) -> None:
        """A spent budget stops *more fixing*; it never re-opens a finished PR."""
        evidence = _evidence(
            head_sha=_ROUND_4,
            reviewed_heads=frozenset({_ROUND_1, _ROUND_2, _ROUND_3, _ROUND_4}),
        )

        assert evaluate(evidence, fixer_budget=3).name == "ready-for-human"

    def test_budget_boundary_follows_the_passed_value_not_a_literal(self) -> None:
        evidence = _evidence(checks=_checks({REVIEW_CONTEXT: ("COMPLETED", "FAILURE")}))

        assert evaluate(evidence, fixer_budget=1).name == "escalate"
        assert evaluate(evidence, fixer_budget=2).name == "fix-blocking"


class TestContracts:
    """Structural guards: they hold by construction, so they stay out of the RED set."""

    def test_review_context_is_part_of_required_contexts(self) -> None:
        assert REVIEW_CONTEXT is PROTECTION_REVIEW_CONTEXT
        assert REVIEW_CONTEXT in REQUIRED_CONTEXTS

    def test_every_verdict_has_a_distinct_exit_code(self) -> None:
        assert sorted(VERDICT_EXIT_CODES.values()) == [0, 10, 20, 30]

    def test_fixer_budget_comes_from_the_role_catalogue(self) -> None:
        catalogue = load_catalog()

        assert fixer_budget(catalogue) == catalogue["roles"]["fixer"]["max_runs"]


def _gh_double(pr_payload: dict[str, Any], runs_payload: dict[str, Any]) -> Any:
    """A `subprocess.run` double answering the gate's two `gh` reads."""

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        payload = pr_payload if args[1] == "pr" else runs_payload
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=json.dumps(payload), stderr=""
        )

    return run


def _pr_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": "OPEN",
        "url": _PR_URL,
        "headRefOid": _ROUND_2,
        "headRefName": "issue-464-feat-observability-dev",
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": "SUCCESS"}
            for name in REQUIRED_CONTEXTS
        ],
        "files": [{"path": "src/kinozal_scraper/app.py"}],
    }
    payload.update(overrides)
    return payload


def _runs_payload(*head_shas: str) -> dict[str, Any]:
    return {
        "workflow_runs": [
            {"head_sha": head_sha, "html_url": _RUN_URL, "status": "completed"}
            for head_sha in head_shas
        ]
    }


class TestEvidence:
    def test_fixer_revisions_count_distinct_reviewed_heads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A re-run on the same head is one round, not two — it spends no budget."""
        monkeypatch.setattr(
            subprocess,
            "run",
            _gh_double(_pr_payload(), _runs_payload(_ROUND_1, _ROUND_2, _ROUND_2)),
        )

        evidence = collect_evidence("465")

        assert evidence.reviewed_heads == frozenset({_ROUND_1, _ROUND_2})
        assert evidence.review_run_url == _RUN_URL

    def test_non_checkrun_rollup_entries_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rollup = [
            {"__typename": "StatusContext", "context": "legacy", "state": "FAILURE"},
            *_pr_payload()["statusCheckRollup"],
        ]
        monkeypatch.setattr(
            subprocess,
            "run",
            _gh_double(_pr_payload(statusCheckRollup=rollup), _runs_payload(_ROUND_2)),
        )

        evidence = collect_evidence("465")

        assert {check.name for check in evidence.checks} == set(REQUIRED_CONTEXTS)

    def test_controller_paths_are_not_special_in_the_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The review controller is reviewed normally, so its verdict is normal.

        While carrier 1 was unreachable for these PRs, a green check did not
        prove a review existed and the gate escalated to manual IDE inspection.
        Now that review works, keeping the exception would yield `escalate` on
        every agent-process PR.
        """
        payload = _pr_payload(files=[{"path": ".github/workflows/agent-review.yml"}])
        monkeypatch.setattr(subprocess, "run", _gh_double(payload, _runs_payload(_ROUND_2)))

        verdict = evaluate(collect_evidence("465"), fixer_budget=3)

        assert verdict.name == "ready-for-human"
        assert verdict.exit_code == 0

    def test_gh_failure_exits_two_instead_of_producing_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="403")

        monkeypatch.setattr(subprocess, "run", fail)

        with pytest.raises(SystemExit) as excinfo:
            collect_evidence("465")

        assert excinfo.value.code == 2

    def test_broken_capture_exits_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`stdout is None` means the reader died, not an empty answer."""

        def capture_failed(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=None, stderr="")

        monkeypatch.setattr(subprocess, "run", capture_failed)

        with pytest.raises(SystemExit) as excinfo:
            collect_evidence("465")

        assert excinfo.value.code == 2

    def test_closed_pr_exits_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            _gh_double(_pr_payload(state="MERGED"), _runs_payload(_ROUND_2)),
        )

        with pytest.raises(SystemExit) as excinfo:
            collect_evidence("465")

        assert excinfo.value.code == 2


class TestCli:
    def test_verdict_name_and_exit_code_reach_the_operator(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            subprocess, "run", _gh_double(_pr_payload(), _runs_payload(_ROUND_1, _ROUND_2))
        )

        with pytest.raises(SystemExit) as excinfo:
            main(["465"])

        captured = capsys.readouterr().out
        assert "ready-for-human" in captured
        assert _PR_URL in captured
        assert excinfo.value.code == VERDICT_EXIT_CODES["ready-for-human"]
