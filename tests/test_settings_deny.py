"""Anti-drift checks for the shared agent command policy."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.agent_policy import FORBIDDEN_COMMANDS, denied_reason

_REPO = Path(__file__).resolve().parents[1]
_CLAUDE_SETTINGS = _REPO / ".claude" / "settings.json"


def _claude_deny_patterns() -> list[str]:
    data = json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    return [str(pattern) for pattern in data["permissions"]["deny"]]


@pytest.mark.parametrize("command", FORBIDDEN_COMMANDS)
def test_shared_policy_rejects_each_declared_command(command: str) -> None:
    assert denied_reason(command) is not None


def test_claude_defense_in_depth_covers_shared_policy() -> None:
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
    assert not missing, f"Claude deny list drifted from shared policy: {missing}"


@pytest.mark.parametrize(
    "command",
    (
        "git push origin issue-443-fix",
        "git branch -d old-branch",
        "gh pr view 444",
        'git commit -m "sleep on it"',
    ),
)
def test_shared_policy_allows_safe_commands(command: str) -> None:
    assert denied_reason(command) is None
