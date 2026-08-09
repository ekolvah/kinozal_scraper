"""RED tests for #456: safe updates of an existing delivery PR body."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.update_pr_body import main, normalized_body


class TestNormalizedBody:
    def test_adds_one_canonical_close_line_and_preserves_unicode(self) -> None:
        body = "## Summary\n\nПроверка `$(never-run)` and **Markdown**.\n"

        result = normalized_body(body, 456)

        assert result.count("Closes #456") == 1
        assert "Проверка `$(never-run)` and **Markdown**." in result

    def test_collapses_duplicate_target_lines_but_keeps_other_links(self) -> None:
        body = "Closes #456\n\n## Summary\n\nCloses #999\nCloses #456\n"

        result = normalized_body(body, 456)

        assert result.count("Closes #456") == 1
        assert "Closes #999" in result


class _GhDispatcher:
    def __init__(self, *, refs_fail: bool = False, metadata_fail: bool = False) -> None:
        self.refs_fail = refs_fail
        self.metadata_fail = metadata_fail
        self.calls: list[list[str]] = []
        self.edited_body: str | None = None

    def __call__(
        self, cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)

        def done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, err)

        if cmd[:3] == ["gh", "pr", "view"] and "headRefName,url" in cmd:
            if self.metadata_fail:
                return done(1, err="gh: could not resolve host")
            return done(
                0,
                json.dumps(
                    {
                        "headRefName": "issue-456-fix-tooling-update-pr",
                        "url": "https://github.com/ekolvah/kinozal_scraper/pull/999",
                    }
                ),
            )
        if cmd[:3] == ["gh", "pr", "edit"]:
            body_file = Path(cmd[cmd.index("--body-file") + 1])
            self.edited_body = body_file.read_text(encoding="utf-8")
            return done(0)
        if cmd[:3] == ["gh", "pr", "view"] and "closingIssuesReferences" in cmd:
            if self.refs_fail:
                return done(1, err="gh: rate limit exceeded")
            return done(0, json.dumps({"closingIssuesReferences": [{"number": 456}]}))
        raise AssertionError(f"unexpected command: {cmd}")


class TestMain:
    def test_derives_issue_edits_via_body_file_and_verifies_link(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested = tmp_path / "report.md"
        requested.write_text(
            "## Summary\n\nUnicode: готово. Literal shell: `$(never-run)`.\nCloses #456\nCloses #456\n",
            encoding="utf-8",
        )
        dispatcher = _GhDispatcher()
        monkeypatch.setattr(subprocess, "run", dispatcher)

        main(["999", "--body-file", str(requested)])

        assert dispatcher.calls[0] == [
            "gh",
            "pr",
            "view",
            "999",
            "--json",
            "headRefName,url",
        ]
        edit = dispatcher.calls[1]
        assert edit[:4] == [
            "gh",
            "pr",
            "edit",
            "https://github.com/ekolvah/kinozal_scraper/pull/999",
        ]
        assert "--body-file" in edit
        assert dispatcher.edited_body is not None
        assert dispatcher.edited_body.count("Closes #456") == 1
        assert "Unicode: готово. Literal shell: `$(never-run)`." in dispatcher.edited_body
        assert dispatcher.calls[-1] == [
            "gh",
            "pr",
            "view",
            "https://github.com/ekolvah/kinozal_scraper/pull/999",
            "--json",
            "closingIssuesReferences",
        ]

    def test_non_issue_branch_exits_2_without_edit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body_file = tmp_path / "report.md"
        body_file.write_text("## Summary\n", encoding="utf-8")
        dispatcher = _GhDispatcher()

        def non_issue_metadata(
            cmd: list[str], *args: Any, **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["gh", "pr", "view"] and "headRefName,url" in cmd:
                dispatcher.calls.append(cmd)
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps({"headRefName": "feature/manual", "url": "https://x/pull/999"}),
                    "",
                )
            return dispatcher(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", non_issue_metadata)

        with pytest.raises(SystemExit) as exc:
            main(["999", "--body-file", str(body_file)])

        assert exc.value.code == 2
        assert not any(cmd[:3] == ["gh", "pr", "edit"] for cmd in dispatcher.calls)

    def test_failed_final_linkage_read_is_unknown_not_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        body_file = tmp_path / "report.md"
        body_file.write_text("## Summary\n", encoding="utf-8")
        dispatcher = _GhDispatcher(refs_fail=True)
        monkeypatch.setattr(subprocess, "run", dispatcher)
        monkeypatch.setattr("time.sleep", lambda *_: None)

        with pytest.raises(SystemExit) as exc:
            main(["999", "--body-file", str(body_file)])

        assert exc.value.code == 2
        error = capsys.readouterr().err
        assert "linkage is unknown, not absent" in error
        assert "NOT linked" not in error

    def test_pr_metadata_failure_exits_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        body_file = tmp_path / "report.md"
        body_file.write_text("## Summary\n", encoding="utf-8")
        dispatcher = _GhDispatcher(metadata_fail=True)
        monkeypatch.setattr(subprocess, "run", dispatcher)

        with pytest.raises(SystemExit) as exc:
            main(["999", "--body-file", str(body_file)])

        assert exc.value.code == 2
        assert "could not resolve host" in capsys.readouterr().err
        assert not any(cmd[:3] == ["gh", "pr", "edit"] for cmd in dispatcher.calls)
