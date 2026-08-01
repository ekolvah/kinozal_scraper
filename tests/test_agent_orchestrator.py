"""Tests for the budget-aware, provider-neutral agent-workflow control plane."""

from __future__ import annotations

import json

from scripts.agent_orchestrator import WorkflowState, decide, load_catalog, main


def _state(**overrides: object) -> WorkflowState:
    values: dict[str, object] = {
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


class TestRoleCatalogue:
    def test_all_initial_roles_have_complete_contracts(self) -> None:
        catalogue = load_catalog()

        assert set(catalogue["roles"]) == {
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

    def test_fixer_limit_escalates_to_human_without_retrying(self) -> None:
        catalogue = load_catalog()
        decision = decide(
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

        assert decision.next_role == "human_merge"
        assert decision.status == "escalate"


class TestEvidenceTruthfulness:
    def test_selected_role_is_not_reported_as_completed_without_its_evidence(self) -> None:
        decision = decide(_state(), load_catalog())

        assert decision.next_role == "architect_reviewer"
        assert decision.status == "next"
        assert decision.completed_roles == ("planner",)


class TestCli:
    def test_cli_reads_state_and_emits_structured_next_action(self, tmp_path, capsys) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps({"plan_completed": True, "issue_kind": "nontrivial"}), encoding="utf-8"
        )

        main([str(state_file)])

        result = json.loads(capsys.readouterr().out)
        assert result["next_role"] == "architect_reviewer"
        assert result["status"] == "next"
