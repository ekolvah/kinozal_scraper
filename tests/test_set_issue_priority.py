"""RED tests for #351: `set_issue_priority.py`—set an issue's Priority in Project 1.

Issue priority lives as the Priority single-select field in GitHub Project 1. The mechanism
(“add to project + set field”) is a deterministic `gh` invocation which, following
`principles.md` §Scripts over instructions, is extracted into a script with an exit code rather than prose or memory.

`gh` is the only external boundary and is mocked through the `subprocess.run` seam (§II—not a mock
of internal logic), like `scripts/open_pr.py`. Hard-coded option IDs are the main
source of drift, so any nonzero `gh` exit must be a visible anomaly
(§IV), not a false confirmation (`test_edit_failure_exits_nonzero`).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.set_issue_priority import (
    PRIORITY_FIELD_ID,
    PROJECT_ID,
    item_id_from_add_json,
    main,
    option_id_for_level,
    priority_from_project_json,
)


class TestOptionIdForLevel:
    def test_maps_each_level(self) -> None:
# Contract for option IDs verified against `gh project field-list 1`.
        assert option_id_for_level("High") == "b9005885"
        assert option_id_for_level("Medium") == "ca573e2f"
        assert option_id_for_level("Low") == "3a2c2352"

    def test_case_insensitive(self) -> None:
        assert option_id_for_level("high") == option_id_for_level("High")
        assert option_id_for_level("LOW") == option_id_for_level("Low")

    def test_rejects_unknown_level(self) -> None:
        # Invalid data raises a visible ValueError, never a silent None/default.
        with pytest.raises(ValueError):
            option_id_for_level("Urgent")


class TestItemIdFromAddJson:
    def test_extracts_id(self) -> None:
        assert item_id_from_add_json('{"id":"PVTI_x","title":"t"}') == "PVTI_x"

    @pytest.mark.parametrize("bad", ["{}", "", "not json", None])
    def test_raises_on_missing_id(self, bad: str | None) -> None:
        # Empty or malformed JSON fails visibly. `_run` now rejects capture
        # failure with exit 2 (#410), but this pure function remains total for its
        # declared `str | None` input rather than relying on today's caller.
        with pytest.raises(ValueError):
            item_id_from_add_json(bad)


class TestPriorityFromProjectJson:
    ISSUE_URL = "https://github.com/ekolvah/kinozal_scraper/issues/376"

    def test_reads_priority_for_matching_issue_url(self) -> None:
        payload = json.dumps(
            {
                "items": [
                    {
                        "content": {"url": self.ISSUE_URL, "number": 376},
                        "priority": "High",
                    }
                ]
            }
        )

        assert priority_from_project_json(payload, self.ISSUE_URL) == "High"

    def test_missing_project_item_returns_none(self) -> None:
        payload = json.dumps(
            {
                "items": [
                    {
                        "content": {
                            "url": "https://github.com/ekolvah/other/issues/376",
                            "number": 376,
                        },
                        "priority": "High",
                    }
                ]
            }
        )

        assert priority_from_project_json(payload, self.ISSUE_URL) is None

    def test_missing_priority_returns_none(self) -> None:
        payload = json.dumps({"items": [{"content": {"url": self.ISSUE_URL}}]})

        assert priority_from_project_json(payload, self.ISSUE_URL) is None

    def test_rejects_malformed_payload(self) -> None:
        with pytest.raises(ValueError):
            priority_from_project_json("not json", self.ISSUE_URL)

    def test_rejects_unknown_priority(self) -> None:
        payload = json.dumps(
            {
                "items": [
                    {
                        "content": {"url": self.ISSUE_URL},
                        "priority": "Urgent",
                    }
                ]
            }
        )

        with pytest.raises(ValueError, match="unsupported Priority"):
            priority_from_project_json(payload, self.ISSUE_URL)


class _GhDispatcher:
    """`gh` boundary double dispatching by argv and recording calls.

    It verifies `main()` orchestration without touching the network or GitHub.
    """

    def __init__(self, *, edit_fails: bool = False, project_payload: str | None = None) -> None:
        self.edit_fails = edit_fails
        self.project_payload = project_payload
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
        if cmd[:3] == ["gh", "project", "item-list"]:
            assert self.project_payload is not None
            return done(0, self.project_payload)
        raise AssertionError(f"unexpected command: {cmd}")

    def cmds(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


class TestMain:
    def test_orchestrates_add_then_edit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        disp = _GhDispatcher()
        monkeypatch.setattr(subprocess, "run", disp)
        main(["351", "High"])
        joined = disp.cmds()
        add_i = next(i for i, c in enumerate(joined) if c.startswith("gh project item-add"))
        edit_i = next(i for i, c in enumerate(joined) if c.startswith("gh project item-edit"))
        assert add_i < edit_i, "item-add должен идти до item-edit"
        edit_cmd = next(c for c in disp.calls if c[:3] == ["gh", "project", "item-edit"])
        # IDs: item from add, field/project constants, option selected for High.
        assert "PVTI_item" in edit_cmd
        assert PRIORITY_FIELD_ID in edit_cmd
        assert PROJECT_ID in edit_cmd
        assert "b9005885" in edit_cmd  # High

    def test_bad_level_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        disp = _GhDispatcher()
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["351", "Urgent"])
        assert exc.value.code != 0
        assert disp.calls == [], "gh не должен дёргаться при невалидном уровне"

    def test_edit_failure_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A failed edit is visible, never a false success from the earlier item ID.
        disp = _GhDispatcher(edit_fails=True)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["351", "High"])
        assert exc.value.code != 0
        assert capsys.readouterr().err.strip() != ""


class TestCheckMode:
    def test_priority_present_exits_zero_without_mutation(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = json.dumps(
            {
                "items": [
                    {
                        "content": {"url": "https://github.com/ekolvah/kinozal_scraper/issues/376"},
                        "priority": "High",
                    }
                ]
            }
        )
        disp = _GhDispatcher(project_payload=payload)
        monkeypatch.setattr(subprocess, "run", disp)

        main(["376", "--check"])

        assert "priority is High" in capsys.readouterr().out
        assert all(
            cmd[:3] not in (["gh", "project", "item-add"], ["gh", "project", "item-edit"])
            for cmd in disp.calls
        )

    def test_missing_priority_exits_nonzero_with_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = json.dumps(
            {
                "items": [
                    {"content": {"url": "https://github.com/ekolvah/kinozal_scraper/issues/376"}}
                ]
            }
        )
        disp = _GhDispatcher(project_payload=payload)
        monkeypatch.setattr(subprocess, "run", disp)

        with pytest.raises(SystemExit) as exc:
            main(["376", "--check"])

        assert exc.value.code != 0
        assert "has no Priority" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
