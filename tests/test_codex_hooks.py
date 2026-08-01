"""Unit tests for the Codex hook adapter and shared edit-feedback path."""

from __future__ import annotations

import pytest

from scripts.codex_hooks import edited_paths, pre_tool_response, read_payload, run_on_edit


def _patch(*paths: str) -> dict:
    command = "*** Begin Patch\n" + "".join(f"*** Update File: {path}\n" for path in paths)
    return {"tool_input": {"command": command}}


class TestPreToolUse:
    def test_forbidden_git_operation_uses_codex_denial_schema(self) -> None:
        response = pre_tool_response({"tool_input": {"command": "git push origin main"}})
        assert response == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Blocked by repository policy: git push to main.",
            }
        }

    def test_safe_command_has_no_hook_response(self) -> None:
        assert (
            pre_tool_response({"tool_input": {"command": "python -m pytest tests/test_x.py"}})
            is None
        )


class TestPostToolUse:
    def test_apply_patch_paths_are_deduplicated_and_ignore_deletes(self) -> None:
        payload = {
            "tool_input": {
                "command": """*** Begin Patch
*** Add File: src/new.py
*** Update File: src/new.py
*** Update File: requirements.in
*** Delete File: src/old.py
*** End Patch"""
            }
        }
        assert edited_paths(payload) == ["src/new.py", "requirements.in"]

    def test_python_edit_uses_shared_ruff_feedback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.codex_hooks as codex_hooks

        monkeypatch.setattr(codex_hooks, "run_on_paths", lambda paths: (2, repr(paths)))
        assert run_on_edit(_patch("src/new.py", "requirements.in")) == (
            2,
            "['src/new.py', 'requirements.in']",
        )

    def test_malformed_payload_is_silent_noop(self) -> None:
        assert read_payload("not json") == {}
        assert run_on_edit({}) == (0, "")
