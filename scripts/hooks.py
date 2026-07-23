#!/usr/bin/env python3
"""Session-level PostToolUse hook: instant feedback after Edit/Write (#281).

Wired in `.claude/settings.json` as a single PostToolUse entry (matcher
`Edit|Write`) calling `python "$CLAUDE_PROJECT_DIR/scripts/hooks.py" on-edit`.
It reads the PostToolUse JSON payload from stdin and dispatches two cheap checks
in ONE process (one python spawn per edit):

  - `*.py`            → ruff check-only (`ruff format --check` + `ruff check`,
                       NO `--fix`/format mutation — the harness tracks file
                       contents, so rewriting behind its back breaks the next
                       Edit's `old_string` match). Remaining lint → stderr,
                       exit 2 (PostToolUse exit 2 feeds stderr back to the agent
                       without blocking the already-applied edit).
  - `requirements*.in` → a `pip-compile` reminder (workflow #7 is otherwise only
                       prose — easy to forget; the reminder makes it visible).
  - a write under the agent's out-of-repo auto-memory dir
                       (`.claude/projects/<slug>/memory/`) → a Memory↔repo
                       checkpoint reminder (#353). The policy "project knowledge
                       → repo, only machine/operator-specific → memory" was prose
                       and got violated twice in one session; the deterministic
                       half (a write *into* the memory dir) is a pure path
                       predicate, so it becomes a forcing-function here instead of
                       a "don't forget" rule. It is a reminder (a *checkpoint
                       question*), not a block: the predicate cannot tell a
                       legitimate machine-specific note from a misplaced process
                       fact (semantic — deliberately not scripted), so it fires on
                       every memory write and asks the agent to confirm.

§IV: a malformed/empty payload is a silent no-op (do not red every edit on a
payload bug), but a ruff *exec* failure (not installed / bad config) is a
VISIBLE, distinct marker — otherwise the agent believes instant-lint runs when
it does not (a silent setup degradation).

This is session-level instant feedback during agentic work; it does NOT replace
`scripts/ci_check.py` (the canonical pre-push gate) and is unrelated to the
pre-commit/tox *framework* declined in #255/#267.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

# ruff exit codes: 0 = clean, 1 = lint findings, >=2 = ruff itself errored.
_RUFF_EXEC_ERROR = 2


@dataclass(frozen=True)
class Signal:
    """A message to surface to the agent. `kind` distinguishes the cause so a
    broken-setup marker is never mistaken for a lint finding (§IV)."""

    kind: str  # "lint" | "setup_broken" | "pipcompile" | "memory_write"
    message: str


# A write under the agent's out-of-repo auto-memory dir: `.claude/projects/<slug>/memory/`.
# Anchored at `(^|/)` so repo-`.claude/rules/*` (no `projects/<x>/memory/` segment) and a
# stray `foo.claude/...` never match; `[^/]+` is the single repo-slug dir component.
_MEMORY_DIR_RE = re.compile(r"(^|/)\.claude/projects/[^/]+/memory/")


def read_payload(stdin_text: str) -> dict:
    """Parse the PostToolUse JSON; tolerant to empty/broken input → {}."""
    stdin_text = (stdin_text or "").strip()
    if not stdin_text:
        return {}
    try:
        data = json.loads(stdin_text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def edited_path(payload: dict) -> str | None:
    """The `tool_input.file_path` of an Edit/Write payload, or None."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path")
    return path if isinstance(path, str) and path else None


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def _is_requirements_in(path: str) -> bool:
    """A pip-compile *source* file: requirements*.in (NOT the generated .txt)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.startswith("requirements") and name.endswith(".in")


def _is_memory_write(path: str) -> bool:
    """A write into the agent's out-of-repo auto-memory dir (#353).

    Pure path predicate (like `_is_python`/`_is_requirements_in`): normalize
    backslashes, then match the `.claude/projects/<slug>/memory/` segment. Matches
    any file under it, including `memory/MEMORY.md` at the root."""
    return _MEMORY_DIR_RE.search(path.replace("\\", "/")) is not None


def plan_checks(payload: dict) -> list[str]:
    """Which checks apply to this edit (pure dispatch by file path)."""
    path = edited_path(payload)
    if path is None:
        return []
    # memory-write before _is_python: a hypothetical `.py` under the memory dir must
    # get the Memory↔repo checkpoint, not a ruff lint run.
    if _is_memory_write(path):
        return ["memory_write"]
    if _is_python(path):
        return ["ruff"]
    if _is_requirements_in(path):
        return ["pipcompile"]
    return []


def classify_ruff_result(returncode: int, output: str) -> Signal | None:
    """Map a ruff run to a Signal: 0 → None (clean); 1 → lint; >=2 → setup_broken."""
    if returncode == 0:
        return None
    if returncode >= _RUFF_EXEC_ERROR:
        return Signal(
            kind="setup_broken",
            message=(
                "ruff could not run (exit "
                f"{returncode}) — instant-lint is NOT active; fix the hook setup:\n"
                f"{output.strip()}"
            ),
        )
    return Signal(kind="lint", message=f"ruff found issues (fix before commit):\n{output.strip()}")


def pipcompile_signal(path: str) -> Signal:
    """Reminder to regenerate the lockfile after editing a requirements*.in."""
    return Signal(
        kind="pipcompile",
        message=(
            f"{path} changed — run `pip-compile {path}` in the SAME commit "
            "(workflow #7) or CI will red on lockfile drift."
        ),
    )


def memory_write_signal(path: str) -> Signal:
    """Memory↔repo checkpoint after a write into the agent's auto-memory dir (#353).

    A *checkpoint question*, not an accusation: the predicate is "wrote into memory",
    whereas the violation is "wrote *process knowledge* into memory" — indistinguishable
    without semantics (deliberately not scripted, §VII). So it fires on every memory
    write, including legitimate machine/operator-specific notes, and asks to confirm."""
    return Signal(
        kind="memory_write",
        message=(
            f"{path} — запись в agent-память. Политика Memory↔repo "
            "(`docs/architecture/project-map.md`): в память идёт ТОЛЬКО "
            "машинно/операторо-специфичное; проектное знание → репо "
            "(`.claude/`, `docs/`, скрипты). Подтверди, что это первое, иначе перенеси."
        ),
    )


def exit_code(signals: list[Signal]) -> int:
    """PostToolUse: exit 2 surfaces stderr to the agent; 0 = nothing to say."""
    return 2 if signals else 0


def _run_ruff(file_path: str) -> tuple[int, str]:
    """Thin I/O wrapper: run ruff check-only on one file. FileNotFoundError
    (ruff not installed) is mapped to the exec-error code so it surfaces (§IV)."""
    combined_out = ""
    worst_rc = 0
    for cmd in (
        [sys.executable, "-m", "ruff", "format", "--check", file_path],
        [sys.executable, "-m", "ruff", "check", file_path],
    ):
        try:
            completed = subprocess.run(cmd, text=True, capture_output=True)
        except FileNotFoundError as exc:  # ruff/python missing → visible, not silent
            return _RUFF_EXEC_ERROR, str(exc)
        # Windows + git-bash can hand back None despite text=True (see #109).
        combined_out += (completed.stdout or "") + (completed.stderr or "")
        worst_rc = max(worst_rc, completed.returncode)
    return worst_rc, combined_out


def run_on_edit(
    payload: dict,
    ruff_runner: Callable[[str], tuple[int, str]] = _run_ruff,
) -> tuple[int, str]:
    """Execute the planned checks for one edit. Returns (exit_code, stderr_text)."""
    path = edited_path(payload)
    signals: list[Signal] = []
    for check in plan_checks(payload):
        if check == "ruff" and path is not None:
            returncode, output = ruff_runner(path)
            sig = classify_ruff_result(returncode, output)
            if sig is not None:
                signals.append(sig)
        elif check == "pipcompile" and path is not None:
            signals.append(pipcompile_signal(path))
        elif check == "memory_write" and path is not None:
            signals.append(memory_write_signal(path))
    stderr = "\n".join(s.message for s in signals)
    return exit_code(signals), stderr


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "on-edit":
        print(
            "Usage: python scripts/hooks.py on-edit  (reads PostToolUse JSON on stdin)",
            file=sys.stderr,
        )
        sys.exit(2)
    payload = read_payload(sys.stdin.read())
    code, stderr = run_on_edit(payload)
    if stderr:
        print(stderr, file=sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
