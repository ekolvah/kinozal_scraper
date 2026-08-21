#!/usr/bin/env python3
"""Session-level Claude hook adapter, plus the shared post-edit checks.

Three events, one entry point (mirroring `scripts/codex_hooks.py`):

  - `pre-bash` (PreToolUse, matcher `Bash`) → `scripts.navigation_policy`, which denies a
    shell route into the filesystem *with the replacement call named*. It replaced a
    static `permissions.deny` block, which could carry no message and could not tell
    `grep FILE` from `cmd | grep`.
  - `pre-read` (PreToolUse, matcher `Read`) → the same policy applied to the other route:
    a slice over the byte budget is denied with the slice that fits handed back.
  - `on-edit` (PostToolUse, matcher `Edit|Write`) → the checks below.

`on-edit` reads an adapter payload from stdin and dispatches two cheap checks
in ONE process (one python spawn per edit):

  - `*.py` → ruff check-only (`ruff format --check` + `ruff check`,
                       NO `--fix`/format mutation — the harness tracks file
                       contents, so rewriting behind its back breaks the next
                       Edit's `old_string` match). Remaining lint → stderr,
                       exit 2 (PostToolUse exit 2 feeds stderr back to the agent
                       without blocking the already-applied edit).
  - `requirements*.in` → a `pip-compile` reminder (the agent process is otherwise only
                       prose — easy to forget; the reminder makes it visible).
  - a write under the agent's out-of-repo auto-memory dir
                       (`.claude/projects/<slug>/memory/`) → a Memory↔repo
                       checkpoint reminder. The policy "project knowledge
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
pre-commit/tox *framework*, which is intentionally outside this adapter.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from scripts.navigation_policy import navigation_hint, read_budget_hint

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
    """A write into the agent's out-of-repo auto-memory dir.

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
            "(see `docs/architecture/agent-process.md`) or CI will red on lockfile drift."
        ),
    )


def memory_write_signal(path: str) -> Signal:
    """Memory↔repo checkpoint after a write into the agent's auto-memory dir.

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
            # `encoding` is mandatory: otherwise Windows decodes ruff output with
            # its system code page, the reader thread dies on the first Cyrillic byte, and
            # finding text is lost although the hook reports “ruff found issues.”
            # `errors="replace"` is deliberate: a visibility tool must not die while
            # decoding a non-UTF-8 byte from a third-party tool. One mangled character is
            # more honest than silence about the whole finding (§IV).
            # The child-side contract (`PYTHONUTF8`) is deliberately absent: ruff itself,
            # a Rust binary that emits UTF-8, writes the pipe, not a Python process whose
            # encoding the interpreter sets. If a shim emits non-UTF-8, `errors="replace"`
            # degrades a character rather than losing the finding.
            completed = subprocess.run(
                cmd, text=True, capture_output=True, encoding="utf-8", errors="replace"
            )
        except FileNotFoundError as exc:  # ruff/python missing → visible, not silent
            return _RUFF_EXEC_ERROR, str(exc)
        if completed.stdout is None or completed.stderr is None:
            # Failed capture means “setup broken,” the same class as missing ruff above,
            # so it uses the same code. Do not raise: unhandled failure yields exit 1,
            # whose hook stderr reaches the user but NOT the agent, reducing visibility
            # in a tool that exists for visibility.
            # Use `combined_out +`, not bare diagnostics: if the first command
            # (`ruff format --check`) already found issues, early return must not discard
            # them—the visibility tool must not lose its own finding.
            return _RUFF_EXEC_ERROR, combined_out + (
                f"capture failed for `{' '.join(cmd)}` (rc={completed.returncode}): "
                f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
            )
        combined_out += completed.stdout + completed.stderr
        worst_rc = max(worst_rc, completed.returncode)
    return worst_rc, combined_out


def run_on_paths(
    paths: list[str],
    ruff_runner: Callable[[str], tuple[int, str]] = _run_ruff,
) -> tuple[int, str]:
    """Execute edit checks for paths supplied by any agent adapter.

    The Claude hook supplies one ``tool_input.file_path`` while the Codex hook
    supplies the paths parsed from an ``apply_patch`` command. Keep the policy
    here so adapters only translate their platform payloads.
    """
    signals: list[Signal] = []
    for path in dict.fromkeys(paths):
        for check in plan_checks({"tool_input": {"file_path": path}}):
            if check == "ruff":
                returncode, output = ruff_runner(path)
                sig = classify_ruff_result(returncode, output)
                if sig is not None:
                    signals.append(sig)
            elif check == "pipcompile":
                signals.append(pipcompile_signal(path))
            elif check == "memory_write":
                signals.append(memory_write_signal(path))
    stderr = "\n".join(s.message for s in signals)
    return exit_code(signals), stderr


def run_on_edit(
    payload: dict,
    ruff_runner: Callable[[str], tuple[int, str]] = _run_ruff,
) -> tuple[int, str]:
    """Execute the shared checks for the Claude post-edit payload."""
    path = edited_path(payload)
    return run_on_paths([] if path is None else [path], ruff_runner=ruff_runner)


def pre_bash_response(payload: dict) -> dict | None:
    """Return Claude's PreToolUse denial shape when a Bash command reads the filesystem.

    Fail-open, unlike the Codex security adapter, which denies on a malformed payload: this
    policy only claims a cheaper route exists, so a payload bug must degrade to
    "no opinion" rather than block every `Bash` call in the session.
    """
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    hint = navigation_hint(command)
    if hint is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": hint,
        }
    }


def pre_read_response(payload: dict) -> dict | None:
    """Return Claude's PreToolUse denial shape when a `Read` slice busts the budget.

    Fail-open for the same reason as `pre_bash_response`: the policy claims only that a
    cheaper route exists, and behind a `Read` matcher a payload bug that denied would take
    the agent's primary way of seeing the repository with it.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    hint = read_budget_hint(
        tool_input.get("file_path"), tool_input.get("offset"), tool_input.get("limit")
    )
    if hint is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": hint,
        }
    }


_PRE_TOOL_USE = {"pre-bash": pre_bash_response, "pre-read": pre_read_response}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"on-edit", *_PRE_TOOL_USE}:
        print(
            "Usage: python -m scripts.hooks {on-edit|pre-bash|pre-read}"
            " (reads the hook JSON on stdin)",
            file=sys.stderr,
        )
        sys.exit(2)
    payload = read_payload(sys.stdin.read())
    if sys.argv[1] in _PRE_TOOL_USE:
        # PreToolUse denies via exit 0 + JSON on stdout; exit 2 would discard the JSON and
        # feed stderr instead, losing the replacement message this hook exists to deliver.
        response = _PRE_TOOL_USE[sys.argv[1]](payload)
        if response is not None:
            print(json.dumps(response))
        sys.exit(0)
    code, stderr = run_on_edit(payload)
    if stderr:
        print(stderr, file=sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
