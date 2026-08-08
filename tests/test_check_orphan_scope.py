"""Tests for the non-blocking orphaned-scope reminder (#368)."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import scripts.check_orphan_scope as orphan_scope
from scripts.check_orphan_scope import find_orphan_scope_reminders


def _issue_body(out_of_scope: str, *, context: str = "Normal context.") -> str:
    return (
        f"## Context / Why\n\n{context}\n\n"
        f"## Out of scope\n\n{out_of_scope}\n\n"
        "## ADR\n\nnone: no durable decision.\n"
    )


class TestFindOrphanScopeReminders:
    @pytest.mark.parametrize(
        "bullet",
        (
            "- Follow-up after this change.",
            "- Separate issue for the historical audit.",
            "- Separate ticket for the migration.",
            "- Отдельная задача для миграции.",
        ),
    )
    def test_flags_follow_up_without_issue_reference(self, bullet: str) -> None:
        assert find_orphan_scope_reminders(_issue_body(bullet)) == [bullet.removeprefix("- ")]

    @pytest.mark.parametrize(
        "bullet",
        (
            "- Follow-up tracked by #419.",
            "- Отдельный issue — #420.",
        ),
    )
    def test_accepts_follow_up_with_issue_reference(self, bullet: str) -> None:
        assert find_orphan_scope_reminders(_issue_body(bullet)) == []

    @pytest.mark.parametrize(
        "bullet",
        (
            "- Follow-up classifier is wontfix: semantic judgement stays human.",
            "- Separate issue is YAGNI until a real consumer exists.",
            "- Separate ticket: won't fix because the source is retired.",
        ),
    )
    def test_accepts_wontfix_and_yagni_rejections(self, bullet: str) -> None:
        assert find_orphan_scope_reminders(_issue_body(bullet)) == []

    def test_ignores_markers_outside_out_of_scope_and_in_nested_items(self) -> None:
        body = _issue_body(
            "- No expansion of the current workflow.\n"
            "  - Separate issue mentioned only in a nested detail.",
            context="A follow-up without a number is discussed here.",
        )
        assert find_orphan_scope_reminders(body) == []


class TestReminderCli:
    def test_findings_print_reminder_and_exit_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            orphan_scope,
            "_fetch_body",
            lambda _n: _issue_body("- Follow-up for the historical audit."),
        )

        assert orphan_scope.main(["368"]) == 0
        output = capsys.readouterr().out
        assert "reminder" in output.lower()
        assert "#368" in output
        assert "Follow-up for the historical audit" in output


class TestFetchBody:
    def test_capture_failure_is_not_reported_as_an_empty_issue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert kwargs.get("encoding") == "utf-8"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=None, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="capture failed"):
            orphan_scope._fetch_body(368)
