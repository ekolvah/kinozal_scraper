"""RED tests for #519: `set_issue_status.py` — the board Status transitions the process owns.

Project 1 has four Status options; the two middle ones (`Planned`, `In Progress`) have no
built-in Project automation, so they are written from the scripts the planner and the
implementer already run. `Todo` and `Done` stay with the built-in workflows and are rejected
here, so this CLI cannot become a second, competing writer for them.

`gh` is the only external boundary and is mocked through the `subprocess.run` seam (§II), like
`tests/test_set_issue_priority.py`. The hard-coded Project/field/option IDs are the main drift
source, so any nonzero `gh` exit must be a visible anomaly (§IV) rather than a false
confirmation, and `test_project_constants_match_the_priority_script` guards the duplication
that keeping this module importless creates.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import scripts.set_issue_priority as priority_script
import scripts.set_issue_status as status_script
from scripts.set_issue_status import main, option_id_for_status, set_status


class TestOptionIdForStatus:
    def test_maps_planned_and_in_progress(self) -> None:
        # Contract for option IDs verified against `gh project field-list 1 --owner ekolvah`.
        assert option_id_for_status("planned") == "334e22ea"
        assert option_id_for_status("in-progress") == "47fc9ee4"

    @pytest.mark.parametrize("rejected", ["todo", "done", "Todo", "Done", "in progress", ""])
    def test_rejects_todo_done_and_unknown(self, rejected: str) -> None:
        # `Todo`/`Done` are owned by the built-in Project workflows, so they are rejected the
        # same visible way as a typo — never silently mapped to a default (§IV).
        with pytest.raises(ValueError):
            option_id_for_status(rejected)


class _GhDispatcher:
    """`gh` boundary double dispatching by argv and recording calls."""

    def __init__(self, *, edit_fails: bool = False) -> None:
        self.edit_fails = edit_fails
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)

        def done(code: int, out: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, "boom" if code else "")

        if cmd[:3] == ["gh", "issue", "view"]:
            return done(
                0,
                json.dumps({"url": f"https://github.com/ekolvah/kinozal_scraper/issues/{cmd[3]}"}),
            )
        if cmd[:3] == ["gh", "project", "item-add"]:
            return done(0, '{"id":"PVTI_item"}')
        if cmd[:3] == ["gh", "project", "item-edit"]:
            return done(1 if self.edit_fails else 0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    def cmds(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


class TestSetStatus:
    def test_orchestrates_add_then_edit_on_the_status_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disp = _GhDispatcher()
        monkeypatch.setattr(subprocess, "run", disp)

        set_status(519, "in-progress")

        joined = disp.cmds()
        add_i = next(i for i, c in enumerate(joined) if c.startswith("gh project item-add"))
        edit_i = next(i for i, c in enumerate(joined) if c.startswith("gh project item-edit"))
        assert add_i < edit_i, "item-add должен идти до item-edit"
        edit_cmd = next(c for c in disp.calls if c[:3] == ["gh", "project", "item-edit"])
        assert "PVTI_item" in edit_cmd
        assert status_script.STATUS_FIELD_ID in edit_cmd
        assert status_script.PROJECT_ID in edit_cmd
        assert "47fc9ee4" in edit_cmd  # In Progress
        # The write must land on Status, not on the neighbouring single-select.
        assert priority_script.PRIORITY_FIELD_ID not in edit_cmd

    def test_gh_failure_is_visible_and_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A library-level failure raises instead of exiting: the callers are two other CLIs
        # whose own exit codes must keep their meaning, so they decide how loud it is.
        disp = _GhDispatcher(edit_fails=True)
        monkeypatch.setattr(subprocess, "run", disp)

        with pytest.raises(RuntimeError, match="boom"):
            set_status(519, "planned")


class TestMain:
    def test_invalid_status_makes_no_gh_calls(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        disp = _GhDispatcher()
        monkeypatch.setattr(subprocess, "run", disp)

        with pytest.raises(SystemExit) as exc:
            main(["519", "done"])

        assert exc.value.code != 0
        assert disp.calls == [], "gh не должен дёргаться при невалидном статусе"
        assert capsys.readouterr().err.strip() != ""


class TestCli:
    def test_documented_cli_runs_from_a_clean_sys_path(self) -> None:
        """`python scripts/set_issue_status.py` puts `scripts/` on `sys.path`, not the repo root.

        A repository import here would raise `ModuleNotFoundError` on the documented route while
        passing under pytest, which prepends the repo root — the suite would be structurally
        blind to it (B1). Running the real CLI with no arguments makes that observable.
        """
        repo_root = Path(__file__).resolve().parent.parent
        completed = subprocess.run(
            [sys.executable, "scripts/set_issue_status.py"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )

        assert completed.stdout is not None and completed.stderr is not None
        output = completed.stdout + completed.stderr
        assert "ModuleNotFoundError" not in output, output
        assert completed.returncode == 2, output
        assert "usage" in output.lower(), output


def test_project_constants_match_the_priority_script() -> None:
    """One board, two writers: the coordinates are duplicated, so guard the drift (S4/S10)."""
    assert status_script.PROJECT_NUMBER == priority_script.PROJECT_NUMBER
    assert status_script.PROJECT_OWNER == priority_script.PROJECT_OWNER
    assert status_script.PROJECT_ID == priority_script.PROJECT_ID
    assert status_script.STATUS_FIELD_ID != priority_script.PRIORITY_FIELD_ID


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
