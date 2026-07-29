"""Presence-гард на header'ы картируемых `.md` (#421).

**Что стережём.** `docs/architecture/project-map.md` §«Конвенция-заголовков» объявляет:
каждый картируемый файл несёт header с единственным вопросом, на который он отвечает, и
этот header — **канон** («при дрейфе header wins»), тогда как «Карта файлов» — производный
индекс. Для `.py` presence этого канона уже загейчен — ruff `D100`/`D104`/`D419` в
`check_lint` (#253, бывший bespoke `scripts/check_headers.py`). Для `.md` аналога не было:
правило жило прозой с #164 и за это время соблюдалось меньше чем наполовину. Это ровно
случай `mindset.md` §«Скрипты > инструкции» — детерминируемый шаг «убедись, что header
есть» становится exit-code'ом.

**Почему тест, а не запись в `CHECKS`.** `tests/test_ci_check.py::TestStepParity` требует
`_ci_yml_check_names() == set(CHECKS)`, поэтому новая запись в реестр обязала бы завести
ещё и `--only`-шаг в `ci.yml` — лишний parity-элемент ради статической проверки, которую и
так гоняет `check_pytest`. Жанр — `test_repo_layout.py` / `test_agent_frontmatter.py`.

**Скоуп — это сам glob, и никакого второго фильтра поверх него нет.** Первая версия
отсеивала файлы с frontmatter `description:`, чтобы «зачислять по свойству, а не по
каталогу». Отсеивать было нечего: `.claude/agents/*.md` и `.claude/commands/*.md` — то
самое `description:`-множество — этим glob'ом не перечисляются вовсе, то есть исключены
раньше и безусловно. Единственным живым эффектом фильтра внутри скоупа был **тихий
opt-out**: добавь `description:` в шапку arch-дока, и он молча выпадал из параметризации,
не уронив ни одного теста, — ровно §IV-дефект, против которого написан гард на пустой
скоуп. Свойство `description:` осталось тем, чем оно и было по сути, — **обоснованием**
границы в спеке (`project-map.md`), а не механизмом здесь.

**Границы гарда, честно.** Presence ≠ correctness: что header *есть* и непуст —
детерминируемо, что он *актуален* — нет, и это уже записано в `project-map.md`
§«Presence ≠ correctness» (здесь ссылка, не вторая копия). Расхождение header ↔ реальное
назначение ловит человек на ревью.

**Скоуп производен от glob, а не от списка** — чтобы следующий arch-док попал под правило
автоматически, а не через ручной перечень, который забудут дополнить (та же логика, что в
`test_agent_frontmatter.py`, #407).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Каталоги картируемых `.md`. Канон границы — `project-map.md` §«Конвенция-заголовков».
_SCOPED_DIRS = (
    _REPO_ROOT / "docs" / "architecture",
    _REPO_ROOT / ".claude" / "rules",
)

# Маркер header'а — на языке самого дока. Принимаются оба варианта: репо двуязычен
# (`testing.md`/`principles.md` англоязычны, остальные — русские), и требовать один
# язык значило бы гнать churn-дифф с переводом ради формы. Набор закрыт: расширять
# его — правка спеки в `project-map.md`, а не подгонка теста под файл.
_MARKERS = (
    "**На какой вопрос отвечает этот файл:**",
    "**Question this document answers:**",
)

# Любой заголовок секции, не только `## `: на `### ` тоже надо останавливаться, иначе
# у дока, чья первая секция — h3, «областью header'а» становится весь файл, и маркер,
# упомянутый в прозе внизу, засчитался бы за header.
_SECTION_HEADING = re.compile(r"^#{2,6} ")

# Минимум содержательного текста после маркера: пустой «header» — это отсутствие
# header'а с галочкой, ровно тот зелёный-но-пустой сигнал, против которого написан
# `D419` для `.py`.
_MIN_ANSWER_CHARS = 20


def _mapped_docs_in(directory: Path) -> list[Path]:
    """`rglob`, а не `glob`: спека говорит «`.md` **под** каталогом».

    Плоский `glob` оставил бы будущий `docs/architecture/adr/*.md` вне инварианта молча —
    та же форма тихого вакуума, что и переезд самого каталога.
    """
    return sorted(directory.rglob("*.md"))


def _mapped_docs() -> list[Path]:
    return [path for directory in _SCOPED_DIRS for path in _mapped_docs_in(directory)]


def _header_region(path: Path) -> list[str]:
    """Строки до первого заголовка секции — там обязан быть header.

    Граница семантическая, а не «первые N строк»: N разваливается на файле с
    многострочной шапкой (`operations.md` — header + блок «Чего здесь нет», 12 строк).
    """
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _SECTION_HEADING.match(line):
            break
        lines.append(line)
    return lines


def _header_answer(lines: list[str]) -> str | None:
    """Текст после маркера, если маркер **начинает** строку, иначе `None`.

    Привязка к началу строки, а не `marker in text`: иначе док, который маркер лишь
    упоминает (как `project-map.md`, цитирующий оба варианта в тексте самой спеки),
    засчитывался бы как несущий header. Ведущий `> ` снимается — `testing.md` держит
    header блокквотом, и это легитимная форма.
    """
    for line in lines:
        stripped = line.removeprefix("> ").strip()
        for marker in _MARKERS:
            if stripped.startswith(marker):
                return stripped.removeprefix(marker).strip()
    return None


class TestMappedDocsCarryHeader:
    @pytest.mark.parametrize("directory", _SCOPED_DIRS, ids=lambda d: d.name)
    def test_every_scoped_directory_contributes(self, directory: Path) -> None:
        """Гард на пустой скоуп, по каталогу а не по объединению.

        Проверять непустоту объединения мало: переезд одного из двух каталогов оставил
        бы тест зелёным за счёт второго — «нечего проверять» стало бы неотличимо от
        «всё в порядке» (§IV). Прецедент — `test_agent_frontmatter.py`. Утверждение
        идёт о **том же** списке, что параметризует проверки ниже: гард, смотрящий на
        более широкий набор, чем реально сканируется, сам был бы вакуумом.
        """
        assert _mapped_docs_in(directory), f"no .md found under {directory}"

    @pytest.mark.parametrize("path", _mapped_docs(), ids=lambda p: p.name)
    def test_mapped_doc_carries_header(self, path: Path) -> None:
        answer = _header_answer(_header_region(path))
        assert answer is not None, (
            f"{path.name}: нет header'а до первого заголовка секции. Header — канон того, "
            f"на какой вопрос отвечает файл ('при дрейфе header wins'), и без него спор о "
            f"том, какому файлу принадлежит секция, **нечем разрешить**: сверяться остаётся "
            f"только с производной строкой 'Карты файлов', которая по собственному правилу "
            f"проигрывает header'у (#421, триггер — #418). Ожидалась строка, начинающаяся "
            f"одним из маркеров: {' | '.join(_MARKERS)}"
        )
        assert len(answer) >= _MIN_ANSWER_CHARS, (
            f"{path.name}: header есть, но пуст ({len(answer)} симв. после маркера). "
            f"Пустой header — отсутствие канона с галочкой; тот же дефект, ради которого "
            f"для `.py` выбран `D419`, а не только `D100`"
        )
