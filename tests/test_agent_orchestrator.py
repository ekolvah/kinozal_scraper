"""Tests for the budget-aware, provider-neutral agent-workflow control plane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.agent_orchestrator import (
    WorkflowState,
    _decision,
    _state_from_json,
    decide,
    load_catalog,
    main,
)


def _state(**overrides: object) -> WorkflowState:
    values: dict[str, Any] = {
        "plan_completed": True,
        "issue_kind": "nontrivial",
        "architect_completed": False,
        "architect_skip_reason": None,
        "implementation_completed": False,
        "ci_passed": False,
        "head_sha": None,
        "reviewed_heads": (),
        "review_outcome": None,
        "fixer_revisions": 0,
    }
    values.update(overrides)
    return WorkflowState(**values)


_EXAMPLE_STATE = (
    Path(__file__).resolve().parent.parent / ".agents" / "orchestration" / "state.example.json"
)


class TestRoleCatalogue:
    def test_example_workflow_state_is_a_valid_starting_point(self) -> None:
        payload = json.loads(_EXAMPLE_STATE.read_text(encoding="utf-8"))

        state = _state_from_json(payload)

        assert decide(state, load_catalog()).next_role == "architect_reviewer"

    def test_all_initial_roles_have_complete_contracts(self) -> None:
        catalogue = load_catalog()

        assert set(catalogue["roles"]) >= {
            "planner",
            "architect_reviewer",
            "implementer",
            "pr_reviewer",
            "fixer",
            "human_merge",
        }
        for role in catalogue["roles"].values():
            assert set(role) >= {
                "adapter",
                "authority",
                "entry_evidence",
                "completion_evidence",
                "activation",
                "max_runs",
            }

    def test_catalogue_reports_malformed_yaml_as_a_validation_error(self, tmp_path: Path) -> None:
        broken_catalogue = tmp_path / "roles.yaml"
        broken_catalogue.write_text("roles: [unterminated", encoding="utf-8")

        with pytest.raises(ValueError, match="cannot read role catalogue"):
            load_catalog(broken_catalogue)

    def test_catalogue_can_add_a_role_without_a_code_membership_change(
        self, tmp_path: Path
    ) -> None:
        catalogue = load_catalog()
        catalogue["roles"]["code_critic"] = dict(catalogue["roles"]["pr_reviewer"])
        extended_catalogue = tmp_path / "roles.yaml"
        extended_catalogue.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        assert "code_critic" in load_catalog(extended_catalogue)["roles"]

    def test_catalogue_validation_errors_are_visible(self, tmp_path: Path) -> None:
        missing_mapping = tmp_path / "missing-mapping.yaml"
        missing_mapping.write_text("roles: []", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping named 'roles'"):
            load_catalog(missing_mapping)

        missing_role = tmp_path / "missing-role.yaml"
        missing_role.write_text("roles: {}", encoding="utf-8")
        with pytest.raises(ValueError, match="missing initial roles"):
            load_catalog(missing_role)

        zero_budget = load_catalog()
        zero_budget["roles"]["planner"]["max_runs"] = 0
        zero_budget_catalogue = tmp_path / "zero-budget.yaml"
        zero_budget_catalogue.write_text(yaml.safe_dump(zero_budget), encoding="utf-8")
        with pytest.raises(ValueError, match="positive max_runs"):
            load_catalog(zero_budget_catalogue)


class TestRouteResolution:
    def test_nontrivial_issue_routes_through_architect_then_implementer(self) -> None:
        catalogue = load_catalog()

        assert decide(_state(), catalogue).next_role == "architect_reviewer"
        assert decide(_state(architect_completed=True), catalogue).next_role == "implementer"

    def test_trivial_issue_requires_a_recorded_architect_skip_reason(self) -> None:
        catalogue = load_catalog()

        missing_reason = decide(_state(issue_kind="trivial"), catalogue)
        assert missing_reason.status == "blocked"
        assert "architect_skip_reason" in missing_reason.missing_evidence
        assert (
            decide(
                _state(issue_kind="trivial", architect_skip_reason="one-line typo"), catalogue
            ).next_role
            == "implementer"
        )

    def test_rework_routes_to_fixer_and_clean_routes_to_human_merge(self) -> None:
        catalogue = load_catalog()
        shared = {
            "architect_completed": True,
            "implementation_completed": True,
            "ci_passed": True,
            "head_sha": "a" * 40,
            "reviewed_heads": ("a" * 40,),
        }

        assert decide(_state(review_outcome="rework", **shared), catalogue).next_role == "fixer"
        assert (
            decide(_state(review_outcome="clean", **shared), catalogue).next_role == "human_merge"
        )


class TestBudgetLimits:
    @pytest.mark.parametrize(
        ("run_field", "state_overrides", "expected_action"),
        [
            ("planner_runs", {"plan_completed": False}, "human plan decision"),
            ("architect_runs", {}, "human architecture decision"),
            (
                "implementer_runs",
                {"architect_completed": True},
                "human implementation decision",
            ),
        ],
    )
    def test_role_budget_caps_escalate_without_retrying(
        self, run_field: str, state_overrides: dict[str, object], expected_action: str
    ) -> None:
        decision = decide(_state(**state_overrides, **{run_field: 1}), load_catalog())

        assert decision.next_role == "human_merge"
        assert decision.status == "escalate"
        assert decision.next_action == expected_action

    def test_reviewer_budget_is_bound_to_head_sha(self) -> None:
        catalogue = load_catalog()
        common = {
            "architect_completed": True,
            "implementation_completed": True,
            "ci_passed": True,
            "head_sha": "b" * 40,
        }

        assert (
            decide(_state(reviewed_heads=("a" * 40,), **common), catalogue).next_role
            == "pr_reviewer"
        )
        current_head = decide(_state(reviewed_heads=("b" * 40,), **common), catalogue)
        assert current_head.status == "blocked"
        assert "review_outcome" in current_head.missing_evidence
        assert "pr_reviewer" not in current_head.completed_roles

    def test_fixer_limit_escalates_to_human_without_retrying(self) -> None:
        catalogue = load_catalog()
        third_revision = decide(
            _state(
                architect_completed=True,
                implementation_completed=True,
                ci_passed=True,
                head_sha="a" * 40,
                reviewed_heads=("a" * 40,),
                review_outcome="blocking",
                fixer_revisions=2,
            ),
            catalogue,
        )
        decision = decide(
            _state(
                architect_completed=True,
                implementation_completed=True,
                ci_passed=True,
                head_sha="a" * 40,
                reviewed_heads=("a" * 40,),
                review_outcome="blocking",
                fixer_revisions=3,
            ),
            catalogue,
        )

        assert third_revision.next_role == "fixer"
        assert decision.next_role == "human_merge"
        assert decision.status == "escalate"
        assert "fixer" not in decision.completed_roles


class TestEvidenceTruthfulness:
    def test_selected_role_is_not_reported_as_completed_without_its_evidence(self) -> None:
        decision = decide(_state(), load_catalog())

        assert decision.next_role == "architect_reviewer"
        assert decision.status == "next"
        assert decision.completed_roles == ("planner",)

    def test_nontrivial_architect_skip_does_not_report_completion(self) -> None:
        decision = decide(
            _state(architect_skip_reason="one-line typo"),
            load_catalog(),
        )

        assert decision.next_role == "architect_reviewer"
        assert "architect_reviewer" not in decision.completed_roles

    def test_implementer_needs_ci_and_fixer_needs_an_unreviewed_head(self) -> None:
        blocked_ci = decide(
            _state(architect_completed=True, implementation_completed=True),
            load_catalog(),
        )
        fixer_in_progress = decide(
            _state(
                architect_completed=True,
                implementation_completed=True,
                ci_passed=True,
                head_sha="d" * 40,
                reviewed_heads=("d" * 40,),
                review_outcome="rework",
                fixer_revisions=1,
            ),
            load_catalog(),
        )
        fixer_handed_off = decide(
            _state(
                architect_completed=True,
                implementation_completed=True,
                ci_passed=True,
                head_sha="e" * 40,
                fixer_revisions=1,
            ),
            load_catalog(),
        )

        assert "implementer" not in blocked_ci.completed_roles
        assert "fixer" not in fixer_in_progress.completed_roles
        assert "fixer" in fixer_handed_off.completed_roles


class TestBlockedRoutes:
    @pytest.mark.parametrize(
        "state",
        [
            _state(issue_kind="unknown"),
            _state(issue_kind="trivial"),
            _state(architect_completed=True, implementation_completed=True),
            _state(
                architect_completed=True,
                implementation_completed=True,
                ci_passed=True,
            ),
            _state(
                architect_completed=True,
                implementation_completed=True,
                ci_passed=True,
                head_sha="f" * 40,
                reviewed_heads=("f" * 40,),
            ),
        ],
    )
    def test_blocked_role_is_never_reported_completed(self, state: WorkflowState) -> None:
        decision = decide(state, load_catalog())

        assert decision.status == "blocked"
        assert decision.next_role not in decision.completed_roles

    def test_invalid_issue_kind_has_a_blocked_shape(self) -> None:
        decision = decide(_state(issue_kind="unknown"), load_catalog())

        assert decision.status == "blocked"
        assert decision.missing_evidence == ("issue_kind",)

    def test_missing_ci_evidence_is_blocked_until_the_command_runs(self) -> None:
        decision = decide(
            _state(architect_completed=True, implementation_completed=True),
            load_catalog(),
        )

        assert decision.next_role == "deterministic_ci"
        assert decision.status == "blocked"
        assert decision.missing_evidence == ("ci_passed",)

    def test_missing_head_sha_and_invalid_review_outcome_are_blocked(self) -> None:
        catalogue = load_catalog()
        ready_for_review = {
            "architect_completed": True,
            "implementation_completed": True,
            "ci_passed": True,
        }
        missing_head = decide(_state(**ready_for_review), catalogue)
        invalid_outcome = decide(
            _state(
                **ready_for_review,
                head_sha="c" * 40,
                reviewed_heads=("c" * 40,),
                review_outcome="maybe",
            ),
            catalogue,
        )

        assert missing_head.missing_evidence == ("head_sha",)
        assert invalid_outcome.missing_evidence == ("review_outcome",)
        assert missing_head.status == invalid_outcome.status == "blocked"
        assert "pr_reviewer" not in invalid_outcome.completed_roles

    def test_unknown_route_step_and_malformed_reviewed_heads_are_visible(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(ValueError, match="unknown route step"):
            _decision("fixxer", load_catalog(), _state())

        state_file = tmp_path / "bad-state.json"
        state_file.write_text(
            json.dumps(
                {
                    "plan_completed": True,
                    "issue_kind": "nontrivial",
                    "reviewed_heads": "not-a-list",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="2"):
            main([str(state_file)])
        assert "reviewed_heads must be a list of strings" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"plan_completed": "false", "issue_kind": "nontrivial"}, "must be a boolean"),
            ({"plan_completed": True, "issue_kind": "nontrivial", "head_sha": 12345}, "head_sha"),
            (
                {"plan_completed": True, "issue_kind": "nontrivial", "fixer_revisions": -1},
                "non-negative integer",
            ),
        ],
    )
    def test_boolean_and_counter_state_errors_are_visible(
        self, payload: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            _state_from_json(payload)

    def test_missing_required_state_evidence_is_visible(self) -> None:
        with pytest.raises(ValueError, match="plan_completed"):
            _state_from_json({"issue_kind": "nontrivial"})


class TestCli:
    def test_cli_reads_state_and_emits_structured_next_action(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps({"plan_completed": True, "issue_kind": "nontrivial"}), encoding="utf-8"
        )

        main([str(state_file)])

        result = json.loads(capsys.readouterr().out)
        assert result["next_role"] == "architect_reviewer"
        assert result["status"] == "next"

    def test_cli_reports_a_missing_state_file_without_a_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing_file = tmp_path / "missing-state.json"

        with pytest.raises(SystemExit, match="2"):
            main([str(missing_file)])

        error = capsys.readouterr().err
        assert missing_file.name in error
        assert "error:" in error
