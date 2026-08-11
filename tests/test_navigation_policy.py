"""The navigation policy and its Claude PreToolUse adapter (#485).

The policy replaces the static `permissions.deny` navigation block: a static pattern
cannot carry a denial message and cannot tell `grep FILE` (a tool replaces it) from
`cmd | grep` (nothing does). Both properties are asserted here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.hooks import pre_bash_response
from scripts.navigation_policy import navigation_hint

_REPO = Path(__file__).resolve().parents[1]
_CLAUDE_SETTINGS = _REPO / ".claude" / "settings.json"

# Commands whose filesystem-reading form the hook owns. A static deny on any of them
# would match first and swallow the message (a deny rule blocks before a hook runs).
_OWNED = ("ls", "find", "cat", "sed", "grep", "head", "tail")


def _settings() -> Any:
    """The parsed settings file; shape is asserted by the tests that read it."""
    return json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("command", "tool"),
    (
        ("ls tests/", "Glob"),
        ("ls", "Glob"),
        ("ls -la src/", "Glob"),
        ('find . -name "*.py"', "Glob"),
        ("cat README.md", "Read"),
        ("cat src/a.py src/b.py", "Read"),
        ("sed -n '1,40p' CLAUDE.md", "Read"),
        ('grep -rn "foo" src/', "Grep"),
        # The static pattern `Bash(grep -r*)` missed these two; the parser does not.
        ('grep -nr "foo" src/', "Grep"),
        ('grep -n -r "foo" src/', "Grep"),
        # Not reachable by a static pattern at all: `grep`/`head`/`tail` reading a file
        # look exactly like their pipe form until the operands are counted.
        ('grep -n "foo" scripts/hooks.py', "Grep"),
        ("head -40 scripts/hooks.py", "Read"),
        ("tail -n 20 scripts/hooks.py", "Read"),
    ),
)
def test_filesystem_reads_are_denied_with_the_replacement_named(command: str, tool: str) -> None:
    hint = navigation_hint(command)
    assert hint is not None, command
    assert tool in hint, hint


@pytest.mark.parametrize(
    "command",
    (
        # Trimming another command's output. No tool expresses this, so it stays allowed.
        "git log --oneline | head -40",
        "git status --short | tail -5",
        "gh issue view 485 | grep -n fix",
        "git diff | sed -n '1,20p'",
        "git diff | cat",
        "python -m pytest | grep -c passed",
        # `grep -A 3 pattern` downstream: the value flag must not be read as a file.
        "git log | grep -A 3 fix",
        # Untouched toolchain.
        "python scripts/ci_check.py",
        "git commit -m 'cat the file'",
        "gh pr view 497",
        # No line-counting tool exists, so `wc` is not owned.
        "wc -l scripts/hooks.py",
    ),
)
def test_pipe_stages_and_toolchain_stay_allowed(command: str) -> None:
    assert navigation_hint(command) is None, command


@pytest.mark.parametrize(
    "command",
    (
        "cd tests && ls",
        "ls tests/ | head -3",
        "python -m pytest -q; cat README.md",
        # `sh -c` is not unwrapped by Claude Code's matcher — it was the documented hole
        # in the static list, and the parser closes it.
        'sh -c "cat README.md"',
        'bash -c "grep -rn foo src/"',
    ),
)
def test_a_denied_stage_is_found_anywhere_in_a_compound_command(command: str) -> None:
    assert navigation_hint(command) is not None, command


def test_heredoc_write_is_routed_to_the_edit_tools() -> None:
    hint = navigation_hint("cat > notes.md <<'EOF'")
    assert hint is not None
    assert "Write" in hint and "Edit" in hint


def test_unparseable_command_fails_open() -> None:
    """An unbalanced quote is a lexer limit, not a violation: never block on it."""
    assert navigation_hint('grep "unterminated src/') is None


class TestClaudeAdapter:
    def test_denial_uses_the_documented_pretooluse_shape(self) -> None:
        response = pre_bash_response({"tool_input": {"command": "cat README.md"}})
        assert response is not None
        specific = response["hookSpecificOutput"]
        assert specific["hookEventName"] == "PreToolUse"
        assert specific["permissionDecision"] == "deny"
        assert "Read" in specific["permissionDecisionReason"]

    def test_allowed_command_returns_no_decision(self) -> None:
        assert pre_bash_response({"tool_input": {"command": "git status"}}) is None

    def test_malformed_payload_is_a_no_op(self) -> None:
        """Fail-open, unlike the Codex security adapter: a payload bug must not brick Bash."""
        assert pre_bash_response({}) is None
        assert pre_bash_response({"tool_input": {"command": None}}) is None


class TestClaudeHookWiring:
    def test_pretooluse_hook_is_wired_for_bash(self) -> None:
        entries = _settings()["hooks"]["PreToolUse"]
        matching = [entry for entry in entries if entry.get("matcher") == "Bash"]
        assert len(matching) == 1
        commands = [hook["command"] for hook in matching[0]["hooks"]]
        assert any(re.search(r"scripts\.hooks pre-bash", command) for command in commands)

    def test_no_static_deny_shadows_the_hook(self) -> None:
        """A matching `permissions.deny` rule blocks before the hook runs, so a static
        navigation entry would silently drop the replacement message."""
        patterns = [str(p) for p in _settings()["permissions"]["deny"]]
        shadowing = [
            pattern for pattern in patterns if re.match(rf"Bash\((?:{'|'.join(_OWNED)})\b", pattern)
        ]
        assert not shadowing, f"navigation deny entries shadow the hook: {shadowing}"
