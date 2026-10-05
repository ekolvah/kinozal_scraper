#!/usr/bin/env python3
"""Session-level Claude post-edit hook (#281).

The navigation policy and the memory checkpoint are the agent-process plugin's hooks;
this module keeps only what the plugin does not ship.

`on-edit` (PostToolUse, matcher `Edit|Write`) reads an adapter payload from stdin and
dispatches two cheap checks in ONE process (one python spawn per edit):

  - `*.py`            → ruff check-only (`ruff format --check` + `ruff check`,
                       NO `--fix`/format mutation — the harness tracks file
                       contents, so rewriting behind its back breaks the next
                       Edit's `old_string` match). Remaining lint → stderr,
                       exit 2 (PostToolUse exit 2 feeds stderr back to the agent
                       without blocking the already-applied edit).
  - `requirements*.in` → a `pip-compile` reminder (the agent process is otherwise only
                       prose — easy to forget; the reminder makes it visible).

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

    kind: str  # "lint" | "setup_broken" | "pipcompile"
    message: str


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


def plan_checks(payload: dict) -> list[str]:
    """Which checks apply to this edit (pure dispatch by file path)."""
    path = edited_path(payload)
    if path is None:
        return []
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
            # `encoding` is mandatory (#364): otherwise Windows decodes ruff output with
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
            # in a tool that exists for visibility (#410).
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
    """Execute edit checks for the edited paths.

    Keep the policy here so a hook adapter only translates its platform payload.
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
    stderr = "\n".join(s.message for s in signals)
    return exit_code(signals), stderr


def run_on_edit(
    payload: dict,
    ruff_runner: Callable[[str], tuple[int, str]] = _run_ruff,
) -> tuple[int, str]:
    """Execute the shared checks for the Claude post-edit payload."""
    path = edited_path(payload)
    return run_on_paths([] if path is None else [path], ruff_runner=ruff_runner)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "on-edit":
        print(
            "Usage: python -m scripts.hooks on-edit  (reads the hook JSON on stdin)",
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
