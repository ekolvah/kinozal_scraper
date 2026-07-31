"""Гард на битые внутренние ссылки и якоря в `.md` (#427).

**Что стережём.** Доки ссылаются друг на друга якорями секций
(`pipeline.md#trailer-retrieval-and-selection`), а якорь GitHub генерит **из текста
заголовка** — то есть переименование секции молча рвёт все входящие указатели. Ровно это
и делает #427: снимает номера тасок из 12 заголовков в 6 файлах, под которыми висит 16
входящих вхождений в 7 файлах. Детерминируемый шаг «убедись, что ни одна ссылка не
провисла» — случай `mindset.md` §«Скрипты > инструкции»: exit-code, а не пункт в
чек-листе ревьюера.

**Почему `markdown-it-py`, а не регексп.** Две вещи регексп даёт неверно. Ссылка внутри
```-блока — не ссылка, а пример; и текст заголовка нужен *отрендеренный* (`` `X` `` → `X`),
иначе slug не совпадёт с тем, что делает GitHub. Парсер уже прямая dev-зависимость (#426),
так что §VII-платы за него нет.

**Code-span считается указателем наравне со ссылкой.** `project-map.md` держит часть
deep-dive-указателей в backticks (`` `testing.md#eval-harness--trailer-selection` ``) —
по ним не кликают, но гниют они так же. Гард, зелёный при сгнившем указателе, — это §IV
silent-skip, ради которого его и заводят. Требуем якорь: голое `` `file.md` `` — упоминание
файла, а не адрес, и проверять его значило бы краснеть на прозе.

**Скоуп — `git ls-files`, а не обход файловой системы.** `.claude/worktrees/` gitignored
и содержит полные копии репо со старыми доками: `rglob` дал бы красный локально и зелёный
в CI. Скоуп производен от индекса git, поэтому следующий `.md` попадает под инвариант
автоматически.

**Границы гарда, честно.** Ловятся *нерезолвящиеся* ссылки, но не
*неверные-но-резолвящиеся*: указатель на существующий файл, переставший быть домом темы
(случай `principles.md` «coverage gaps → `testing.md`» до #427), для гарда неотличим от
верного. Тот же «presence ≠ correctness», что в `test_doc_headers.py` и
`test_adr_records.py`; расхождение ловит человек на ревью.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import unquote

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

_REPO_ROOT = Path(__file__).resolve().parent.parent

_MD = MarkdownIt("commonmark")

# Правила github-slugger: текст заголовка → lower → выбросить всё, кроме `\w`,
# пробела и дефиса → пробелы в дефисы. `re.UNICODE` не декоративен: `_` и кириллица
# обязаны выжить (`#kinozal_pipeline`, `#operations--как-прод-прогон-исполняется`).
# Dedupe-суффиксы (`-1` на втором одноимённом заголовке) намеренно не реализованы:
# в репо нет ни одной ссылки на дублирующийся заголовок, и спекулятивная ветка
# стоила бы больше, чем страхует (§VII).
_SLUG_DROP = re.compile(r"[^\w\s-]", re.UNICODE)

# Схемы, которые гард не трогает: живость внешнего URL — сеть в CI, чужая доступность
# и другой класс флака.
_EXTERNAL = ("http://", "https://", "mailto:", "tel:")

# Code-span засчитывается за указатель, только если несёт якорь.
_CODE_SPAN_REF = re.compile(r"^[\w./-]+\.md#\S+$", re.UNICODE)

# Каталоги, которые обязаны попасть в скоуп. Проверка идёт по каждому отдельно, а не по
# непустоте объединения: переезд одного каталога остался бы зелёным за счёт остальных —
# «нечего проверять» стало бы неотличимо от «всё в порядке» (§IV, жанр
# `test_doc_headers.py::test_every_scoped_directory_contributes`).
_EXPECTED_SCOPE_DIRS = ("docs/architecture", "docs/adr", ".claude/rules", ".claude/commands")


def slugify(heading_text: str) -> str:
    """Якорь GitHub по тексту заголовка (правила github-slugger)."""
    return _SLUG_DROP.sub("", heading_text.strip().lower()).replace(" ", "-")


def _inline_text(inline_token: Token) -> str:
    return "".join(
        child.content
        for child in (inline_token.children or [])
        if child.type in {"text", "code_inline"}
    )


def anchors_of(markdown: str) -> set[str]:
    """Множество якорей, которые GitHub сгенерит для заголовков документа."""
    tokens = _MD.parse(markdown)
    return {
        slugify(_inline_text(tokens[i + 1]))
        for i, token in enumerate(tokens)
        if token.type == "heading_open"
    }


def link_targets(markdown: str) -> list[str]:
    """Внутренние указатели документа: `[](…)`-ссылки и code-span'ы с якорем.

    ```-блоки отсеиваются самим парсером: их содержимое — токен `fence`, а не `inline`.
    """
    targets: list[str] = []
    for token in _MD.parse(markdown):
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href")
                # `attrGet` типизирован как `str | int | float | None` (атрибуты в markdown-it
                # общие для всех токенов); href — всегда строка, но сузить надо явно.
                if isinstance(href, str) and not href.startswith(_EXTERNAL):
                    # `unquote`: markdown-it percent-энкодит нелатиницу в href, а якорь
                    # сравнивается с slug'ом заголовка, который остаётся кириллицей.
                    targets.append(unquote(href))
            elif child.type == "code_inline" and _CODE_SPAN_REF.match(child.content):
                targets.append(child.content)
    return targets


def target_problem(
    target: str,
    source: Path,
    anchors_for: Callable[[Path], set[str]],
) -> str | None:
    """Что не так с указателем `target` из файла `source`, или `None` если всё цело.

    `anchors_for` — инъекция, чтобы предикат был проверяем на синтетике без файлов.
    """
    path_part, _, anchor = target.partition("#")
    dest = (source.parent / path_part).resolve() if path_part else source
    # `exists()`, а не `is_file()`: доки ссылаются и на каталоги (`docs/adr/`), и на `.py`.
    if not dest.exists():
        return f"нет такого файла: {path_part}"
    if anchor and dest.suffix == ".md" and anchor not in anchors_for(dest):
        return f"нет такого якоря: #{anchor} в {path_part or source.name}"
    return None


def _tracked_docs() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [_REPO_ROOT / line for line in result.stdout.splitlines() if line]


def _anchors_for(path: Path) -> set[str]:
    return anchors_of(path.read_text(encoding="utf-8"))


def _problems(docs: Iterable[Path]) -> list[str]:
    found: list[str] = []
    for doc in docs:
        for target in link_targets(doc.read_text(encoding="utf-8")):
            problem = target_problem(target, doc, _anchors_for)
            if problem:
                found.append(f"{doc.relative_to(_REPO_ROOT).as_posix()} -> {target}: {problem}")
    return found


class TestDocLinks:
    @pytest.mark.parametrize("directory", _EXPECTED_SCOPE_DIRS)
    def test_scope_covers_expected_dirs(self, directory: str) -> None:
        docs = {doc.relative_to(_REPO_ROOT).as_posix() for doc in _tracked_docs()}
        assert any(name.startswith(f"{directory}/") for name in docs), (
            f"в скоуп гарда не попал ни один `.md` из {directory} — либо каталог переехал, "
            f"либо `git ls-files` вернул не то. Пустой скоуп зелёный, и это ровно тот "
            f"вакуум, против которого гард написан (§IV)"
        )

    def test_every_internal_link_resolves(self) -> None:
        problems = _problems(_tracked_docs())
        assert not problems, (
            "битые внутренние указатели (якорь GitHub генерится из текста заголовка, "
            "поэтому переименование секции рвёт все входящие ссылки):\n  " + "\n  ".join(problems)
        )


class TestLinkPredicates:
    """Предикаты на синтетике: без них гард доказывал бы сам себя на зелёном репо."""

    @pytest.mark.parametrize(
        ("heading", "expected"),
        [
            ("Trailer retrieval and selection", "trailer-retrieval-and-selection"),
            ("Eval harness — trailer selection", "eval-harness--trailer-selection"),
            ("`kinozal_pipeline`", "kinozal_pipeline"),
            ("Operations — как прогон исполняется", "operations--как-прогон-исполняется"),
            ("Secret scan (`secrets`, #389)", "secret-scan-secrets-389"),
        ],
    )
    def test_slug_matches_github_rules(self, heading: str, expected: str) -> None:
        assert slugify(_inline_text(_MD.parse(f"## {heading}")[1])) == expected

    def test_missing_file_is_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "a.md"
        source.write_text("[x](nope.md)", encoding="utf-8")
        assert target_problem("nope.md", source, lambda _: set()) is not None

    def test_missing_anchor_is_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "a.md"
        source.write_text("# A", encoding="utf-8")
        (tmp_path / "b.md").write_text("## Real", encoding="utf-8")
        assert target_problem("b.md#gone", source, lambda _: {"real"}) is not None
        assert target_problem("b.md#real", source, lambda _: {"real"}) is None

    def test_directory_target_is_valid(self, tmp_path: Path) -> None:
        (tmp_path / "adr").mkdir()
        source = tmp_path / "a.md"
        source.write_text("# A", encoding="utf-8")
        assert target_problem("adr/", source, lambda _: set()) is None

    def test_link_inside_code_fence_is_not_a_link(self) -> None:
        assert link_targets("```\n[x](ghost.md)\n```\n") == []
        assert link_targets("[x](real.md)") == ["real.md"]

    def test_code_span_anchor_is_checked(self) -> None:
        assert link_targets("см. `testing.md#eval-harness`") == ["testing.md#eval-harness"]
        # Голое упоминание файла — не адрес: проверять его значило бы краснеть на прозе.
        assert link_targets("файл `testing.md` описывает") == []

    def test_cyrillic_anchor_is_decoded(self) -> None:
        assert link_targets("[x](a.md#%D0%BA%D0%B0%D1%80%D1%82%D0%B0)") == ["a.md#карта"]

    def test_external_link_is_skipped(self) -> None:
        assert link_targets("[gh](https://github.com/x) и [m](mailto:a@b.c)") == []
