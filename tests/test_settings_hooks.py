"""Anti-drift checks for the repository-local Codex hook adapter."""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_HOOKS = _REPO / ".codex" / "hooks.json"


def _entries(event: str) -> list[dict]:
    data = json.loads(_HOOKS.read_text(encoding="utf-8"))
    return list(data["hooks"][event])


def _commands(event: str) -> list[str]:
    return [
        str(hook["command"])
        for entry in _entries(event)
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]


class TestCodexHookWiring:
    def test_hooks_file_references_existing_adapter(self) -> None:
        assert _HOOKS.is_file()
        commands = [*_commands("PreToolUse"), *_commands("PostToolUse")]
        assert commands
        assert all("scripts/codex_hooks.py" in command for command in commands)
        assert (_REPO / "scripts" / "codex_hooks.py").is_file()

    def test_pretooluse_blocks_shell_commands_before_execution(self) -> None:
        entries = _entries("PreToolUse")
        assert len(entries) == 1
        assert entries[0]["matcher"] == "^Bash$"
        assert any(
            re.search(r"codex_hooks\.py\"? pre-tool", command)
            for command in _commands("PreToolUse")
        )

    def test_posttooluse_checks_all_file_edits_in_one_spawn(self) -> None:
        entries = _entries("PostToolUse")
        matching = [
            entry for entry in entries if _matches_edit_and_write(str(entry.get("matcher", "")))
        ]
        assert len(matching) == 1
        assert any(
            re.search(r"codex_hooks\.py\"? on-edit", command)
            for command in _commands("PostToolUse")
        )


def _matches_edit_and_write(matcher: str) -> bool:
    return {"Edit", "Write"}.issubset(re.split(r"[|\s]+", matcher))
