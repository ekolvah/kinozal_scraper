"""Anti-drift guards for the local agent surface (`.claude/agents/*.md`, #392).

A static guard without network or credentials—of the same genre as `tests/test_settings_deny.py`.

**What is guarded.** `model: opus` is an ALIAS, not an ID: according to Claude Code documentation
(https://code.claude.com/docs/en/sub-agents, §Choose a model), the field accepts an alias
(`sonnet`/`opus`/`haiku`/`fable`), a full ID (`claude-opus-5`), or `inherit`
(the default when the field is absent). With the release of Opus 5, the plan-stage reviewer moved to
another model without a line in the diff—§IV: the change is indistinguishable from its absence.
`effort` (same source, frontmatter-field table; values `low|medium|high|xhigh|max`)
**inherits from the session** by default, so without an explicit pin the strictness of
the plan gate depends on the session in which it was invoked—non-reproducible across
contributors.

**Guard boundaries, honestly.** Frontmatter is guarded in full; the prompt body only for the
absence of removed suppression wording (`TestNoSuppressionPhrasing` below). What is NOT caught
is a **semantic paraphrase** of the filter (“be selective”, “write only about important things”).
This residual gap is recorded in the ledger
[`coverage-gaps.md`](../docs/architecture/coverage-gaps.md),
so the rejection is not reopened as work-for-work. The findings-grading contract
(`confidence`/`blocking`) belongs to the `agent-process` plugin's reviewer, outside this
repository, so it is not checked here.

Both invariants run over **every** file under `.claude/agents/`, derived from a glob, so the next
agent enters the rule automatically rather than through a manual list someone will forget to extend.
Today that is `discovery.md` alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_DIR = _REPO_ROOT / ".claude" / "agents"

# Anything the repository does NOT resolve: aliases move to a new generation
# with upstream, floating pointers move with its default, and `inherit` moves
# with the session model. Each lets the agent's quality change without a line in
# the diff (§IV: a change becomes indistinguishable from no change).
UNPINNED_MODEL_VALUES = frozenset(
    {"opus", "sonnet", "haiku", "fable", "default", "latest", "inherit"}
)

# Known suppression phrases removed in #392; this denylist is intentionally exact.
_REMOVED_SUPPRESSION = ("do not inflate", "ruthless", "brevity by default")

# Upstream frontmatter values. Verify documentation on failure rather than
# adjusting this copied set to the file under test.
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _agent_files() -> list[Path]:
    # Claude Code scans recursively, so the invariant must use `rglob` as well.
    return sorted(_AGENTS_DIR.rglob("*.md"))


def _body(path: Path) -> str:
    """Return prompt content without frontmatter."""
    return path.read_text(encoding="utf-8").partition("---")[2].partition("\n---")[2]


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
            "the session model, so the review's rigor is not a repo decision (#392)"
        )
        assert model.lower() not in UNPINNED_MODEL_VALUES, (
            f"{path.name}: `model: {model}` is an alias or `inherit` — it resolves "
            "outside the repo, so the agent moves to another model with no line in "
            "any diff; pin a full id such as `claude-opus-5` (#392)"
        )

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_effort_pinned_to_allowed_level(self, path: Path) -> None:
        effort = str(_frontmatter(path).get("effort", "")).strip().lower()
        assert effort, (
            f"{path.name}: no `effort` in frontmatter — it defaults to inheriting the "
            "session level, so the same review is stricter or laxer depending on who "
            "ran it (#392)"
        )
        assert effort in _EFFORT_LEVELS, (
            f"{path.name}: `effort: {effort}` is not one of {sorted(_EFFORT_LEVELS)}; "
            "an unrecognised value is ignored silently. If the upstream set changed, "
            "check the Claude Code docs and update the set here — do not relax the test"
        )


class TestNoSuppressionPhrasing:
    """Prompt body: removed “be brief” wording stays out (#392).

    An instruction to be “shorter” converts a weak finding into no finding rather than
    low severity. The denylist can only reject excess, so it runs over every agent."""

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_removed_suppression_phrases_stay_out(self, path: Path) -> None:
        body = _body(path).lower()
        present = [phrase for phrase in _REMOVED_SUPPRESSION if phrase in body]
        assert not present, (
            f"{path.name}: suppression phrasing is back in the agent prompt {present} — "
            "it converts a weak finding into no finding at all instead of a low "
            "severity, and a filtered finding is indistinguishable from a review that "
            "never ran (§IV, #392)"
        )
