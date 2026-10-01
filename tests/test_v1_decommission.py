"""Temporary guard for ADR-0013 step D (#600): v1 is gone, and nothing kept still points at it.

Deleted in the PR's last commit; afterwards `tests/test_doc_links.py` catches dangling links.

**What counts as a reference.** A deleted path's repo-relative form, a deleted directory prefix,
the dotted module form of a deleted `.py` (`scripts.check_red`), the v1 entry tokens, and the
basename — but only when it has an extension and no surviving tracked file shares it. So
`SKILL.md` and `signal-catalogue.json` (surviving twins) and the extensionless `pre-push` (named
legitimately by the pre-commit hook text) are not matched, while `.githooks/pre-push` is still
caught by its path. Bare stems (`check_red`) are not matched: `agent-process check_red` is a
legitimate reference, and `review_gate` / `issue_branches` occur in unrelated senses.

**Scope.** `git ls-files` minus `docs/adr/**` (records are dated by design), minus this file, and
minus the plugin-owned set, so a plugin upgrade cannot red a guard this PR may not fix.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parent.parent

_SCRIPTS = (
    # replaced by the plugin
    "validate_issue_sections", "agent_orchestrator", "check_orphan_scope", "check_red",
    "issue_branch", "new_branch", "set_issue_status", "open_pr", "update_pr_body",
    "verify_pr_link", "review_gate", "gh_io", "check_branch_protection",
    # Codex route
    "codex_hooks", "check_agent_review_outcome", "request_codex_review",
    "check_codex_otel_config", "agent_policy",
    # export machinery
    "agent_process_plugin",
)  # fmt: skip
_TESTS = (
    "validate_issue_sections", "agent_orchestrator", "agent_process", "check_orphan_scope",
    "check_red", "issue_branch", "new_branch", "set_issue_status", "open_pr", "update_pr_body",
    "verify_pr_link", "review_gate", "branch_protection", "agent_review_workflow",
    "codex_hooks", "check_agent_review_outcome", "request_codex_review", "codex_otel_assets",
    "settings_hooks",
    "agent_process_plugin", "agent_process_template", "pr_template",
)  # fmt: skip

DELETED_FILES: tuple[str, ...] = (
    *(f"scripts/{name}.py" for name in _SCRIPTS),
    *(f"tests/test_{name}.py" for name in _TESTS),
    "tests/_model_pin_policy.py",
    ".claude/commands/plan.md",
    ".claude/commands/implement.md",
    ".claude/agents/architect-reviewer.md",
    ".github/workflows/ci.yml",
    ".github/workflows/pr-link.yml",
    ".github/workflows/agent-review-v1.yml",
    ".github/pull_request_template.md",
    "AGENTS.md",
    "docs/architecture/agent-process-export.md",
    ".agents/orchestration/change-classes.yaml",
    ".agents/orchestration/roles.yaml",
    ".agents/orchestration/state.example.json",
    ".agents/skills/implement-issue/SKILL.md",
    ".agents/skills/implement-issue/agents/openai.yaml",
    ".agents/skills/plan-issue/SKILL.md",
    ".agents/skills/plan-issue/agents/openai.yaml",
    ".codex/hooks.json",
    ".githooks/pre-push",
    "observability/codex/config.alloy.example",
    "observability/codex/otel.toml.example",
    "observability/codex/signal-catalogue.json",
)
DELETED_DIRS: tuple[str, ...] = (
    ".agents/",
    ".codex/",
    ".githooks/",
    "templates/agent-process",
    "observability/codex/",
)
V1_TOKENS: tuple[str, ...] = ("/plan #", "/implement #", "$implement-issue")

_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "docs/adr/",
    "openspec/",
    ".claude/commands/opsx/",
    ".claude/skills/openspec-",
)
_EXCLUDED_FILES: frozenset[str] = frozenset(
    {
        "tests/test_v1_decommission.py",
        ".pre-commit-config.yaml",
        ".claude/agent-process-check.py",
        ".github/workflows/agent-process.yml",
        ".github/workflows/agent-review.yml",
    }
)


def _tracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert result.stdout is not None, "capture failed for `git ls-files -z`"
    return [name for name in result.stdout.split("\0") if name]


def _is_deleted(path: str) -> bool:
    return path in DELETED_FILES or path.startswith(DELETED_DIRS)


def _patterns(tracked: list[str]) -> dict[str, re.Pattern[str]]:
    # Basenames come from the explicit list only, so the pattern set is the same before and
    # after the deletion; `templates/agent-process**` is matched by its prefix.
    surviving_names = {PurePosixPath(p).name for p in tracked if not _is_deleted(p)}
    literals = {*DELETED_FILES, *DELETED_DIRS, *V1_TOKENS}
    patterns = {text: re.compile(re.escape(text)) for text in literals}
    for path in DELETED_FILES:
        pure = PurePosixPath(path)
        if pure.suffix == ".py":
            dotted = ".".join(pure.with_suffix("").parts)
            patterns[dotted] = re.compile(rf"(?<![\w.]){re.escape(dotted)}(?!\w)")
        if pure.suffix and pure.name not in surviving_names:
            patterns[pure.name] = re.compile(rf"(?<![\w.-]){re.escape(pure.name)}(?!\w)")
    return patterns


def _in_scope(path: str) -> bool:
    return not (_is_deleted(path) or path in _EXCLUDED_FILES or path.startswith(_EXCLUDED_PREFIXES))


def test_deleted_paths_are_absent() -> None:
    present = sorted(p for p in _tracked() if _is_deleted(p))
    assert not present, f"{len(present)} v1 paths are still tracked: {present}"


def test_no_tracked_file_references_a_deleted_path() -> None:
    tracked = _tracked()
    patterns = _patterns(tracked)
    offenders: dict[str, list[str]] = {}
    for path in filter(_in_scope, tracked):
        try:
            text = (_REPO_ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits = sorted(label for label, pattern in patterns.items() if pattern.search(text))
        if hits:
            offenders[path] = hits
    report = "\n".join(f"{path}: {', '.join(hits)}" for path, hits in sorted(offenders.items()))
    assert not offenders, f"{len(offenders)} kept files reference deleted v1 paths:\n{report}"
