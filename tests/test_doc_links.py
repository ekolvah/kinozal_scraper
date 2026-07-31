"""Гард на битые внутренние ссылки и якоря в `.md` (#427).

**Что стережём.** Доки ссылаются друг на друга якорями секций
(`pipeline.md#trailer-retrieval-and-selection`), а якорь GitHub генерит **из текста
заголовка** — то есть переименование секции молча рвёт все входящие указатели. Ровно это
и делает #427: снимает номера тасок из 12 заголовков в 6 файлах, под которыми висит 17
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
файла, а не адрес, и проверять его значило бы краснеть на прозе. Обратная сторона: **пример**
якоря в прозе гард примет за настоящий указатель, поэтому иллюстрацию пиши двойными
backtick'ами (`` `file.md#anchor` `` — так её и держат `project-map.md` и `ci.md`), иначе
она уедет в проверку и покраснеет.

**Скоуп — `git ls-files`, а не обход файловой системы.** `.claude/worktrees/` gitignored
и содержит полные копии репо со старыми доками: `rglob` дал бы красный локально и зелёный
в CI. Скоуп производен от индекса git, поэтому следующий `.md` попадает под инвариант
автоматически. Существование цели тоже сверяется **с индексом**, а не с ФС: `Path.exists()`
на Windows регистронезависим, и ссылка `Pipeline.md#…` прошла бы локально, чтобы упасть в
CI на Linux, — тот же local-green/CI-red раскол, ради которого выбран `git ls-files`.

**Границы гарда, честно.** Ловятся *нерезолвящиеся* ссылки, но не
*неверные-но-резолвящиеся*: указатель на существующий файл, переставший быть домом темы
(случай `principles.md` «coverage gaps → `testing.md`» до #427), для гарда неотличим от
верного. Тот же «presence ≠ correctness», что в `test_doc_headers.py` и
`test_adr_records.py`; расхождение ловит человек на ревью. Второй предел — **форма**:
проверяются markdown-ссылки и code-span'ы, но не `![](x.png)` (`image`-токен) и не сырой
`<a href>`/`<img src>` (`html_inline`); сегодня в отслеживаемых `.md` нет ни одного такого.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from functools import cache, lru_cache
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
# и другой класс флака. Детект по форме, а не по денилисту из четырёх схем: денилист
# отправил бы `//host/x` или `vscode:` резолвиться как путь на диске.
_SCHEMELESS_EXTERNAL = ("mailto:", "tel:", "//")


def _is_external(href: str) -> bool:
    return "://" in href or href.startswith(_SCHEMELESS_EXTERNAL)


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
                if isinstance(href, str) and not _is_external(href):
                    # `unquote`: markdown-it percent-энкодит нелатиницу в href, а якорь
                    # сравнивается с slug'ом заголовка, который остаётся кириллицей.
                    targets.append(unquote(href))
            elif child.type == "code_inline" and _CODE_SPAN_REF.match(child.content):
                targets.append(child.content)
    return targets


def target_problem(
    target: str,
    source: Path,
    is_tracked: Callable[[Path], bool],
    anchors_for: Callable[[Path], set[str]],
) -> str | None:
    """Что не так с указателем `target` из файла `source`, или `None` если всё цело.

    `is_tracked` / `anchors_for` — инъекции, чтобы предикат был проверяем на синтетике.
    `is_tracked` принимает и каталоги, и не-`.md`: доки ссылаются на `docs/adr/` и на `.py`.
    """
    path_part, _, anchor = target.partition("#")
    dest = (source.parent / path_part).resolve() if path_part else source
    if not is_tracked(dest):
        return f"нет такого файла: {path_part}"
    if anchor and dest.suffix == ".md" and anchor not in anchors_for(dest):
        return f"нет такого якоря: #{anchor} в {path_part or source.name}"
    return None


def _tracked_files() -> list[Path]:
    """Все отслеживаемые пути. `-z`: git C-квотит нелатинские имена в обычном режиме."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [_REPO_ROOT / name for name in result.stdout.split("\0") if name]


@lru_cache(maxsize=1)
def _tracked_paths() -> frozenset[Path]:
    """Файлы **и** их каталоги — цель ссылки бывает и тем, и другим."""
    paths: set[Path] = set()
    for file in _tracked_files():
        paths.add(file.resolve())
        paths.update(parent.resolve() for parent in file.parents if _REPO_ROOT in parent.parents)
    return frozenset(paths)


def _tracked_docs() -> list[Path]:
    return [path for path in _tracked_files() if path.suffix == ".md"]


def _is_tracked(path: Path) -> bool:
    return path.resolve() in _tracked_paths()


@cache
def _anchors_for(path: Path) -> set[str]:
    return anchors_of(path.read_text(encoding="utf-8"))


def _problems(docs: Iterable[Path]) -> list[str]:
    found: list[str] = []
    for doc in docs:
        for target in link_targets(doc.read_text(encoding="utf-8")):
            problem = target_problem(target, doc, _is_tracked, _anchors_for)
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
        tracked = {source}
        assert target_problem("nope.md", source, tracked.__contains__, lambda _: set()) is not None

    def test_untracked_but_existing_file_is_reported(self, tmp_path: Path) -> None:
        """Сверка идёт с индексом git, а не с ФС.

        Файл на диске есть, в индексе — нет: так выглядит и gitignored-копия репо, и
        ссылка `Pipeline.md#…`, которую регистронезависимый `Path.exists()` пропустил бы
        локально, чтобы уронить CI на Linux.
        """
        source = tmp_path / "a.md"
        (tmp_path / "ghost.md").write_text("# G", encoding="utf-8")
        assert target_problem("ghost.md", source, {source}.__contains__, lambda _: set())

    def test_missing_anchor_is_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "a.md"
        tracked = {source, tmp_path / "b.md"}.__contains__
        assert target_problem("b.md#gone", source, tracked, lambda _: {"real"}) is not None
        assert target_problem("b.md#real", source, tracked, lambda _: {"real"}) is None

    def test_directory_target_is_valid(self, tmp_path: Path) -> None:
        source = tmp_path / "a.md"
        tracked = {source, tmp_path / "adr"}.__contains__
        assert target_problem("adr/", source, tracked, lambda _: set()) is None

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
