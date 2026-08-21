"""Codex hook adapter for the shared agent safety and edit-feedback policies."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from typing import Any

from scripts.agent_policy import denied_reason
from scripts.hooks import run_on_paths

_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.MULTILINE)
_MOVE_TO_PATH = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def read_payload(stdin_text: str) -> dict[str, Any]:
    """Parse a Codex hook payload, treating malformed input as no-op."""
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def payload_is_valid(stdin_text: str) -> bool:
    """Report whether a hook payload has the documented object shape."""
    try:
        return isinstance(json.loads(stdin_text), dict)
    except json.JSONDecodeError:
        return False


def edited_paths(payload: dict[str, Any]) -> list[str]:
    """Extract added and updated paths from Codex's apply-patch command text."""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return []
    paths = [*_PATCH_PATH.findall(command), *_MOVE_TO_PATH.findall(command)]
    return list(dict.fromkeys(path.strip() for path in paths if path.strip()))


def pre_tool_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return Codex's documented PreToolUse denial shape when needed."""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    reason = denied_reason(command)
    if reason is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def run_on_edit(payload: dict[str, Any]) -> tuple[int, str]:
    """Run the transport-neutral checks for every path changed by one patch."""
    return run_on_paths(edited_paths(payload))


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"pre-tool", "on-edit"}:
        print("Usage: python scripts/codex_hooks.py {pre-tool|on-edit}", file=sys.stderr)
        raise SystemExit(2)

    stdin_text = sys.stdin.read()
    payload = read_payload(stdin_text)
    if args[0] == "pre-tool":
        if not payload_is_valid(stdin_text):
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                "Blocked by repository policy: malformed Codex hook payload."
                            ),
                        }
                    }
                )
            )
            raise SystemExit(2)
        response = pre_tool_response(payload)
        if response is not None:
            print(json.dumps(response))
        return

    code, stderr = run_on_edit(payload)
    if stderr:
        print(stderr, file=sys.stderr)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
