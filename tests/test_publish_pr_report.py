"""Tests for the fixed-path issue-PR report publisher."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import Mock

import pytest


def _module() -> ModuleType:
    return importlib.import_module("scripts.publish_pr_report")


def test_report_path_is_fixed_inside_repository() -> None:
    module = _module()
    assert module.REPORT_PATH == module.REPO_ROOT / ".codex" / "pr-body.md"
    assert module.REPORT_PATH.is_absolute()


def test_report_path_rejects_symlink() -> None:
    mock_link = Mock(spec=Path)
    mock_link.is_file.return_value = True
    mock_link.is_symlink.return_value = True
    link = cast("Path", mock_link)
    with pytest.raises(ValueError, match="regular non-symlink"):
        _module().read_report(link)


def test_command_line_rejects_caller_supplied_paths() -> None:
    with pytest.raises(SystemExit, match="2"):
        _module().main(["--body-file", "C:/secret.txt"])


def test_documented_script_entry_point_loads_package_imports() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/publish_pr_report.py", "unexpected"],
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.stdout is not None
    assert result.stderr is not None
    assert result.returncode == 2
    assert result.stderr.strip() == "Usage: python scripts/publish_pr_report.py"


class _DeliveryDispatcher:
    """Record and answer the publisher's Git and GitHub subprocess boundary."""

    def __init__(self, *, existing_pr: bool = False, linked: bool = True) -> None:
        self.existing_pr = existing_pr
        self.linked = linked
        self.calls: list[list[str]] = []
        self.edited_body: str | None = None

    def __call__(
        self, command: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)

        def done(output: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, output, "")

        if command == ["git", "remote", "get-url", "origin"]:
            return done("https://github.com/ekolvah/kinozal_scraper.git\n")
        if command == ["git", "branch", "--show-current"]:
            return done("issue-499-publisher-tests\n")
        if command == ["git", "status", "--porcelain"]:
            return done()
        if command[:3] == ["gh", "pr", "list"]:
            existing = (
                [{"url": "https://github.com/ekolvah/kinozal_scraper/pull/999", "body": "old"}]
                if self.existing_pr
                else []
            )
            return done(json.dumps(existing))
        if command == ["gh", "issue", "view", "499", "--json", "title"]:
            return done(json.dumps({"title": "Publish safely"}))
        if command[:3] == ["gh", "pr", "create"]:
            return done("https://github.com/ekolvah/kinozal_scraper/pull/999\n")
        if command[:3] == ["gh", "pr", "edit"]:
            body_path = Path(command[command.index("--body-file") + 1])
            self.edited_body = body_path.read_text(encoding="utf-8")
            return done()
        if command[:3] == ["gh", "pr", "view"]:
            references = [{"number": 499}] if self.linked else []
            return done(json.dumps({"closingIssuesReferences": references}))
        raise AssertionError(f"unexpected command: {command}")


def _use_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str = "## Summary\n"
) -> ModuleType:
    module = _module()
    report = tmp_path / "pr-body.md"
    report.write_text(body, encoding="utf-8")
    monkeypatch.setattr(module, "REPORT_PATH", report)
    return module


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not JSON", "could not parse issue title"),
        ("{}", "could not parse issue title"),
        ('{"title": 499}', "issue title is missing"),
        ('{"title": "   "}', "issue title is missing"),
    ],
)
def test_issue_title_rejects_malformed_or_missing_values(
    payload: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def title_response(
        command: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["gh", "issue", "view", "499", "--json", "title"]
        return subprocess.CompletedProcess(command, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", title_response)

    with pytest.raises(ValueError, match=message):
        _module()._issue_title(499)


def test_main_creates_new_pr_with_derived_title_and_close_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _use_report(tmp_path, monkeypatch)
    dispatcher = _DeliveryDispatcher()
    monkeypatch.setattr(subprocess, "run", dispatcher)

    module.main([])

    create = next(command for command in dispatcher.calls if command[:3] == ["gh", "pr", "create"])
    assert create[create.index("--title") + 1] == "Publish safely"
    assert "Closes #499" in create[create.index("--body") + 1]
    assert capsys.readouterr().out.strip().endswith("/pull/999")


def test_main_updates_existing_pr_with_normalized_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _use_report(
        tmp_path,
        monkeypatch,
        "Closes #499\n## Summary\nCloses #499\n",
    )
    dispatcher = _DeliveryDispatcher(existing_pr=True)
    monkeypatch.setattr(subprocess, "run", dispatcher)

    module.main([])

    assert dispatcher.edited_body is not None
    assert dispatcher.edited_body.count("Closes #499") == 1
    assert not any(command[:3] == ["gh", "pr", "create"] for command in dispatcher.calls)
    assert not any(command[:3] == ["gh", "issue", "view"] for command in dispatcher.calls)


def test_main_exits_1_when_pr_is_not_linked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _use_report(tmp_path, monkeypatch)
    dispatcher = _DeliveryDispatcher(linked=False)
    monkeypatch.setattr(subprocess, "run", dispatcher)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    with pytest.raises(SystemExit) as exc:
        module.main([])

    assert exc.value.code == 1
    assert "not linked" in capsys.readouterr().err.lower()
