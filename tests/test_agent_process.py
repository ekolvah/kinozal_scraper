"""Anti-drift checks for the agent-neutral workflow and its adapters."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml

from scripts.agent_orchestrator import RouteDecision, WorkflowState
from scripts.validate_issue_sections import REQUIRED_SECTIONS

_REPO = Path(__file__).resolve().parent.parent
_IMPLEMENTER_CONTRACT_MARKERS = (
    "validate_issue_sections.py",
    "issue_branch.py",
    "check_red.py",
    "ci_check.py",
    "open_pr.py",
    "gh pr checks",
    "gh run view",
    "review/fix loop",
    "`clean` reviewer outcome",
    "`not ready`",
)


class TestAgentProcess:
    def test_issue_contract_has_nine_sections_and_handoff_last(self) -> None:
        assert len(REQUIRED_SECTIONS) == 9
        assert REQUIRED_SECTIONS[-1] == "Agent handoff"

    def test_claude_and_codex_adapters_link_to_the_same_contract(self) -> None:
        contract = "agent-process.md"
        claude_plan = (_REPO / ".claude" / "commands" / "plan.md").read_text(encoding="utf-8")
        codex_skill = (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert contract in claude_plan
        assert contract in codex_skill

    def test_claude_implement_command_is_removed(self) -> None:
        assert not (_REPO / ".claude" / "commands" / "implement.md").exists()

    def test_pr_template_records_agent_provenance(self) -> None:
        template = (_REPO / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        assert "## Agent record" in template
        assert "Implementer:" in template
        assert "Reviewer / fixer:" in template
        assert "CI evidence:" in template
        assert "Route:" in template
        assert "Model invocations:" in template
        assert "Fixer revisions:" in template
        assert "Conditional skips / escalations:" in template

    def test_control_plane_is_provider_neutral_and_advisory(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        catalogue = (_REPO / ".agents" / "orchestration" / "roles.yaml").read_text(encoding="utf-8")

        assert "agent_orchestrator.py" in process
        assert "never invokes a model" in process
        assert "human_merge:" in catalogue

    def test_documented_control_plane_caps_match_the_catalogue(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        catalogue = yaml.safe_load(
            (_REPO / ".agents" / "orchestration" / "roles.yaml").read_text(encoding="utf-8")
        )

        for role in (
            "planner",
            "architect_reviewer",
            "implementer",
            "pr_reviewer",
            "fixer",
            "human_merge",
        ):
            assert f"| `{role}` | {catalogue['roles'][role]['max_runs']} |" in process

    def test_documented_control_plane_output_matches_route_decision(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")

        assert "### Control-plane output contract" in process
        for field in fields(RouteDecision):
            assert f"| `{field.name}` |" in process
        assert all(status in process for status in ("`next`", "`blocked`", "`escalate`"))

    def test_documented_input_contract_matches_workflow_state(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")

        for field in fields(WorkflowState):
            assert f"| `{field.name}` |" in process

    def test_completed_roles_contract_explains_blocked_route_exception(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        output_contract = process.split("### Control-plane output contract", maxsplit=1)[1]
        output_contract = output_contract.split("| Role |", maxsplit=1)[0]

        assert "selected route is `blocked`" in output_contract
        assert "omitted" in output_contract

    def test_every_agent_record_copy_qualifies_invocation_counts(self) -> None:
        records = (
            _REPO / ".github" / "pull_request_template.md",
            _REPO / "docs" / "architecture" / "agent-process.md",
        )

        for record in records:
            # Line wrapping is a formatting choice, not part of the contract.
            unwrapped = " ".join(record.read_text(encoding="utf-8").split())
            assert "completed run-count proxy at the time this record is written" in unwrapped, (
                f"{record.name} lost the invocation-count qualifier"
            )

    def test_codex_adapter_defers_the_agent_record_contract_instead_of_restating_it(self) -> None:
        skill = " ".join(
            (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md")
            .read_text(encoding="utf-8")
            .split()
        )

        assert "agent-process.md#agent-records-and-adapters" in skill
        # A third enumeration of the fields is what drifts; the pointer is the contract.
        assert "fixer revisions" not in skill

    def test_codex_skill_is_finished_adapter_not_scaffold(self) -> None:
        skill_dir = _REPO / ".agents" / "skills" / "implement-issue"
        skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        manifest = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        assert "TODO:" not in skill
        assert "## Structuring This Skill" not in skill
        assert "implement-issue" in manifest

    def test_codex_adapter_requires_clean_review_before_merge_handoff(self) -> None:
        skill = (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "review/fix loop" in skill
        assert "`clean` reviewer outcome" in skill
        assert "not ready" in skill

    def test_canonical_contract_and_codex_skill_keep_all_implementer_gates(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        skill = (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for marker in _IMPLEMENTER_CONTRACT_MARKERS:
            assert marker in process, f"canonical process lost {marker!r}"
            assert marker in skill, f"Codex adapter lost {marker!r}"

    def test_permanent_codex_rules_preserve_post_pr_readiness_gate(self) -> None:
        agents = (_REPO / "AGENTS.md").read_text(encoding="utf-8")
        for marker in ("review/fix loop", "`not ready`", "`clean` reviewer outcome"):
            assert marker in agents, f"AGENTS.md lost {marker!r}"

    def test_review_controller_policy_requires_manual_ide_review(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        assert "## Review-controller manual review" in process
        assert "manual IDE-agent review" in process
        assert "it is enforced as `clean`, `rework`, or `blocking`" in process
