"""The navigation policy and its Claude PreToolUse adapters.

The policy replaces the static `permissions.deny` navigation block: a static pattern
cannot carry a denial message and cannot tell `grep FILE` (a tool replaces it) from
`cmd | grep` (nothing does). Both properties are asserted here.

The second route is `Read` itself: the same policy, applied to the byte size of
the slice the tool will actually return rather than to a shell command.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.hooks import pre_bash_response, pre_read_response
from scripts.navigation_policy import _READ_BUDGET_BYTES, navigation_hint, read_budget_hint

_REPO = Path(__file__).resolve().parents[1]
_CLAUDE_SETTINGS = _REPO / ".claude" / "settings.json"

# Commands whose filesystem-reading form the hook owns. A static deny on any of them
# would match first and swallow the message (a deny rule blocks before a hook runs).
_OWNED = ("ls", "find", "cat", "sed", "grep", "head", "tail")


def _settings() -> Any:
    """The parsed settings file; shape is asserted by the tests that read it."""
    return json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))


def _text_file(tmp_path: Path, name: str, size: int) -> Path:
    """A UTF-8 file of at least `size` bytes, in 40-byte lines so slices are addressable."""
    line = "x" * 39 + "\n"
    path = tmp_path / name
    path.write_text(line * (size // len(line) + 1), encoding="utf-8")
    return path


def _over_budget(tmp_path: Path, name: str = "big.py") -> Path:
    return _text_file(tmp_path, name, _READ_BUDGET_BYTES * 2)


def _not_a_string(_: Path) -> object:
    return 12


def _missing(tmp_path: Path) -> str:
    return str(tmp_path / "gone.py")


def _a_directory(tmp_path: Path) -> str:
    return str(tmp_path)


def _undecodable(tmp_path: Path) -> str:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\xff\xfe\x00\x01" * _READ_BUDGET_BYTES)
    return str(path)


def _asset(suffix: str) -> Callable[[Path], str]:
    """A file large enough to bust the budget, in a format where offset/limit make no sense."""

    def make(tmp_path: Path) -> str:
        return str(_over_budget(tmp_path, f"asset{suffix}"))

    return make


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


class TestReadBudget:
    """`Read` is the other route into the filesystem, and the one left ungated."""

    def test_whole_file_read_over_threshold_is_denied(self, tmp_path: Path) -> None:
        hint = read_budget_hint(str(_over_budget(tmp_path)))
        assert hint is not None
        assert "Grep" in hint
        assert "offset" in hint
        # The concrete slice that fits, not a task to guess it.
        assert re.search(r"limit=\d+", hint), hint

    def test_denial_states_the_measured_cost(self, tmp_path: Path) -> None:
        """§IV: the price is on screen when the decision is made, not implied."""
        path = _over_budget(tmp_path, "big.md")
        hint = read_budget_hint(str(path))
        assert hint is not None
        assert str(path.stat().st_size) in hint
        assert "token" in hint

    def test_denial_names_the_whole_file_rewrite_hazard(self, tmp_path: Path) -> None:
        """Slicing a file the agent is about to rewrite must not turn into data loss."""
        hint = read_budget_hint(str(_over_budget(tmp_path)))
        assert hint is not None
        assert "Write" in hint

    def test_file_below_threshold_is_allowed_whole(self, tmp_path: Path) -> None:
        path = _text_file(tmp_path, "small.py", _READ_BUDGET_BYTES // 4)
        assert read_budget_hint(str(path)) is None

    def test_slice_within_budget_is_allowed_on_a_large_file(self, tmp_path: Path) -> None:
        path = _text_file(tmp_path, "huge.py", _READ_BUDGET_BYTES * 4)
        assert read_budget_hint(str(path), offset=1, limit=50) is None

    def test_default_line_limit_on_a_large_file_is_still_denied(self, tmp_path: Path) -> None:
        """`limit` counts LINES and `Read` truncates at 2000 of them; the longest file in
        this repository is 1196 lines, so `limit=2000` returns the whole file. A rule keyed
        on "is `limit` present" would have been a rename, not a policy."""
        assert read_budget_hint(str(_over_budget(tmp_path)), limit=2000) is not None

    @pytest.mark.parametrize(
        "make_path",
        (
            _not_a_string,
            _missing,
            _a_directory,
            _undecodable,
            *map(_asset, (".pdf", ".ipynb", ".png")),
        ),
        ids=("non-string", "missing", "directory", "undecodable", "pdf", "ipynb", "png"),
    )
    def test_fails_open(self, tmp_path: Path, make_path: Callable[[Path], object]) -> None:
        """The policy claims only that a cheaper route exists, so anything it cannot measure
        is "no opinion" — never a block."""
        assert read_budget_hint(make_path(tmp_path)) is None

    def test_fails_open_on_non_integer_slice_arguments(self, tmp_path: Path) -> None:
        """A payload whose `offset`/`limit` cannot be read as line counts is unmeasurable."""
        path = _over_budget(tmp_path)
        assert read_budget_hint(str(path), offset="1", limit="50") is None


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

    def test_read_denial_uses_the_documented_pretooluse_shape(self, tmp_path: Path) -> None:
        response = pre_read_response({"tool_input": {"file_path": str(_over_budget(tmp_path))}})
        assert response is not None
        specific = response["hookSpecificOutput"]
        assert specific["hookEventName"] == "PreToolUse"
        assert specific["permissionDecision"] == "deny"
        assert "Grep" in specific["permissionDecisionReason"]

    def test_read_allowed_call_and_malformed_payload_return_no_decision(
        self, tmp_path: Path
    ) -> None:
        small = _text_file(tmp_path, "small.py", 500)
        assert pre_read_response({"tool_input": {"file_path": str(small)}}) is None
        assert pre_read_response({}) is None
        assert pre_read_response({"tool_input": {"file_path": None}}) is None


@pytest.mark.skipif(
    not _CLAUDE_SETTINGS.is_file(),
    reason="the generated project does not include the optional Claude adapter",
)
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

    def test_pretooluse_hook_is_wired_for_read(self) -> None:
        entries = _settings()["hooks"]["PreToolUse"]
        matching = [entry for entry in entries if entry.get("matcher") == "Read"]
        assert len(matching) == 1
        commands = [hook["command"] for hook in matching[0]["hooks"]]
        assert any(re.search(r"scripts\.hooks pre-read", command) for command in commands)

    def test_no_static_deny_shadows_the_read_hook(self) -> None:
        """A `Read(...)` deny rule would block before the hook runs and drop the budget
        message, leaving the agent with a refusal and no cheaper route named."""
        patterns = [str(p) for p in _settings()["permissions"]["deny"]]
        shadowing = [pattern for pattern in patterns if re.match(r"Read\(", pattern)]
        assert not shadowing, f"static deny entries shadow the read hook: {shadowing}"
