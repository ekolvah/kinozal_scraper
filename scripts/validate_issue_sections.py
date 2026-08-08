#!/usr/bin/env python3
"""Validate that a GitHub issue body contains all required sections.

Usage: python scripts/validate_issue_sections.py <issue-number>

Exits 0 if all required sections are present and non-empty. A passing issue may
also print a non-blocking reminder for an explicit Out of scope follow-up that
has neither an issue reference nor a wontfix/YAGNI decision (#368). Otherwise
prints the list of gaps to stderr and exits 1. Consumed by role adapters so an
agent does not have to "remember" the hand-off contract.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Sequence

from markdown_it import MarkdownIt

check_orphan_scope = importlib.import_module(
    f"{__package__}.check_orphan_scope" if __package__ else "check_orphan_scope"
)

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
    # Machine-independent provenance of the planning hand-off. The section
    # records that a planner actually validated the artifact and intentionally
    # passes it to an implementer; it does not contain prompts or transcripts.
    "Agent handoff",
)
MIN_CONTENT_CHARS = 5

_MD = MarkdownIt("commonmark")


def _split_by_h2(body: str) -> dict[str, str]:
    """Секции по `## `, разобранные CommonMark-парсером.

    **Почему не regexp.** Markdown не регулярен: заголовок ли строка `## X`, зависит
    от контекста — fenced code block, indented block, HTML-блок. Свой построчный
    разбор здесь уже дал дефект: `## <имя обязательной секции>` внутри ``` создавал
    вторую секцию с тем же ключом и перезаписывал настоящую остатком блока, после чего
    заполненная секция рапортовалась пустой и `/implement` абортил на несуществующей
    проблеме (#426). Догонять это заплатками — лестница без конца (первая заплата
    сама внесла регресс на непарном fence), поэтому разбор отдан
    [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — CommonMark-
    реализации, уже присутствовавшей в дереве транзитивно. Побочная выгода: парсер
    видит документ ровно так, как его отрендерит GitHub, — и `Текст` + `---` он тоже
    считает h2, потому что GitHub считает.
    """
    lines = body.splitlines()
    tokens = _MD.parse(body)
    # (заголовок, строка начала самого заголовка, строка начала его содержимого)
    heads: list[tuple[str, int, int]] = [
        (tokens[i + 1].content.strip(), token.map[0], token.map[1])
        for i, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2" and token.map
    ]
    sections: dict[str, str] = {}
    for index, (title, _, content_start) in enumerate(heads):
        content_end = heads[index + 1][1] if index + 1 < len(heads) else len(lines)
        sections[title.lower()] = "\n".join(lines[content_start:content_end]).strip()
    return sections


def handoff_gaps(content: str) -> list[str]:
    """Return the missing provenance fields in an otherwise present hand-off."""
    normalized = "\n".join(line.strip().lower() for line in content.splitlines())
    required = {
        "planner": "planner:",
        "validation": "validation:",
        "validator command": "validate_issue_sections.py",
        "validation result": "passed",
        "next role": "next role: implementer",
        "handoff status": "handoff: ready",
    }
    return [name for name, marker in required.items() if marker not in normalized]


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
        elif name == "Agent handoff":
            missing_fields = handoff_gaps(content)
            if missing_fields:
                gaps.append(f"{name} (missing: {', '.join(missing_fields)})")
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
        for reminder in check_orphan_scope.format_reminders(n, body):
            print(reminder)
        return
    print(f"error: issue #{n} missing/empty sections:", file=sys.stderr)
    for g in gaps:
        print(f"  - {g}", file=sys.stderr)
    # Самый частый способ «потерять» разом много секций — незакрытый ```: по CommonMark
    # он поглощает остаток документа, и GitHub рендерит их серым кодом. Подсказка вместо
    # эвристики-детектора: искать её самим значило бы снова угадывать намерение автора.
    print(
        "hint: если секции в body видны глазами — проверь незакрытый ``` выше них: "
        "остаток документа становится кодовым блоком и на GitHub тоже",
        file=sys.stderr,
    )
    print("run `/plan #" + str(n) + "` to fill them", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
