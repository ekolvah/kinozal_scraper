#!/usr/bin/env python3
"""Validate that a GitHub issue body contains all required sections.

Usage: python scripts/validate_issue_sections.py <issue-number>

Exits 0 if all required sections are present and non-empty. Otherwise
prints the list of gaps to stderr and exits 1. Consumed by `/plan` and
`/implement` so the agent does not have to "remember" the contract.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Context / Why",
    "Acceptance criteria",
    "Test plan",
    "Implementation outline",
    "Docs to update",
    "Out of scope",
    # Architect-review findings (or an explicit `skipped: <reason>`). Enforced as
    # a gate so the review is a consciously-decided step, never silently skipped
    # (#150). Persona lives in `.claude/agents/architect-reviewer.md`; criteria in
    # `docs/architecture/principles.md`.
    "Architect review",
    # Link to the MADR record this issue's decision lands in, or an explicit
    # `none: <reason>`. Same shape and same rationale as `Architect review`: whether
    # a decision *deserves* a record is a cost-of-change judgement no script can make,
    # so the gate enforces that the question was **answered**, not that the answer is
    # right (#426). Route and entry filter: `project-map.md` §Canonical-home.
    "ADR",
)
MIN_CONTENT_CHARS = 5

# Открытие/закрытие fenced code block. По CommonMark маркер — ≥3 backtick'а либо ≥3
# тильды с отступом ≤3 пробелов; закрывает блок маркер **того же символа** и не короче
# открывающего. Считать любые три символа парными нельзя: вложенный ```` ```` ```` вокруг
# ``` инвертировал бы состояние на закрывающей строке внутреннего блока.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _scan_sections(body: str, *, honor_fences: bool) -> tuple[dict[str, str], bool]:
    """Секции по `## ` + флаг «остался незакрытый fence»."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        marker = _FENCE.match(line) if honor_fences else None
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            match = re.match(r"^##\s+(.+?)\s*$", line)
            if match:
                if current is not None:
                    sections[current.lower()] = "\n".join(buf).strip()
                current = match.group(1)
                buf = []
                continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current.lower()] = "\n".join(buf).strip()
    return sections, fence is not None


def _split_by_h2(body: str) -> dict[str, str]:
    """Секции по `## `, **вне** fenced code blocks.

    Заголовок внутри ``` — часть примера, а не структура документа. Цена ошибки не
    косметическая: фантомная секция с именем настоящей перезаписывает её содержимое
    остатком кодового блока, и заполненная секция рапортуется пустой (#426).

    **Незакрытый fence → откат к разбору без fence-логики.** Иначе одна непарная строка
    с ``` съедала бы все секции ниже, и гейт рапортовал бы отсутствующими секции,
    которые автор видит в body глазами. Из двух неточностей выбрана прежняя: она хотя бы
    не режет то, что раньше проходило (§IV — врать в сторону «нет секции» дороже всего).
    """
    sections, unterminated = _scan_sections(body, honor_fences=True)
    if unterminated:
        return _scan_sections(body, honor_fences=False)[0]
    return sections


def find_gaps(body: str, required: Sequence[str] = REQUIRED_SECTIONS) -> list[str]:
    """Пустые/отсутствующие секции из `required`.

    Набор — параметр, а не константа модуля: второй потребитель того же парсера —
    гард MADR-записей (`tests/test_adr_records.py`) со своим списком h2. Форк парсера
    завёл бы вторую реализацию «что такое пустая секция» (#426).
    """
    sections = _split_by_h2(body)
    gaps: list[str] = []
    for name in required:
        content = sections.get(name.lower())
        if content is None or len(content) < MIN_CONTENT_CHARS:
            gaps.append(name)
    return gaps


def _fetch_body(issue_number: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "body,state"],
        check=True,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.stdout is None:
        # `check=True` уже отсеял ненулевой rc → `None` = сломанный захват (#364).
        # Раньше становился `""`: гейт секций читал бы пустой body и рапортовал
        # «нет ни одной секции» вместо «не смог прочитать issue» (#410).
        raise RuntimeError(f"capture failed for `gh issue view {issue_number}`")
    data = json.loads(result.stdout)
    if data.get("state") != "OPEN":
        print(
            f"error: issue #{issue_number} is not OPEN (state={data.get('state')})", file=sys.stderr
        )
        sys.exit(2)
    return data.get("body") or ""


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_issue_sections.py <issue-number>", file=sys.stderr)
        sys.exit(2)
    try:
        n = int(sys.argv[1])
    except ValueError:
        print(f"error: issue number must be int (got {sys.argv[1]!r})", file=sys.stderr)
        sys.exit(2)
    body = _fetch_body(n)
    gaps = find_gaps(body)
    if not gaps:
        print(f"ok: issue #{n} has all {len(REQUIRED_SECTIONS)} required sections")
        return
    print(f"error: issue #{n} missing/empty sections:", file=sys.stderr)
    for g in gaps:
        print(f"  - {g}", file=sys.stderr)
    print("run `/plan #" + str(n) + "` to fill them", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
