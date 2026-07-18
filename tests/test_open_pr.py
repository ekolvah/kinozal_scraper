"""RED tests for #320: `open_pr.py` — надёжная автолинковка PR→issue.

Провал #319: PR не закрыл issue #140 (русское «Закрывает» GitHub не парсит; squash
выбросил `(closes #N)` из тела коммита). Фикс — детерминированно строить английский
`Closes #N` в теле PR (переживает squash) + после create читать `closingIssuesReferences`
и падать видимым сбоем (§IV), если линковка пуста.

`gh`/`git` — внешняя граница, мокаются через `subprocess.run` seam (§II — не мок
внутренней логики). `closingIssuesReferences` парсится толерантно к обеим формам —
плоский CLI-массив и `.nodes`-обёртка (см. `TestHasClosingReference`).
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
    def test_injects_when_absent(self) -> None:
        body = "## Summary\n\nDid a thing.\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result

    def test_idempotent_when_already_present(self) -> None:
        body = "Closes #320\n\n## Summary\n"
        result = ensure_closes_line(body, 320)
        assert result.count("Closes #320") == 1

    def test_leaves_other_closes_untouched(self) -> None:
        # Скрипт только ДОБАВЛЯЕТ свою строку, не переписывает чужой текст: legit
        # `Closes #999` (мульти-issue PR) сохраняется, наш `Closes #320` добавлен.
        body = "## Summary\n\nCloses #999\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result
        assert "#999" in result

    def test_bare_placeholder_left_inert(self) -> None:
        # Голый `Closes #` из шаблона GitHub игнорит (нет номера) — не трогаем его
        # regex-хирургией, просто добавляем свою строку с номером.
        body = "## Summary\n\nCloses #\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result


class TestHasClosingReference:
    def test_true_when_flat_array(self) -> None:
        # Текущая CLI-форма: плоский массив без `.nodes`.
        payload = json.dumps({"closingIssuesReferences": [{"number": 320, "url": "https://x/320"}]})
        assert has_closing_reference(payload) is True

    def test_true_when_nodes_wrapper(self) -> None:
        # GraphQL-форма (и куда мог бы переехать будущий `gh`): `.nodes`-обёртка.
        payload = json.dumps({"closingIssuesReferences": {"nodes": [{"number": 320}]}})
        assert has_closing_reference(payload) is True

    def test_false_when_flat_empty(self) -> None:
        # Реальный вывод `gh pr view 319` (наш провальный кейс).
        assert has_closing_reference('{"closingIssuesReferences":[]}') is False

    def test_false_when_nodes_empty(self) -> None:
        assert has_closing_reference('{"closingIssuesReferences":{"nodes":[]}}') is False


class _GhDispatcher:
    """Дубль внешней границы `gh`/`git`: диспатчит `subprocess.run` по argv, пишет
    вызовы в `calls`. Позволяет проверить оркестрацию `main()` (какие команды и с
    каким body вызваны), не трогая сеть."""

    def __init__(
        self,
        *,
        branch: str,
        existing_pr: dict[str, Any] | None,
        refs_empty_reads: int = 0,
        create_fails: bool = False,
    ) -> None:
        # `refs_empty_reads` — сколько ПЕРВЫХ чтений closingIssuesReferences вернут
        # пусто до непустого (моделирует eventual-consistency GitHub после create,
        # пойманную dogfood'ом PR #321). Большое число = линковка так и не появилась.
        self.branch = branch
        self.existing_pr = existing_pr
        self.refs_empty_reads = refs_empty_reads
        self.create_fails = create_fails
        self._refs_reads = 0
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)

        def done(code: int, out: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, "")

        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return done(0, self.branch + "\n")
        if cmd[:3] == ["gh", "pr", "list"]:
            # existing OPEN-PR probe (`--head <branch> --state open --json url,body`).
            # A closed PR is filtered by `--state open` → empty array, forcing create.
            return done(0, json.dumps([self.existing_pr] if self.existing_pr else []))
        if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
            fields = cmd[cmd.index("--json") + 1]
            if "closingIssuesReferences" in fields:
                self._refs_reads += 1
                empty = self._refs_reads <= self.refs_empty_reads
                refs = [] if empty else [{"number": 320, "url": "https://x/320"}]
                return done(0, json.dumps({"closingIssuesReferences": refs}))
            raise AssertionError(f"unexpected gh pr view: {cmd}")
        if cmd[:3] == ["gh", "pr", "create"]:
            if self.create_fails:
                return done(1, "")  # gh pr create ненулевой exit
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
        # Линковка так и не появилась (все чтения пусты) → видимый сбой (§IV) с PR URL.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=99)
        monkeypatch.setattr(subprocess, "run", disp)
        monkeypatch.setattr("time.sleep", lambda *_: None)  # не ждём реальный backoff
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "999" in err  # PR URL в сообщении
        assert "#320" in err  # remediation указывает issue

    def test_retries_linkage_until_populated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Регресс на dogfood PR #321: GitHub считает closingIssuesReferences
        # асинхронно после create — первое чтение пусто, но линковка КОРРЕКТНА.
        # main() должен опросить повторно, не падать exit 1 на первой гонке.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=1)
        monkeypatch.setattr(subprocess, "run", disp)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        main(["--title", "T"])  # не должно бросить SystemExit
        refs_reads = sum(
            1
            for c in disp.calls
            if c[:3] == ["gh", "pr", "view"] and "closingIssuesReferences" in c
        )
        assert refs_reads >= 2, "линковка должна перечитываться после пустого первого чтения"

    def test_reuses_existing_pr_and_fixes_linkage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Повторный запуск: PR для ветки уже есть, body без линкера → edit, НЕ create.
        disp = _GhDispatcher(
            branch="issue-320-x",
            existing_pr={"url": "https://x/pull/999", "body": "## Summary\n\nDid it.\n"},
            refs_empty_reads=0,
        )
        monkeypatch.setattr(subprocess, "run", disp)
        main(["--title", "T"])
        joined = disp.cmds()
        assert not any(c.startswith("gh pr create") for c in joined), "create не должен вызываться"
        assert any(c.startswith("gh pr edit") for c in joined), "линковка чинится через edit"
        edit_cmd = next(c for c in disp.calls if c[:3] == ["gh", "pr", "edit"])
        assert any("Closes #320" in part for part in edit_cmd)

    def test_creates_new_pr_when_no_open_pr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Фикс #4: `gh pr list --state open` не видит ЗАКРЫТЫЙ PR той же ветки →
        # пустой список → скрипт создаёт новый, а не цепляется к мёртвому PR.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=0)
        monkeypatch.setattr(subprocess, "run", disp)
        main(["--title", "T"])
        joined = disp.cmds()
        assert any(c.startswith("gh pr create") for c in joined), "должен создать новый PR"
        assert not any(c.startswith("gh pr edit") for c in joined), (
            "edit мёртвого PR не должен быть"
        )

    def test_exits_1_when_create_fails(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # `gh pr create` упал (нет upstream / уже есть PR / сеть) → видимый exit 1,
        # не тихое продолжение к чтению линковки по пустому url.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, create_fails=True)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])
        assert exc.value.code == 1
        assert "create" in capsys.readouterr().err.lower()

    def test_exits_2_for_non_issue_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        disp = _GhDispatcher(branch="feature/x", existing_pr=None)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])
        assert exc.value.code == 2
