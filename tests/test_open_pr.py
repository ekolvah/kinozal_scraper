"""RED tests for #320: `open_pr.py` — надёжная автолинковка PR→issue.

Провал #319: PR не закрыл issue #140 (русское «Закрывает» GitHub не парсит; squash
выбросил `(closes #N)` из тела коммита). Фикс — детерминированно строить английский
`Closes #N` в теле PR (переживает squash) + после create читать `closingIssuesReferences`
и падать видимым сбоем (§IV), если линковка пуста.

`gh`/`git` — внешняя граница, мокаются через `subprocess.run` seam (§II — не мок
внутренней логики). Форма `closingIssuesReferences` сверена вживую: `gh pr view <N>
--json closingIssuesReferences` отдаёт ПЛОСКИЙ массив без `.nodes`.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.open_pr import (
    ensure_closes_line,
    has_closing_reference,
    issue_number_from_branch,
    main,
)


class TestIssueNumberFromBranch:
    def test_extracts_number_from_issue_branch(self) -> None:
        assert issue_number_from_branch("issue-320-slug") == 320

    def test_multidigit_and_hyphen_slug(self) -> None:
        assert issue_number_from_branch("issue-140-trailers-data-prep") == 140

    def test_returns_none_for_non_issue_branch(self) -> None:
        assert issue_number_from_branch("feature/x") is None

    def test_returns_none_for_main(self) -> None:
        assert issue_number_from_branch("main") is None


class TestEnsureClosesLine:
    def test_replaces_bare_placeholder(self) -> None:
        # pull_request_template.md несёт голое `Closes #` — должно стать `Closes #320`.
        body = "## Summary\n\nDid a thing.\n\nCloses #\n\n## Test plan\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result
        assert "Closes #\n" not in result

    def test_injects_when_no_placeholder(self) -> None:
        body = "## Summary\n\nDid a thing.\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result

    def test_idempotent_when_already_correct(self) -> None:
        body = "Closes #320\n\n## Summary\n"
        result = ensure_closes_line(body, 320)
        assert result.count("Closes #320") == 1

    def test_fixes_wrong_number(self) -> None:
        # Скоуп замены — строка-плейсхолдер `Closes …`, чужой номер приводится к N.
        body = "## Summary\n\nCloses #999\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result
        assert "#999" not in result


class TestHasClosingReference:
    def test_true_when_refs_present(self) -> None:
        # Реальная форма (сверено `gh pr view 312`): плоский массив, без `.nodes`.
        payload = json.dumps({"closingIssuesReferences": [{"number": 320, "url": "https://x/320"}]})
        assert has_closing_reference(payload) is True

    def test_false_when_empty(self) -> None:
        # Реальный вывод `gh pr view 319` (наш провальный кейс).
        assert has_closing_reference('{"closingIssuesReferences":[]}') is False


class _GhDispatcher:
    """Дубль внешней границы `gh`/`git`: диспатчит `subprocess.run` по argv, пишет
    вызовы в `calls`. Позволяет проверить оркестрацию `main()` (какие команды и с
    каким body вызваны), не трогая сеть."""

    def __init__(
        self, *, branch: str, existing_pr: dict[str, Any] | None, refs_empty: bool
    ) -> None:
        self.branch = branch
        self.existing_pr = existing_pr
        self.refs_empty = refs_empty
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)

        def done(code: int, out: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, "")

        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return done(0, self.branch + "\n")
        if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
            fields = cmd[cmd.index("--json") + 1]
            if "closingIssuesReferences" in fields:
                refs = [] if self.refs_empty else [{"number": 320, "url": "https://x/320"}]
                return done(0, json.dumps({"closingIssuesReferences": refs}))
            # existing-PR probe (`--json url,body`)
            if self.existing_pr is None:
                return done(1, "")  # no PR for branch
            return done(0, json.dumps(self.existing_pr))
        if cmd[:3] == ["gh", "pr", "create"]:
            return done(0, "https://github.com/ekolvah/kinozal_scraper/pull/999\n")
        if cmd[:3] == ["gh", "pr", "edit"]:
            return done(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    def cmds(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


class TestMainVerification:
    def test_exits_1_when_linkage_empty(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # PR создан, но closingIssuesReferences пусто → видимый сбой (§IV) с PR URL.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty=True)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "999" in err  # PR URL в сообщении
        assert "#320" in err  # remediation указывает issue

    def test_reuses_existing_pr_and_fixes_linkage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Повторный запуск: PR для ветки уже есть, body без линкера → edit, НЕ create.
        disp = _GhDispatcher(
            branch="issue-320-x",
            existing_pr={"url": "https://x/pull/999", "body": "## Summary\n\nDid it.\n"},
            refs_empty=False,
        )
        monkeypatch.setattr(subprocess, "run", disp)
        main(["--title", "T"])
        joined = disp.cmds()
        assert not any(c.startswith("gh pr create") for c in joined), "create не должен вызываться"
        assert any(c.startswith("gh pr edit") for c in joined), "линковка чинится через edit"
        edit_cmd = next(c for c in disp.calls if c[:3] == ["gh", "pr", "edit"])
        assert any("Closes #320" in part for part in edit_cmd)

    def test_exits_2_for_non_issue_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        disp = _GhDispatcher(branch="feature/x", existing_pr=None, refs_empty=False)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])
        assert exc.value.code == 2
