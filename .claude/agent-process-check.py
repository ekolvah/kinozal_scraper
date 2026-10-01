# agent-process:managed
"""Claude `SessionStart` check: is the agent-process skill loaded for this project? (#187)

Usage: python .claude/agent-process-check.py <Install URL>

Silent when exactly one enabled user-scope install of the plugin exists and holds the skill, and
no install of another scope applies to the project. A project-level install is reported with the
command that removes it; any other case, or a check that cannot decide, is `skill not loaded`.
When the project's `.pre-commit-config.yaml` carries the agent-process block and this clone does
not run pre-commit's pre-push hook, or the check cannot tell, it is `pre-push hook not
installed` with the two commands that install it (#270). Any marker is printed in one hook JSON:
a `systemMessage` for the person, one line per marker, and an `additionalContext` for the agent.
It always exits 0: a crashing hook is silent, and the marker is the carrier.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

PLUGIN = "agent-process@agent-process-marketplace"
MARKER = "agent-process skill not loaded"
PROJECT_MARKER = "agent-process project-scope install applies"
PRE_PUSH_MARKER = "agent-process pre-push hook not installed"
PRE_PUSH_FIX = (
    "git config --unset-all core.hooksPath where it is set, then "
    "pre-commit install --hook-type pre-push"
)
# The line pre-commit writes into every hook it installs, as `manual.py` of the installer reads it.
PRE_COMMIT_ID = "# ID: 138fd403232d2ddd5efb44317e38bf03"
BEGIN = "# agent-process:begin"


def verdict(listing: Any, project: str) -> tuple[str, str] | None:
    """`(headline, reason)` when the person must act, `None` when the skill is loaded."""
    if not isinstance(listing, list) or not all(isinstance(e, dict) for e in listing):
        return MARKER, "cannot check: `claude plugin list --json` is not a list of objects"
    here = _folded(project)
    enabled = [e for e in listing if e.get("id") == PLUGIN and e.get("enabled") is True]
    local = [
        e
        for e in enabled
        if e.get("scope") != "user" and _folded(str(e.get("projectPath", ""))) == here
    ]
    if local:
        return PROJECT_MARKER, "; ".join(f"{e.get('version')}: {_removal(e)}" for e in local)
    users = [e for e in enabled if e.get("scope") == "user"]
    if not users:
        return MARKER, f"not installed at user scope: claude plugin install {PLUGIN}"
    installs = {str(entry.get("installPath")): entry for entry in users}
    if len(installs) > 1:
        versions = ", ".join(sorted(str(e.get("version")) for e in installs.values()))
        return MARKER, f"several user-scope installs: {versions}"
    path, entry = next(iter(installs.items()))
    if not (Path(path) / "skills" / "agent-process" / "SKILL.md").is_file():
        return MARKER, f"plugin {entry.get('version')} has no skill"
    return None


def _removal(entry: dict[str, Any]) -> str:
    """The command removing a project-level install: `uninstall` picks the record by the cwd's
    exact spelling, and only cmd's `cd /d` keeps a lowercase drive letter."""
    path = str(entry.get("projectPath"))
    cd = "cd /d" if re.match(r"^[A-Za-z]:", path) else "cd"
    return f'{cd} "{path}" && claude plugin uninstall {PLUGIN} --scope {entry.get("scope")}'


def _folded(path: str) -> str:
    """A project path compared ignoring case on every OS: `normcase` folds only on Windows."""
    return os.path.normpath(path).casefold()


def _verdict(argv: list[str]) -> tuple[str, str] | None:
    if len(argv) != 1:
        return MARKER, "cannot check: the hook passes no Install URL"
    claude = shutil.which("claude")
    if claude is None:
        return MARKER, "cannot check: `claude` is not on PATH"
    done = subprocess.run(
        [claude, "plugin", "list", "--json"],
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )
    if done.returncode != 0:
        return (
            MARKER,
            f"cannot check: `claude plugin list --json` exited {done.returncode}: {done.stderr}",
        )
    if done.stdout is None:
        return MARKER, "cannot check: `claude plugin list --json` output not captured"
    try:
        listing = json.loads(done.stdout)
    except json.JSONDecodeError:
        return MARKER, "cannot check: `claude plugin list --json` printed no JSON"
    return verdict(listing, os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _pre_push(project: Path) -> tuple[str, str] | None:
    """`(PRE_PUSH_MARKER, reason)` when the project renders the pre-push hook and this clone
    does not run pre-commit's hook, `None` otherwise."""
    try:
        lines = (project / ".pre-commit-config.yaml").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    if not any(line.strip() == BEGIN for line in lines):
        return None
    git = shutil.which("git")
    if git is None:
        return PRE_PUSH_MARKER, "cannot check: `git` is not on PATH"
    if _git(git, project, (0, 1), "config", "--get", "core.hooksPath").returncode == 0:
        return PRE_PUSH_MARKER, "core.hooksPath is set"
    done = _git(git, project, (0,), "rev-parse", "--git-path", "hooks/pre-push")
    hook = project / done.stdout.strip()
    try:
        installed = PRE_COMMIT_ID.encode() in hook.read_bytes()
    except FileNotFoundError:
        installed = False
    return None if installed else (PRE_PUSH_MARKER, f"{hook} is not pre-commit's hook")


def _git(
    git: str, project: Path, ok: tuple[int, ...], *args: str
) -> subprocess.CompletedProcess[str]:
    """A git read in the project; an exit outside `ok` or no captured output raises."""
    done = subprocess.run(
        [git, "-C", str(project), *args], capture_output=True, encoding="utf-8", timeout=10
    )
    if done.returncode not in ok or done.stdout is None:
        raise RuntimeError(f"`git {' '.join(args)}` exited {done.returncode}: {done.stderr}")
    return done


def _guarded(read: Callable[[], tuple[str, str] | None], headline: str) -> tuple[str, str] | None:
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - any failure is a reason, never a silent hook
        return headline, f"cannot check: {type(exc).__name__}: {exc}"


def _texts(headline: str, reason: str, argv: list[str]) -> tuple[str, str]:
    """The person's line and the agent's sentence for one marker."""
    if headline == PRE_PUSH_MARKER:
        fix = PRE_PUSH_FIX
        advice = "Tell the person to run these commands in this clone; until then a push runs no local check."
    else:
        fix = argv[0] if argv else "the Install section of the agent-process SKILL.md"
        advice = (
            "Tell the person to run these commands and restart the session."
            if headline == PROJECT_MARKER
            else "Do not fetch, clone or reconstruct it; tell the person and wait."
        )
    return f"{headline} ({reason}) — fix: {fix}", f"{headline} ({reason}). {advice} Fix: {fix}"


def main(argv: list[str]) -> int:
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    found = [
        _guarded(lambda: _verdict(argv), MARKER),
        _guarded(lambda: _pre_push(project), PRE_PUSH_MARKER),
    ]
    texts = [_texts(headline, reason, argv) for headline, reason in filter(None, found)]
    if not texts:
        return 0
    print(
        json.dumps(
            {
                "systemMessage": "\n".join(person for person, _ in texts),
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": " ".join(agent for _, agent in texts),
                },
            }
        )  # ASCII escapes: a Windows console encoding cannot fail the print
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
