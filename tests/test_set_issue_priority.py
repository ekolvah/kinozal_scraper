"""RED tests for #351: `set_issue_priority.py` — выставить Priority issue в Project 1.

Приоритет issue живёт как single-select поле Priority в GitHub Project 1. Механика
(«добавь в проект + выставь поле») — детерминированный gh-вызов, который по канону
`principles.md` §Scripts over instructions вынесен в скрипт с exit-code вместо прозы/памяти.

`gh` — единственная внешняя граница, мокается через `subprocess.run` seam (§II — не
мок внутренней логики), как `scripts/open_pr.py`. Захардкоженные option-ID — главный
источник дрейфа, поэтому любой ненулевой exit `gh` обязан быть видимой аномалией
(§IV), а не ложным подтверждением (`test_edit_failure_exits_nonzero`).
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
        # Контракт зашитых option-id (сверено с `gh project field-list 1`).
        assert option_id_for_level("High") == "b9005885"
        assert option_id_for_level("Medium") == "ca573e2f"
        assert option_id_for_level("Low") == "3a2c2352"

    def test_case_insensitive(self) -> None:
        assert option_id_for_level("high") == option_id_for_level("High")
        assert option_id_for_level("LOW") == option_id_for_level("Low")

    def test_rejects_unknown_level(self) -> None:
        # Мусор → видимый ValueError, не тихий None/дефолт.
        with pytest.raises(ValueError):
            option_id_for_level("Urgent")


class TestItemIdFromAddJson:
    def test_extracts_id(self) -> None:
        assert item_id_from_add_json('{"id":"PVTI_x","title":"t"}') == "PVTI_x"

    @pytest.mark.parametrize("bad", ["{}", "", "not json", None])
    def test_raises_on_missing_id(self, bad: str | None) -> None:
        # Пусто / битый JSON → видимая ошибка, не непонятный TypeError позже.
        # `None` в параметрах остаётся как контрактная тотальность самой pure-функции:
        # через `_run` он теперь недостижим (сломанный захват ловится там и выходит
        # кодом 2, #410), но функция обязана оставаться тотальной по своему типу
        # `str | None` — а не полагаться на то, что её единственный сегодняшний
        # вызывающий отсеял этот вход.
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


class _GhDispatcher:
    """Дубль внешней границы `gh`: диспатчит `subprocess.run` по argv, пишет вызовы
    в `calls`. Позволяет проверить оркестрацию `main()` (какие команды и с какими
    флагами), не трогая сеть/GitHub."""

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
            return done(0, '{"url":"https://github.com/ekolvah/kinozal_scraper/issues/351"}')
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
        # Правильные id: item из add, field/project — константы, option — по уровню High.
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
        # B1: item-edit упал (протухший option-id / отзыв доступа к Project / auth) →
        # видимый сбой, НЕ ложное подтверждение из уже полученного item-id (§IV).
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
