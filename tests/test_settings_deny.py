"""Anti-drift check for the Claude command deny list in `.claude/settings.json`.

GitHub branch protection is the authoritative barrier; `permissions.deny` is defense in depth.
The expected entries live here, so dropping one from the settings is a red test rather than a
silent loss.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CLAUDE_SETTINGS = _REPO / ".claude" / "settings.json"

FORBIDDEN_COMMANDS: tuple[str, ...] = (
    "git push origin main",
    "git push origin HEAD:main",
    "git push --force",
    "git push --force-with-lease",
    "git push -f",
    "git push --no-verify",
    "git commit --no-verify",
    "git branch -D",
    "git reset --hard",
    "gh pr merge",
    "gh repo delete",
    "sleep",
)


def _claude_deny_patterns() -> list[str]:
    data = json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    return [str(pattern) for pattern in data["permissions"]["deny"]]


def test_claude_deny_list_covers_every_forbidden_command() -> None:
    patterns = _claude_deny_patterns()
    assert patterns, "Claude settings must retain a non-empty defense-in-depth deny list"
    values: list[str] = []
    for pattern in patterns:
        match = re.match(r"Bash\((.*)\)$", pattern)
        assert match is not None, f"unexpected Claude deny pattern: {pattern!r}"
        values.append(match.group(1))
    missing = [
        command for command in FORBIDDEN_COMMANDS if not any(command in value for value in values)
    ]
    assert not missing, f"Claude deny list is missing forbidden commands: {missing}"
