"""Tests for #320 CI gate: `verify_pr_link` — агент-независимый барьер линковки.

`open_pr.py` делает правый путь дешёвым при создании PR, но вызывается прозой в
`/implement`. Этот гейт делает инвариант **необходимым**: CI-job валит PR из
`issue-N-*` ветки с пустым `closingIssuesReferences` (→ required check → нельзя
смёржить), независимо от того, как PR создан. Переиспользует чистые
`issue_number_from_branch` + `has_closing_reference` из `open_pr` (не дублирует).

`gh` — внешняя граница, мокается через `subprocess.run` seam (§II).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.verify_pr_link import link_required_but_missing, main


class TestLinkRequiredButMissing:
    def test_issue_branch_without_link_fails(self) -> None:
        assert link_required_but_missing("issue-320-x", '{"closingIssuesReferences":[]}') is True

    def test_issue_branch_with_link_passes(self) -> None:
        payload = json.dumps({"closingIssuesReferences": [{"number": 320}]})
        assert link_required_but_missing("issue-320-x", payload) is False

    def test_non_issue_branch_not_required(self) -> None:
        # Fork / ручная ветка не обязана закрывать issue — гейт неприменим даже при
        # пустых refs (иначе всякий не-issue PR был бы red).
        assert link_required_but_missing("feature/x", '{"closingIssuesReferences":[]}') is False


class TestMain:
    def _stub_refs(self, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
        def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, payload, "")

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_exits_1_when_issue_branch_unlinked(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stub_refs(monkeypatch, '{"closingIssuesReferences":[]}')
        with pytest.raises(SystemExit) as exc:
            main(["--branch", "issue-320-x", "--pr", "321"])
        assert exc.value.code == 1
        assert "#320" in capsys.readouterr().err  # §IV: сообщение указывает issue

    def test_passes_when_linked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_refs(monkeypatch, json.dumps({"closingIssuesReferences": [{"number": 320}]}))
        main(["--branch", "issue-320-x", "--pr", "321"])  # не должно бросить SystemExit

    def test_polls_past_async_race(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Фикс #1: на `opened` гейт может обогнать async-вычисление линковки GitHub.
        # Первое чтение пусто, второе — с линковкой. Single-read упал бы exit 1 на
        # корректном PR (ложно-красный merge-gate); поллинг проходит.
        empty = '{"closingIssuesReferences":[]}'
        linked = json.dumps({"closingIssuesReferences": [{"number": 320}]})
        reads = iter([empty, linked])

        def fake_run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, next(reads), "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        main(["--branch", "issue-320-x", "--pr", "321"])  # не должно бросить SystemExit

    def test_passes_for_non_issue_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_refs(monkeypatch, '{"closingIssuesReferences":[]}')
        main(["--branch", "dependabot/pip/x", "--pr", "500"])  # гейт неприменим → ok

    def test_gh_failure_distinct_from_missing_link(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Транзиентный сбой `gh` (auth/rate-limit) НЕ должен маскироваться под
        # «linkage empty» — иначе required-check валит PR с ложным диагнозом (§IV).
        # Отдельный exit 2 (инфра-сбой) ≠ exit 1 (реальное нарушение инварианта).
        def fake_run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "gh: could not resolve host")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc:
            main(["--branch", "issue-320-x", "--pr", "321"])
        assert exc.value.code == 2
        assert "gh" in capsys.readouterr().err.lower()
