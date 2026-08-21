"""Anti-drift guards for the local agent surface (`.claude/agents/*.md`, ).

A static guard without network or credentials—of the same genre as `tests/test_agent_review_workflow.py`
(, the cloud half of the same defect) and `tests/test_settings_deny.py`.

**What is guarded.** `model: opus` is an ALIAS, not an ID: according to Claude Code documentation
(https://code.claude.com/docs/en/sub-agents, §Choose a model), the field accepts an alias
(`sonnet`/`opus`/`haiku`/`fable`), a full ID (`claude-opus-5`), or `inherit`
(the default when the field is absent). With the release of Opus 5, the plan-stage reviewer moved to
another model without a line in the diff—§IV: the change is indistinguishable from its absence.
`effort` (same source, frontmatter-field table; values `low|medium|high|xhigh|max`)
**inherits from the session** by default, so without an explicit pin the strictness of
the plan gate depends on the session in which it was invoked—non-reproducible across
contributors.

**The denylist is shared with the cloud guard**—`tests/_model_pin_policy.py`. Keeping copies in
two files was an error in the first version: the sets had already diverged (`fable` was here and
absent there). This is a denylist, so their union is strictly more conservative—it can
only reject too much, not allow too much.

**Guard boundaries, honestly.** Frontmatter is guarded in full; the prompt body follows the
same form as the cloud half (`TestCoverageFirstPrompt` below): presence of a
coverage-first contract + absence of removed suppression wording. What is NOT caught is a
**semantic paraphrase** of the filter (“be selective”, “write only about important things”): no exit code
checks it here or in . This residual gap is recorded in the ledger
[`coverage-gaps.md`](../docs/architecture/coverage-gaps.md),
so the rejection is not reopened as work-for-work.

**Two different scopes are not carelessness.** Frontmatter invariants
(`model`, `effort`) apply to **every** agent: every one needs a pin, and deriving the set from a glob is
precisely so the next agent enters the rule automatically rather than through a manual list someone will forget
to extend. The prompt contract (`TestCoverageFirstPrompt`), by contrast, applies only to those that
**return findings**: requiring `confidence`/`blocking` from an agent that does not return them
guarantees a contributor will weaken the test rather than add a meaningful contract. Enrollment is by
file property, not name: plans `code-critic`, and the `*-reviewer` suffix would silently NOT
enroll a real reviewer—trading a visible trap for a silent one.

Today the repository has exactly one agent, so both scopes are preventive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from _model_pin_policy import UNPINNED_MODEL_VALUES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_DIR = _REPO_ROOT / ".claude" / "agents"

# Since , the provider-neutral process document is the findings-contract home.
_CANONICAL_FINDINGS_HOME = _REPO_ROOT / "docs" / "architecture" / "agent-process.md"

# Known suppression phrases removed in ; this denylist is intentionally exact.
_REMOVED_SUPPRESSION = ("do not inflate", "ruthless", "brevity by default")

# Upstream frontmatter values. Verify documentation on failure rather than
# adjusting this copied set to the file under test.
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

# A findings-format section enrolls a file in the grading contract by property.
_FINDINGS_SECTIONS = ("### Findings format",)

def declares_findings_contract(body: str) -> bool:
    """Whether a file declares, and is bound by, the findings contract."""
    return any(section in body for section in _FINDINGS_SECTIONS)

def _agent_files() -> list[Path]:
    # Claude Code scans recursively, so the invariant must use `rglob` as well.
    return sorted(_AGENTS_DIR.rglob("*.md"))

def _body(path: Path) -> str:
    """Return prompt content without frontmatter, or the full file if absent.

    The canonical process document has no frontmatter and must not collapse to an
    empty body through unconditional partitioning (§IV).
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text
    return text.partition("---")[2].partition("\n---")[2]

def _suppression_scope() -> list[Path]:
    """Apply the suppression denylist to every prompt plus the canonical home.

    The broader scope is conservative and prevents wording from returning to an
    executable prompt after the contract itself moved in .
    """
    return [*_agent_files(), _CANONICAL_FINDINGS_HOME]

def _findings_agents() -> list[Path]:
    """Return the subset of files bound by the findings contract."""
    return [path for path in _suppression_scope() if declares_findings_contract(_body(path))]

def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"{path.name}: no YAML frontmatter block")
    _, _, rest = text.partition("---")
    block, sep, _ = rest.partition("\n---")
    if not sep:
        raise AssertionError(f"{path.name}: unterminated YAML frontmatter block")
    return cast("dict[str, Any]", yaml.safe_load(block) or {})

class TestAgentModelPinned:
    def test_agent_files_are_actually_scanned(self) -> None:
        """Guard against an empty glob: without it, renaming or moving `.claude/agents/`
        leaves both invariants below vacuously green, and “nothing to check” becomes
        indistinguishable from “everything is fine” (§IV)."""
        assert _agent_files(), f"no agent definitions found under {_AGENTS_DIR}"

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_model_is_full_id(self, path: Path) -> None:
        model = str(_frontmatter(path).get("model", "")).strip()
        assert model, (
            f"{path.name}: no `model` in frontmatter — the subagent silently inherits "
            "the session model, so the review's rigor is not a repo decision"
        )
        assert model.lower() not in UNPINNED_MODEL_VALUES, (
            f"{path.name}: `model: {model}` is an alias or `inherit` — it resolves "
            "outside the repo, so the agent moves to another model with no line in "
            "any diff; pin a full id such as `claude-opus-5`"
        )

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_effort_pinned_to_allowed_level(self, path: Path) -> None:
        effort = str(_frontmatter(path).get("effort", "")).strip().lower()
        assert effort, (
            f"{path.name}: no `effort` in frontmatter — it defaults to inheriting the "
            "session level, so the same review is stricter or laxer depending on who "
            "ran it"
        )
        assert effort in _EFFORT_LEVELS, (
            f"{path.name}: `effort: {effort}` is not one of {sorted(_EFFORT_LEVELS)}; "
            "an unrecognised value is ignored silently. If the upstream set changed, "
            "check the Claude Code docs and update the set here — do not relax the test"
        )

class TestFindingsContractScope:
    """Enroll files under the prompt contract by file property, not filename.

    A name (`*-reviewer`) would be the worse signal: plans the `code-critic` agent,
    so a real reviewer would NOT enter the contract and remain silently
    unprotected. The property “declares a findings-format section” follows what
    makes the contract meaningful.
    """

    def test_body_without_findings_section_is_not_enrolled(self) -> None:
        body = "You find repository files and return paths.\n\n## Invocation\n\nAlways.\n"
        assert not declares_findings_contract(body)

    def test_body_with_findings_section_is_enrolled(self) -> None:
        body = "You review a plan.\n\n### Findings format\n\n- **BLOCKING** — ...\n"
        assert declares_findings_contract(body)

class TestCoverageFirstPrompt:
    """Prompt body: the “grade rather than filter” contract (, acceptance #4).

    Mirrors `TestCoverageFirstPrompt` in `tests/test_agent_review_workflow.py`:
    there it is a cloud reviewer and here a local plan reviewer; the defect is the same—
    an instruction to be “shorter” converts a weak finding into no finding rather than
    low severity (confirmed by the reviewer when directly asked, ).

    **Substance guard, not cosmetics:** the model pin is one line, while the change’s
    substance is the rewritten prompt; without these tests the behavior change has no coverage.

    **Two scopes within the class**. The grading contract (`confidence`/`blocking`) applies
    to the file that **declares** it: requiring it of a prompt that does not describe findings forces
    a contributor to weaken the test. The denylist of removed phrases applies to `_suppression_scope()`,
    i.e. all executed prompts plus canonical source: it can only reject excess, and narrowing it to
    declarers would release “be brief” back into the subagent prompt."""

    def test_scope_is_not_empty(self) -> None:
        """§IV on a narrowed list: narrowing does not permit silently collapsing it to zero.
        Without this, renaming the format section empties the class and “we check nobody”
        becomes indistinguishable from “everyone passed.”"""
        assert _findings_agents(), (
            "nothing declares a findings contract — either a section header "
            f"({_FINDINGS_SECTIONS}) drifted or the scope collapsed silently"
        )

    def test_findings_contract_scope_covers_the_canonical_home(self) -> None:
        """The guard follows the canonical source rather than guarding an emptied prompt.

        After moving the contract to `agent-process.md`, the executed subagent prompt stopped
        declaring it. If the denylist ran only over declarers, the removed phrase would silently
        return to the prompt while `test_scope_is_not_empty` stayed green because of a document—
        a hole beneath a checkmark."""
        assert _CANONICAL_FINDINGS_HOME in _findings_agents(), (
            f"{_CANONICAL_FINDINGS_HOME.name} no longer declares the findings contract"
        )
        assert set(_agent_files()) <= set(_suppression_scope()), (
            "an executed agent prompt dropped out of the suppression denylist"
        )

    @pytest.mark.parametrize("path", _suppression_scope(), ids=lambda p: p.name)
    def test_removed_suppression_phrases_stay_out(self, path: Path) -> None:
        body = _body(path).lower()
        present = [phrase for phrase in _REMOVED_SUPPRESSION if phrase in body]
        assert not present, (
            f"{path.name}: suppression phrasing is back in the agent prompt {present} — "
            "it converts a weak finding into no finding at all instead of a low "
            "severity, and a filtered finding is indistinguishable from a review that "
            "never ran (§IV, )"
        )

    @pytest.mark.parametrize("path", _findings_agents(), ids=lambda p: p.name)
    def test_findings_are_graded_not_filtered(self, path: Path) -> None:
        body = _body(path).lower()
        missing = [word for word in ("confidence", "blocking") if word not in body]
        assert not missing, (
            f"{path.name}: the grading contract is incomplete, missing {missing}; "
            "every finding must be reported with a severity bucket and a confidence, "
            "so that filtering happens at the reporting stage, not the search stage"
        )
