"""Anti-drift checks for the agent-neutral workflow and its adapters."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml

from scripts.agent_orchestrator import RouteDecision, WorkflowState
from scripts.review_gate import VERDICT_EXIT_CODES
from scripts.validate_issue_sections import REQUIRED_SECTIONS

_REPO = Path(__file__).resolve().parent.parent
# The readiness condition itself: canonical in agent-process.md, nowhere else.
_STOP_CONDITION = "no blocking finding and every required check passes"
_IMPLEMENTER_CONTRACT_MARKERS = (
    "validate_issue_sections.py",
    "set_issue_priority.py",
    "issue_branch.py",
    "check_red.py",
    "ci_check.py",
    "open_pr.py",
    "gh pr checks",
    "gh run view",
    "review/fix loop",
    # The stop condition is no longer restated per adapter — every home routes the
    # decision through the one gate with an exit code (#467).
    "scripts.review_gate",
    "`not ready`",
)
# The record fields as the canonical section words them; adapters point here instead of copying.
_CANONICAL_AGENT_RECORD_FIELDS = (
    "the implementer",
    "reviewer/fixer",
    "CI evidence",
    "selected route",
    "model-invocation counts",
    "fixer revisions",
    "conditional skips/escalations",
)
# The field labels an adapter must defer to the canonical section instead of restating.
_AGENT_RECORD_FIELD_LABELS = (
    "implementation identity",
    "reviewer/fixer identities",
    "CI evidence",
    "selected route",
    "invocation counts",
    "fixer revisions",
    "skip/escalation",
)
# Anchors an adapter points at instead of restating the runbook it names.
_PLANNER_RUNBOOK_ANCHOR = "agent-process.md#planner-runbook"
_ARCHITECT_CONTRACT_ANCHOR = "agent-process.md#architect-review-contract"
# Substance of the planner runbook: the numbers an adapter must not re-decide.
_PLANNER_RUNBOOK_MARKERS = (
    "at most three clarifying questions",
    "three planning iterations",
)
# A shared gate is *defined* by an enumeration or a bound, never by naming a script:
# `test_canonical_contract_and_codex_skill_keep_all_implementer_gates` deliberately
# requires command names inside the adapters, so a blanket "no gates here" rule would
# contradict it. The genre is `test_codex_adapter_defers_the_agent_record_contract...`:
# what may not travel is the definition, not the pointer. Matching is case-sensitive on
# purpose — an adapter saying `should-fix` findings do not gate its loop states an
# interface fact, while `SHOULD-FIX` reproduces the severity taxonomy (#452).
_SHARED_GATE_DEFINITIONS = (
    ("SHOULD-FIX", "docs/architecture/agent-process.md"),
    ("NICE-TO-HAVE", "docs/architecture/agent-process.md"),
    # Preventive scope for the Claude implementer shim (#473), green on add: both
    # strings live only in the canonical flow today. They replace the deleted
    # `test_claude_implement_command_is_removed`, which guarded the absence of
    # `.claude/commands/implement.md` because the fat 25-line copy removed in #444
    # duplicated exactly these. The invariant worth keeping is the shim's *form*,
    # not its absence, so it moves here where `_provider_files()` already covers
    # `.claude/commands/**` by glob.
    ("no blocking finding and every required check passes", "docs/architecture/agent-process.md"),
    ("three improving iterations", "docs/architecture/agent-process.md"),
    ("at most three clarifying questions", "docs/architecture/agent-process.md"),
    ("three planning iterations", "docs/architecture/agent-process.md"),
    ("for a need that does not exist yet", "docs/architecture/agent-process.md"),
    ("Minimize future bug-fixing and support", "docs/architecture/principles.md"),
    ("Optimize token spend", "docs/architecture/principles.md"),
    ("Preserve predictability and user control", "docs/architecture/principles.md"),
    ("a script with an exit code and unit tests", "docs/architecture/principles.md"),
)


def _provider_files() -> list[Path]:
    """Files whose path already names a provider: adapters, not canon (#452).

    Derived from globs rather than a list, so the next Claude rule or Codex skill
    falls under the invariant without anyone remembering to enrol it — the same
    reason `test_doc_headers.py` scopes by `rglob`.
    """
    return [
        *sorted((_REPO / ".claude" / "commands").rglob("*.md")),
        *sorted((_REPO / ".claude" / "agents").rglob("*.md")),
        *sorted((_REPO / ".claude" / "rules").rglob("*.md")),
        *sorted((_REPO / ".agents" / "skills").rglob("SKILL.md")),
        _REPO / "AGENTS.md",
    ]


def _codex_skills() -> list[Path]:
    return sorted(path for path in (_REPO / ".agents" / "skills").glob("*") if path.is_dir())


class TestAgentProcess:
    def test_issue_contract_has_nine_sections_and_handoff_last(self) -> None:
        assert len(REQUIRED_SECTIONS) == 9
        assert REQUIRED_SECTIONS[-1] == "Agent handoff"

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

    def test_documented_carrier_selection_modes_match_the_catalogue(self) -> None:
        """A selection mode nobody documented is a rule only the validator knows (#478)."""
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        catalogue = yaml.safe_load(
            (_REPO / ".agents" / "orchestration" / "roles.yaml").read_text(encoding="utf-8")
        )

        modes = {str(role["carrier_selection"]) for role in catalogue["roles"].values()}
        assert "ci_failover" in modes, "no role declares the runtime-selected carrier mode"
        for mode in modes:
            assert f"`{mode}`" in process, f"selection mode {mode!r} is undocumented"

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

    def test_canonical_section_still_enumerates_the_agent_record_fields(self) -> None:
        heading = "## Agent records and adapters"
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        assert heading in process, "the canonical record section was renamed or removed"
        section = " ".join(
            process.split(heading, maxsplit=1)[1].split("\n## ", maxsplit=1)[0].split()
        )

        for field in _CANONICAL_AGENT_RECORD_FIELDS:
            assert field in section, f"canonical record section lost {field!r}"

    def test_codex_adapter_defers_the_agent_record_contract_instead_of_restating_it(self) -> None:
        skill = " ".join(
            (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md")
            .read_text(encoding="utf-8")
            .split()
        )

        assert "agent-process.md#agent-records-and-adapters" in skill, (
            "Codex adapter stopped pointing at the canonical record contract"
        )
        # A third enumeration of the fields is what drifts; the pointer is the contract.
        for label in _AGENT_RECORD_FIELD_LABELS:
            assert label not in skill, f"Codex adapter restated {label!r}"

    def test_every_codex_skill_is_a_finished_adapter(self) -> None:
        """Both Codex entry points, not only the first one that was written (#452).

        The check was hardcoded to `implement-issue`, so a second skill would have
        arrived without the single guard that separates an adapter from a scaffold.
        The planner and implementer skills are pinned by name because their absence
        is the failure this test exists to report: a scope derived purely from the
        glob would go green on an empty directory.
        """
        skills = {path.name: path for path in _codex_skills()}
        assert {"implement-issue", "plan-issue"} <= skills.keys(), (
            f"a Codex role adapter is missing; found {sorted(skills)}"
        )

        for name, skill_dir in skills.items():
            skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            manifest = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
            assert "TODO:" not in skill, f"{name}: unfinished scaffold marker"
            assert "## Structuring This Skill" not in skill, f"{name}: template section left in"
            assert name in manifest, f"{name}: manifest does not name its own skill"

    def test_planner_adapters_share_the_canonical_runbook(self) -> None:
        """Both planner entry points point at one runbook instead of carrying a copy.

        Mirrors how the implementer role already works: `## Deterministic delivery
        flow` is canon and `implement-issue/SKILL.md` points at it (#452).
        """
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        assert "## Planner runbook" in process, "the canonical planner runbook is missing"

        adapters = {
            "Claude /plan": _REPO / ".claude" / "commands" / "plan.md",
            "Codex plan-issue": _REPO / ".agents" / "skills" / "plan-issue" / "SKILL.md",
        }
        for name, path in adapters.items():
            assert path.exists(), f"{name}: planner adapter is missing"
            text = path.read_text(encoding="utf-8")
            assert _PLANNER_RUNBOOK_ANCHOR in text, (
                f"{name} does not point at {_PLANNER_RUNBOOK_ANCHOR}"
            )
            for marker in _PLANNER_RUNBOOK_MARKERS:
                assert marker in process, f"canonical runbook lost {marker!r}"
                assert marker not in text, f"{name} restates the runbook bound {marker!r}"

    def test_every_declared_role_adapter_resolves_to_its_contract(self) -> None:
        """Adapter coverage stays symmetric per role, or the gap is named (#473).

        Keyed on declared file-ness — `adapter_files` — rather than on how many
        adapters a role has. `pr_reviewer` and `human_merge` carry entry points that
        are a GitHub Action and a person; a count-based predicate would red them the
        moment either gained a second adapter, with no legal fix available. An
        explicit `null` says "this entry point is not a file"; a missing key is a
        failure, so a new adapter cannot arrive unresolved.
        """
        catalogue = yaml.safe_load(
            (_REPO / ".agents" / "orchestration" / "roles.yaml").read_text(encoding="utf-8")
        )

        for name, role in catalogue["roles"].items():
            declared = role.get("adapter_files")
            assert isinstance(declared, dict), f"role {name!r} declares no adapter_files mapping"
            assert set(declared) == set(role["adapters"]), (
                f"role {name!r}: adapter_files keys {sorted(declared)} do not match "
                f"adapters {sorted(role['adapters'])}"
            )

            # "docs/architecture/agent-process.md#planner-runbook" -> the link fragment
            # an adapter must carry; a link to the whole document is not a contract.
            anchor = role["contract"].rsplit("/", maxsplit=1)[-1]
            for adapter, relative in declared.items():
                if relative is None:
                    continue
                path = _REPO / relative
                assert path.exists(), f"role {name!r}: adapter {adapter!r} names missing {relative}"
                assert anchor in path.read_text(encoding="utf-8"), (
                    f"role {name!r}: {relative} does not point at its contract {anchor!r}"
                )

    def test_provider_specific_adapter_files_do_not_define_shared_gates(self) -> None:
        """A provider file exposes an interface; the gate it obeys is defined elsewhere.

        Both halves are asserted: the definition is present in its canonical home and
        absent from every provider file. Checking only the absence would go green when
        a definition is deleted rather than moved (#452).
        """
        provider_files = _provider_files()
        assert provider_files, "the provider-file scope collapsed to nothing"

        offenders: list[str] = []
        for marker, canonical in _SHARED_GATE_DEFINITIONS:
            home = (_REPO / canonical).read_text(encoding="utf-8")
            assert marker in home, f"{canonical} lost the definition of {marker!r}"
            offenders += [
                f"{path.relative_to(_REPO).as_posix()} defines {marker!r} (canon: {canonical})"
                for path in provider_files
                if marker in path.read_text(encoding="utf-8")
            ]
        assert not offenders, "shared gates defined in provider files: " + "; ".join(offenders)

    def test_architect_reviewer_declares_independence_for_every_adapter(self) -> None:
        """Whether a carrier reviews its own plan is catalogue data, not prose (#474).

        Sibling of `test_every_declared_role_adapter_resolves_to_its_contract`: keyed on
        the declared adapters, so a third carrier cannot arrive with its independence
        unstated. Only `architect_reviewer` declares the map — it is the one role whose
        value changes what the artifact means, because self-review is the case the
        `## Architect review` marker exists to make visible.
        """
        catalogue = yaml.safe_load(
            (_REPO / ".agents" / "orchestration" / "roles.yaml").read_text(encoding="utf-8")
        )
        role = catalogue["roles"]["architect_reviewer"]
        declared = role.get("adapter_independence")
        assert isinstance(declared, dict), "architect_reviewer declares no adapter_independence"
        assert set(declared) == set(role["adapters"]), (
            f"adapter_independence keys {sorted(declared)} do not match "
            f"adapters {sorted(role['adapters'])}"
        )
        assert set(declared.values()) <= {"independent", "self"}, (
            f"unknown independence values: {sorted(set(declared.values()))}"
        )
        assert "self" in declared.values(), (
            "no carrier is marked self-review, so the gate's visible case became unreachable"
        )

    def test_architect_contract_names_self_review_and_its_limit(self) -> None:
        """Permission and its cost travel together, or only the permission survives."""
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        contract = process.split("## Architect review contract", maxsplit=1)[1].split("\n## ")[0]
        assert "self-review" in contract, "the contract stopped naming self-review"
        assert "adapter_independence" in contract, (
            "the contract no longer names the catalogue field the gate reads"
        )
        for limit in ("shared session context", "not an independent"):
            assert limit in contract, f"the contract lost the self-review limit {limit!r}"

    def test_canonical_process_defines_the_architect_review_contract(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        assert "## Architect review contract" in process
        assert "### Findings format" in process
        for word in ("BLOCKING", "SHOULD-FIX", "NICE-TO-HAVE", "confidence"):
            assert word in process, f"the canonical findings contract lost {word!r}"

        reviewer = (_REPO / ".claude" / "agents" / "architect-reviewer.md").read_text(
            encoding="utf-8"
        )
        assert _ARCHITECT_CONTRACT_ANCHOR in reviewer, (
            "the Claude reviewer adapter stopped pointing at the canonical contract"
        )

    def test_goal_function_canon_lives_in_the_principles_document(self) -> None:
        """The shared goal function is readable without opening a provider directory.

        The architect-review contract tells a reviewer to read it, so leaving the
        canon under `.claude/` would keep exactly the Claude-only dependency this
        change removes (#452).
        """
        principles = (_REPO / "docs" / "architecture" / "principles.md").read_text(encoding="utf-8")
        assert "## Goal function" in principles
        assert "### Scripts over instructions" in principles

        mindset = (_REPO / ".claude" / "rules" / "mindset.md").read_text(encoding="utf-8")
        assert "principles.md#goal-function" in mindset, (
            "the Claude rules file no longer points at the goal-function canon"
        )

    def test_codex_adapter_ends_the_loop_on_the_gate_verdict(self) -> None:
        skill = (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "review/fix loop" in skill
        assert "python -m scripts.review_gate" in skill
        assert "not ready" in skill

    def test_review_fix_loop_stop_rule_lives_only_in_the_gate(self) -> None:
        """The condition failed twice as prose (#458, #465); it gets one home now (#467)."""
        homes = {
            "agent-process.md": _REPO / "docs" / "architecture" / "agent-process.md",
            "SKILL.md": _REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md",
            "AGENTS.md": _REPO / "AGENTS.md",
            # Every `fixer` adapter is a home for the loop decision, not only the
            # Codex one — an unenrolled adapter is free to end the loop on its own
            # reading of the findings, which is the #458/#465 recurrence (#473).
            "implement.md": _REPO / ".claude" / "commands" / "implement.md",
        }
        texts = {name: path.read_text(encoding="utf-8") for name, path in homes.items()}

        restating = [name for name, text in texts.items() if _STOP_CONDITION in text]
        assert restating == ["agent-process.md"], f"stop condition restated in {restating}"
        for name, text in texts.items():
            assert "scripts/review_gate.py" in text or "scripts.review_gate" in text, (
                f"{name} does not route the loop decision through the gate"
            )

    def test_documented_review_gate_verdicts_match_the_implementation(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")

        for verdict, exit_code in VERDICT_EXIT_CODES.items():
            assert f"| `{verdict}` | `{exit_code}` |" in process

    def test_canonical_contract_and_codex_skill_keep_all_implementer_gates(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        skill = (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for marker in _IMPLEMENTER_CONTRACT_MARKERS:
            assert marker in process, f"canonical process lost {marker!r}"
            assert marker in skill, f"Codex adapter lost {marker!r}"

    def test_priority_gate_is_required_before_issue_branch(self) -> None:
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        delivery = process.split("## Deterministic delivery flow", maxsplit=1)[1]
        skill = (_REPO / ".agents" / "skills" / "implement-issue" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        agents = (_REPO / "AGENTS.md").read_text(encoding="utf-8")
        command = "set_issue_priority.py <N> --check"

        for name, text in (("canonical flow", delivery), ("Codex skill", skill)):
            assert command in text, f"{name} lost the priority pre-flight"
            assert text.index(command) < text.index("issue_branch.py"), (
                f"{name} checks Priority only after branch creation"
            )
        assert "set_issue_priority.py N --check" in agents

    def test_permanent_codex_rules_preserve_post_pr_readiness_gate(self) -> None:
        agents = (_REPO / "AGENTS.md").read_text(encoding="utf-8")
        for marker in ("review/fix loop", "`not ready`", "scripts.review_gate"):
            assert marker in agents, f"AGENTS.md lost {marker!r}"

    def test_review_outcome_enforcement_is_documented_without_a_path_exception(self) -> None:
        """#483: the controller-PR carve-out is gone; one contract applies to all."""
        process = (_REPO / "docs" / "architecture" / "agent-process.md").read_text(encoding="utf-8")
        assert "## Review-controller manual review" not in process
        assert "manual IDE-agent review" not in process
        assert "`clean` and `rework` pass" in process
        assert "`blocking` reds the check" in process
