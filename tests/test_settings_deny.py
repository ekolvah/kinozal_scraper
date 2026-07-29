"""Anti-drift guard for the git-operation prohibitions (#154).

The prohibitions are *declared* in prose in `.claude/commands/implement.md` and
*enforced* in `.claude/settings.json` `permissions.deny`. These tests assert the
enforcement covers every declared prohibition, so the two cannot silently drift.

NOTE: a local deny-list is defense-in-depth for the *typical* command forms only
— Claude Code matches deny by parsing the command, which can be bypassed (shell
chains, env vars, `bash -c`). The authoritative barrier for `main` is GitHub
branch protection. These tests do NOT claim a hermetic sandbox; they only keep
declaration and enforcement in sync.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SETTINGS = _REPO / ".claude" / "settings.json"
_IMPLEMENT = _REPO / ".claude" / "commands" / "implement.md"


def _deny_patterns() -> list[str]:
    data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    return [str(p) for p in data["permissions"]["deny"]]


def _declared_prohibitions() -> list[str]:
    """Inline-code tokens from the `Запреты` line of implement.md."""
    for line in _IMPLEMENT.read_text(encoding="utf-8").splitlines():
        if "Запреты" in line:
            # Only inline-code tokens that are actual git/gh commands or flags —
            # the line also references files (settings.json, the test) in backticks.
            return [
                t for t in re.findall(r"`([^`]+)`", line) if t.startswith(("git ", "gh ", "--"))
            ]
    raise AssertionError("no `Запреты` line found in implement.md")


def _bash_arg(pattern: str) -> str:
    """The command inside a `Bash(...)` deny pattern (empty for non-Bash)."""
    m = re.match(r"Bash\((.*)\)$", pattern)
    return m.group(1) if m else ""


class TestDenyList:
    def test_settings_json_valid_and_deny_nonempty(self) -> None:
        patterns = _deny_patterns()
        assert isinstance(patterns, list) and patterns, "permissions.deny must be a non-empty list"

    def test_implement_prohibitions_all_enforced(self) -> None:
        patterns = _deny_patterns()
        declared = _declared_prohibitions()
        assert declared, "implement.md must declare prohibitions as inline code"

        # Match the full token (incl. git/gh prefix) inside the Bash(...) argument,
        # not a stripped substring of the whole pattern — avoids a token like
        # `git push` false-matching every push-related deny entry.
        args = [_bash_arg(p) for p in patterns]
        missing = [tok for tok in declared if not any(tok in arg for arg in args)]
        assert not missing, (
            f"prohibitions declared in implement.md but not enforced in settings.json deny: {missing}"
        )

        # Push-to-main is declared in prose ("push в main"), not as inline code.
        assert any("origin main" in pat for pat in patterns), (
            "push-to-main must be enforced in settings.json deny"
        )

    def test_polling_sleep_denied(self) -> None:
        """`sleep` as a wait-loop primitive must be denied (#416).

        Deliberately an explicit assert rather than a line in the `Запреты`
        prose: `_declared_prohibitions()` keeps only tokens starting with
        `git `/`gh `/`--`, so a `sleep` entry there would be dropped and this
        guard would stay vacuously green. Same shape as the push-to-main
        assert above — prose-declared rule, explicitly enforced here.

        Scope is honest: this pins the *lexeme*. The harness already blocks a
        foreground `sleep`, and `run_in_background` is a parameter of the same
        `Bash` tool — there is no separate "background" deny form — so the
        entry closes both. It does not stop a poll loop built from repeated
        `Read`, `python -c "time.sleep()"` or `timeout 60`; that part is prose
        in `.claude/rules/mindset.md` §(2).
        """
        args = [_bash_arg(p) for p in _deny_patterns()]
        assert any(a.startswith("sleep") for a in args), (
            "wait-loop `sleep` must be enforced in settings.json deny"
        )
