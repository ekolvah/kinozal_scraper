"""Anti-drift checks for the agent-neutral workflow and its adapters."""

from __future__ import annotations

from pathlib import Path

from scripts.validate_issue_sections import REQUIRED_SECTIONS

_REPO = Path(__file__).resolve().parent.parent


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
