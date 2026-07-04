"""Anti-drift guard for the PostToolUse hook wiring in `.claude/settings.json` (#281).

Mirrors `tests/test_settings_deny.py`: the hook is *declared* in settings.json and
*implemented* in `scripts/hooks.py`; these tests keep the two from silently
drifting (a settings entry pointing at a missing script, or the single-spawn
invariant regressing into two matchers = two python spawns per edit).

Static JSON/file-existence checks only — no network, no ruff run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SETTINGS = _REPO / ".claude" / "settings.json"


def _posttooluse_entries() -> list[dict]:
    data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    return list(data.get("hooks", {}).get("PostToolUse", []))


def _commands() -> list[str]:
    cmds: list[str] = []
    for entry in _posttooluse_entries():
        for hook in entry.get("hooks", []):
            if hook.get("type") == "command":
                cmds.append(str(hook.get("command", "")))
    return cmds


class TestHookWiring:
    def test_hook_command_references_existing_script(self) -> None:
        cmds = _commands()
        assert cmds, "settings.json must declare a PostToolUse command hook"
        assert any("scripts/hooks.py" in c for c in cmds), (
            f"a PostToolUse hook must invoke scripts/hooks.py — got {cmds!r}"
        )
        assert (_REPO / "scripts" / "hooks.py").is_file(), (
            "scripts/hooks.py referenced by settings.json must exist (anti-drift)"
        )

    def test_single_posttooluse_edit_write_entry(self) -> None:
        # Exactly one entry, matching both Edit and Write, calling the `on-edit`
        # subcommand — one python spawn per edit (architect NICE #5), not two.
        entries = _posttooluse_entries()
        edit_write = [e for e in entries if _matches_edit_and_write(str(e.get("matcher", "")))]
        assert len(edit_write) == 1, (
            f"expected exactly one Edit|Write PostToolUse entry (single spawn), got {len(edit_write)}"
        )
        cmds = _commands()
        assert any(re.search(r"scripts/hooks\.py[\"']?\s+on-edit", c) for c in cmds), (
            f"the hook must call `scripts/hooks.py on-edit` — got {cmds!r}"
        )


def _matches_edit_and_write(matcher: str) -> bool:
    """A matcher covers both Edit and Write (e.g. 'Edit|Write' or 'Write|Edit')."""
    tokens = re.split(r"[|\s]+", matcher)
    return "Edit" in tokens and "Write" in tokens
