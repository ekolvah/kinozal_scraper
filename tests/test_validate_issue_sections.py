from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts.validate_issue_sections import (
    REQUIRED_SECTIONS,
    _fetch_body,
    _split_by_h2,
    find_gaps,
)


def _full_body() -> str:
    parts = []
    for s in REQUIRED_SECTIONS:
        parts.append(f"## {s}\n\nReal content для {s} which is long enough.\n")
    return "\n".join(parts)


# The six sections that predate the `Architect review` gate (#150). Hardcoded on
# purpose: it lets the RED tests below assert that `Architect review` is required
# *independently* of REQUIRED_SECTIONS, so they fail before the gate is added.
_LEGACY_SECTIONS = (
    "Context / Why",
    "Acceptance criteria",
    "Test plan",
    "Implementation outline",
    "Docs to update",
    "Out of scope",
)


def _body_with(sections: tuple[str, ...]) -> str:
    return "\n".join(f"## {s}\n\nReal content для {s} which is long enough.\n" for s in sections)


class TestFindGaps:
    def test_all_sections_filled_returns_no_gaps(self) -> None:
        assert find_gaps(_full_body()) == []

    def test_missing_section_listed(self) -> None:
        body = _full_body().replace(
            "## Out of scope\n\nReal content для Out of scope which is long enough.\n", ""
        )
        assert find_gaps(body) == ["Out of scope"]

    def test_empty_section_listed_even_if_header_present(self) -> None:
        body = "## Context / Why\n\n\n" + "\n".join(
            f"## {s}\n\nReal content для {s} which is long enough.\n" for s in REQUIRED_SECTIONS[1:]
        )
        assert find_gaps(body) == ["Context / Why"]

    def test_whitespace_only_section_listed(self) -> None:
        body = "## Context / Why\n\n   \n\n" + "\n".join(
            f"## {s}\n\nReal content для {s} which is long enough.\n" for s in REQUIRED_SECTIONS[1:]
        )
        assert find_gaps(body) == ["Context / Why"]

    def test_all_sections_missing_returns_all(self) -> None:
        assert find_gaps("") == list(REQUIRED_SECTIONS)

    def test_case_insensitive_header_match(self) -> None:
        body = _full_body().replace("## Context / Why", "## context / why")
        assert find_gaps(body) == []

    def test_extra_section_ignored(self) -> None:
        body = _full_body() + "\n## Extra\n\nNot required.\n"
        assert find_gaps(body) == []

    def test_custom_required_set(self) -> None:
        """Набор секций — параметр, а не константа модуля (#426).

        Второй потребитель — гард MADR-записей (`tests/test_adr_records.py`): у него
        свой список h2, но тот же парсер. Форкнуть парсер значило бы завести вторую
        реализацию «что такое пустая секция» и разойтись с ней (§VII).
        """
        required = ("Context and Problem Statement", "Considered Options", "Decision Outcome")
        body = _body_with(required[:2])
        assert find_gaps(body, required=required) == ["Decision Outcome"]
        assert find_gaps(_body_with(required), required=required) == []

    def test_heading_inside_fenced_block_is_not_a_section(self) -> None:
        """`## ` внутри ``` — часть примера, а не заголовок.

        На issue-body не стреляло, но в MADR-записях примеры разметки вероятнее.
        Цена ошибки не косметическая: фантомная секция с именем **настоящей**
        перезаписывает её содержимое остатком кодового блока — заполненная секция
        рапортуется пустой, и гейт врёт в обе стороны.
        """
        body = _full_body().replace(
            "## Out of scope\n\nReal content для Out of scope which is long enough.\n",
            "## Out of scope\n\nЦитата шаблона:\n\n```md\n## Context / Why\n```\n",
        )
        assert find_gaps(body) == []
        # Строки блока обязаны остаться в своей секции, а не потеряться по дороге.
        assert "## Context / Why" in _split_by_h2(body)["out of scope"]

    def test_unterminated_fence_swallows_the_rest_as_github_renders_it(self) -> None:
        """Незакрытый ``` поглощает остаток документа — и это **верно**.

        По CommonMark незакрытый блок идёт до конца, и GitHub отрендерит все секции
        ниже серым кодом: их там действительно нет. Гейт, сообщающий «секций нет»,
        поэтому не врёт, а называет реальную поломку body — в отличие от догадки,
        которую пришлось бы городить, реши мы «восстановить» намерение автора.
        Подсказку про незакрытый fence несёт сообщение об ошибке (`main`).
        """
        body = _full_body().replace(
            "## Context / Why\n\nReal content для Context / Why which is long enough.\n",
            "## Context / Why\n\nЗабыли закрыть:\n\n```md\n",
        )
        gaps = find_gaps(body)
        assert "Context / Why" not in gaps  # сама секция открылась до fence
        assert "Acceptance criteria" in gaps  # всё, что ниже, — содержимое блока

    def test_setext_heading_counts_as_section(self) -> None:
        """`Текст` + `---` — тоже h2: парсер видит документ как GitHub.

        Regexp-версия такой заголовок не замечала, то есть заполненная через setext
        секция считалась отсутствующей. Тест фиксирует не каприз markdown-it, а
        совпадение гейта с тем, что видит человек в отрендеренной issue.
        """
        body = "\n".join(
            f"{s}\n---\n\nReal content для {s} which is long enough.\n" for s in REQUIRED_SECTIONS
        )
        assert find_gaps(body) == []


class TestArchitectReviewSection:
    """The `Architect review` gate (#150): every issue must carry the section,
    even if filled with an explicit `skipped: <reason>`. Guarantees the review
    is consciously decided, not silently forgotten."""

    def test_architect_review_required(self) -> None:
        # All six legacy sections filled, but no `Architect review` → must be a gap.
        body = _body_with(_LEGACY_SECTIONS)
        assert "Architect review" in find_gaps(body)

    def test_architect_review_filled_passes(self) -> None:
        # Narrow assertion: a filled section is not a gap. Stays correct even if
        # an 8th section is later added (would not fail for the wrong reason).
        body = _body_with((*_LEGACY_SECTIONS, "Architect review"))
        assert "Architect review" not in find_gaps(body)


class TestAdrSection:
    """Гейт `## ADR` (#426): каждая issue несёт секцию — либо ссылку на запись
    в `docs/adr/`, либо явное `none: <причина>`.

    Точно тот же приём, что и с `Architect review` (#150), и по той же причине:
    «нужна ли здесь запись» — суждение cost-of-change, скриптом не вычисляемое,
    поэтому гейтится **наличие решения**, а не его правильность. Без секции шаг
    оставался прозой в `plan.md`, а проза в длинном pipeline пропускается —
    то, что «не забыть сделать X», обязано становиться exit-code'ом
    (`mindset.md` §«Скрипты > инструкции»).
    """

    def test_adr_section_required(self) -> None:
        # Все семь прежних секций заполнены, `ADR` нет → обязан быть gap.
        body = _body_with((*_LEGACY_SECTIONS, "Architect review"))
        assert "ADR" in find_gaps(body)

    def test_adr_section_filled_passes(self) -> None:
        body = _body_with((*_LEGACY_SECTIONS, "Architect review", "ADR"))
        assert "ADR" not in find_gaps(body)


class TestFetchBodyEncoding:
    def test_cyrillic_body_decodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cyrillic_body = "## Context / Why\n\nЭто кириллический контент с символом 0x81 в проблемной кодировке.\n"
        payload = '{"state": "OPEN", "body": ' + _json_string(cyrillic_body) + "}"

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert kwargs.get("encoding") == "utf-8", (
                "subprocess must request utf-8 to avoid cp1252 on Windows"
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _fetch_body(122)
        assert "кириллический" in result


def _json_string(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)
