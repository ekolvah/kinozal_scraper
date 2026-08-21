"""Tests for the budget-aware, provider-neutral agent-workflow control plane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_doc_links import slugify  # the repo's single GitHub-anchor implementation

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


_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_STATE = _REPO_ROOT / ".agents" / "orchestration" / "state.example.json"


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

    def test_catalogue_records_role_and_adapter_separately(self) -> None:
        """Role identity and selected executor are two facts, not one string.

        `contract` is a resolvable pointer at the canonical section, not a fifth
        prose field: `completion_evidence` already says what the role must produce,
        and a second wording of it is what drifts.
        """
        catalogue = load_catalog()

        for name, role in catalogue["roles"].items():
            contract = str(role["contract"])
            doc, _, anchor = contract.partition("#")
            path = _REPO_ROOT / doc
            assert path.exists(), f"role {name!r} points at a missing contract {contract!r}"
            assert anchor, f"role {name!r} contract must name a section anchor"
            headings = {
                slugify(line.lstrip("# ").strip())
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("#")
            }
            assert anchor in headings, f"role {name!r} contract anchor {anchor!r} does not resolve"
            named = [
                provider
                for provider in ("Claude", "Codex")
                if provider in contract or provider in str(role["completion_evidence"])
            ]
            assert not named, f"role {name!r} names {named} in its provider-neutral contract"

    def test_selected_adapter_is_one_of_the_declared_entry_points(self) -> None:
        """`adapter` is the default, `adapters` is the permitted set."""
        catalogue = load_catalog()

        for name, role in catalogue["roles"].items():
            assert role["adapter"] in role["adapters"], (
                f"role {name!r} selects an adapter that is not a declared entry point"
            )
        assert len(catalogue["roles"]["planner"]["adapters"]) > 1, (
            "the planner catalogue entry still describes a single permitted executor"
        )

    def test_incomplete_role_contract_is_a_visible_validation_error(self, tmp_path: Path) -> None:
        catalogue = load_catalog()
        del catalogue["roles"]["planner"]["contract"]
        without_contract = tmp_path / "roles.yaml"
        without_contract.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match="incomplete contract"):
            load_catalog(without_contract)

        mismatched = load_catalog()
        mismatched["roles"]["planner"]["adapter"] = "Some other agent"
        mismatched_path = tmp_path / "mismatched.yaml"
        mismatched_path.write_text(yaml.safe_dump(mismatched), encoding="utf-8")

        with pytest.raises(ValueError, match="declared entry points"):
            load_catalog(mismatched_path)

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


_READY_FOR_REVIEW: dict[str, Any] = {
    "architect_completed": True,
    "implementation_completed": True,
    "ci_passed": True,
    "head_sha": "d" * 40,
    "reviewed_heads": ("d" * 40,),
}


class TestRunRoute:
    """`route` picks the adapter per run; the catalogue default stays the fallback."""

    @pytest.mark.parametrize(
        ("role", "overrides", "claude_adapter", "codex_adapter"),
        [
            (
                "planner",
                {"plan_completed": False},
                "Claude /plan #N",
                "Codex $plan-issue #N",
            ),
            (
                "architect_reviewer",
                {},
                "Claude architect-reviewer subagent",
                "Codex $plan-issue #N self-review",
            ),
            (
                "implementer",
                {"architect_completed": True},
                "Claude /implement #N",
                "Codex $implement-issue #N",
            ),
            (
                "fixer",
                {**_READY_FOR_REVIEW, "review_outcome": "rework"},
                "Claude /implement #N review/fix loop",
                "Codex $implement-issue #N review/fix loop",
            ),
        ],
    )
    def test_route_names_the_running_agent_and_leaves_the_contract_neutral(
        self,
        role: str,
        overrides: dict[str, Any],
        claude_adapter: str,
        codex_adapter: str,
    ) -> None:
        catalogue = load_catalog()

        claude = decide(_state(route="claude", **overrides), catalogue)
        codex = decide(_state(route="codex", **overrides), catalogue)

        assert claude.next_role == codex.next_role == role
        assert (claude.adapter, claude.next_action) == (claude_adapter, claude_adapter)
        assert (codex.adapter, codex.next_action) == (codex_adapter, codex_adapter)
        # The role contract is provider-neutral; only its executor moves.
        assert claude.contract == codex.contract
        assert (claude.route, codex.route) == ("claude", "codex")

    def test_an_unset_route_keeps_the_catalogue_default(self) -> None:
        catalogue = load_catalog()

        decision = decide(_state(architect_completed=True), catalogue)

        assert decision.adapter == catalogue["roles"]["implementer"]["adapter"]
        assert decision.next_action == decision.adapter
        assert decision.route is None

    @pytest.mark.parametrize(
        ("overrides", "role", "adapter"),
        [
            ({**_READY_FOR_REVIEW, "reviewed_heads": ()}, "pr_reviewer", "Claude code-review"),
            ({**_READY_FOR_REVIEW, "review_outcome": "clean"}, "human_merge", "Human reviewer"),
        ],
    )
    def test_a_role_the_run_route_does_not_pick_answers_with_its_default(
        self, overrides: dict[str, Any], role: str, adapter: str
    ) -> None:
        """A GitHub Action and a human are not a provider's variant of anything.

        The review gate keeps answering this way after gaining a second carrier: that
        one is selected inside CI, not by the chat the human is in."""
        decision = decide(_state(route="codex", **overrides), load_catalog())

        assert decision.next_role == role
        assert decision.adapter.startswith(adapter)
        assert decision.route == "codex"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"plan_completed": False},
            # A typo must not resolve silently at a single-carrier step either.
            {**_READY_FOR_REVIEW, "reviewed_heads": ()},
        ],
    )
    def test_a_route_unknown_to_the_catalogue_is_a_visible_error(
        self, overrides: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError, match="unknown run route 'codx'") as failure:
            decide(_state(route="codx", **overrides), load_catalog())

        assert "claude" in str(failure.value) and "codex" in str(failure.value)

    def test_a_role_without_the_requested_route_names_itself_and_its_routes(
        self, tmp_path: Path
    ) -> None:
        """Asymmetric catalogues are the case the per-role check guards."""
        catalogue = load_catalog()
        planner = catalogue["roles"]["planner"]
        planner["adapters"].append("Gemini /plan #N")
        planner["adapter_routes"]["gemini"] = "Gemini /plan #N"
        asymmetric = tmp_path / "roles.yaml"
        asymmetric.write_text(yaml.safe_dump(catalogue), encoding="utf-8")
        loaded = load_catalog(asymmetric)

        assert decide(_state(plan_completed=False, route="gemini"), loaded).adapter.startswith(
            "Gemini"
        )
        with pytest.raises(ValueError, match="'implementer'") as failure:
            decide(_state(architect_completed=True, route="gemini"), loaded)

        assert "claude" in str(failure.value) and "codex" in str(failure.value)

    @pytest.mark.parametrize(
        ("role", "mutation", "message"),
        [
            ("planner", {"adapter_routes": None}, "adapter_routes"),
            ("planner", {"adapter_routes": {"claude": "Claude /plan #N"}}, "adapter_routes"),
            (
                "pr_reviewer",
                {"adapter_routes": {"claude": "Claude code-review GitHub Action"}},
                "route-independent",
            ),
        ],
    )
    def test_a_route_map_that_does_not_cover_the_adapters_fails_validation(
        self, tmp_path: Path, role: str, mutation: dict[str, Any], message: str
    ) -> None:
        catalogue = load_catalog()
        catalogue["roles"][role].update(mutation)
        broken = tmp_path / "roles.yaml"
        broken.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match=message):
            load_catalog(broken)

    def test_a_missing_route_map_is_an_incomplete_contract(self, tmp_path: Path) -> None:
        catalogue = load_catalog()
        del catalogue["roles"]["planner"]["adapter_routes"]
        without_routes = tmp_path / "roles.yaml"
        without_routes.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match="incomplete contract"):
            load_catalog(without_routes)

    def test_cli_carries_the_route_from_the_state_file_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {"plan_completed": True, "issue_kind": "nontrivial", "route": "codex"},
            ),
            encoding="utf-8",
        )

        main([str(state_file)])

        result = json.loads(capsys.readouterr().out)
        assert result["route"] == "codex"
        assert result["adapter"] == "Codex $plan-issue #N self-review"
        assert result["next_action"] == result["adapter"]


class TestCarrierSelection:
    """Not every alternative carrier is a run-route variant.

    `pr_reviewer` gains a second carrier so an exhausted quota stops locking every
    PR behind a required check. That carrier is picked inside the CI job, by whether
    the first one produced a verdict — not by which chat the human is sitting in. The
    catalogue therefore has to say *how* a role's carrier is selected; deriving it
    from the adapter count would force the review gate to claim a `codex` run is
    reviewed by Codex, which is confident misinformation of exactly the kind.
    """

    def test_the_review_gate_declares_both_of_its_carriers(self) -> None:
        role = load_catalog()["roles"]["pr_reviewer"]

        assert len(role["adapters"]) == 2, (
            "the second review carrier exists in CI but not in the catalogue, so the "
            "control plane cannot name who reviewed a head"
        )
        assert role["carrier_selection"] == "ci_failover"
        assert role["adapter_routes"] is None
        assert set(role["adapter_files"]) == set(role["adapters"])

    def test_a_runtime_selected_carrier_ignores_the_run_route(self) -> None:
        """Both routes name the carrier that is asked first, because CI asks it first."""
        catalogue = load_catalog()
        overrides = {**_READY_FOR_REVIEW, "reviewed_heads": ()}

        carriers = catalogue["roles"]["pr_reviewer"]["adapters"]
        assert len(carriers) > 1, "with one carrier this asserts nothing about selection"

        claude = decide(_state(route="claude", **overrides), catalogue)
        codex = decide(_state(route="codex", **overrides), catalogue)

        assert claude.next_role == codex.next_role == "pr_reviewer"
        assert claude.adapter == codex.adapter == carriers[0]

    def test_the_carrier_asked_first_is_the_declared_default(self, tmp_path: Path) -> None:
        """Otherwise `adapter` and the failover order tell two different stories."""
        catalogue = load_catalog()
        role = catalogue["roles"]["pr_reviewer"]
        role["adapter"] = role["adapters"][1]
        reordered = tmp_path / "roles.yaml"
        reordered.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match="asked first"):
            load_catalog(reordered)

    def test_a_second_carrier_cannot_arrive_without_saying_how_it_is_selected(
        self, tmp_path: Path
    ) -> None:
        """The guard survives the new mode: silence is not a selection rule."""
        catalogue = load_catalog()
        catalogue["roles"]["human_merge"]["adapters"].append("Second human reviewer")
        undeclared = tmp_path / "roles.yaml"
        undeclared.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match="carrier_selection"):
            load_catalog(undeclared)

    @pytest.mark.parametrize(
        ("role", "mutation", "message"),
        [
            ("planner", {"carrier_selection": "sole"}, "carrier_selection"),
            ("human_merge", {"carrier_selection": "ci_failover"}, "carrier_selection"),
            ("pr_reviewer", {"carrier_selection": "whoever-is-free"}, "carrier_selection"),
        ],
    )
    def test_a_selection_mode_that_contradicts_the_adapters_fails_validation(
        self, tmp_path: Path, role: str, mutation: dict[str, Any], message: str
    ) -> None:
        catalogue = load_catalog()
        catalogue["roles"][role].update(mutation)
        broken = tmp_path / "roles.yaml"
        broken.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match=message):
            load_catalog(broken)

    def test_a_missing_selection_mode_is_an_incomplete_contract(self, tmp_path: Path) -> None:
        catalogue = load_catalog()
        del catalogue["roles"]["pr_reviewer"]["carrier_selection"]
        without_mode = tmp_path / "roles.yaml"
        without_mode.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(ValueError, match="incomplete contract"):
            load_catalog(without_mode)


class TestRouteResolution:
    def test_nontrivial_issue_routes_through_architect_then_implementer(self) -> None:
        catalogue = load_catalog()

        assert decide(_state(), catalogue).next_role == "architect_reviewer"
        assert decide(_state(architect_completed=True), catalogue).next_role == "implementer"

    def test_evidence_required_issue_routes_to_discovery_before_planning(self) -> None:
        """A bug issue observes first, then plans.

        Routing to the planner while the `## Evidence` block is still missing is what
        produced the section nobody owed: the planner would be sent to write a plan
        whose required input does not exist yet.
        """
        catalogue = load_catalog()

        first = decide(_state(plan_completed=False, evidence_required=True), catalogue)
        assert first.next_role == "discovery"
        assert first.status == "next"

        after = decide(
            _state(plan_completed=False, evidence_required=True, evidence_completed=True),
            catalogue,
        )
        assert after.next_role == "planner"

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

    def test_exhausted_discovery_budget_escalates_to_a_human(self) -> None:
        """Two runs, because a failed capture is retried once after access is fixed.

        The third attempt is not a retry, it is a standing external obstacle — the case
        the `status: failed` branch already writes down, so the router hands it to a
        person instead of looping.
        """
        catalogue = load_catalog()

        second = decide(
            _state(plan_completed=False, evidence_required=True, discovery_runs=1), catalogue
        )
        assert second.next_role == "discovery"

        decision = decide(
            _state(plan_completed=False, evidence_required=True, discovery_runs=2), catalogue
        )
        assert decision.next_role == "human_merge"
        assert decision.status == "escalate"
        assert decision.next_action == "human observation decision"

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

    def test_discovery_is_not_reported_completed_without_its_evidence(self) -> None:
        """Selection is not completion, for the new role as for every other one."""
        in_progress = decide(_state(plan_completed=False, evidence_required=True), load_catalog())
        assert in_progress.next_role == "discovery"
        assert "discovery" not in in_progress.completed_roles

        handed_off = decide(_state(evidence_required=True, evidence_completed=True), load_catalog())
        assert "discovery" in handed_off.completed_roles

        # A change class that never asks for the block must not claim the role ran.
        assert "discovery" not in decide(_state(), load_catalog()).completed_roles

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
            ({"plan_completed": True, "issue_kind": "nontrivial", "route": 7}, "route"),
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
