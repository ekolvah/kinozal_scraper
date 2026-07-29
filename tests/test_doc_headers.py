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

**Границы гарда, честно.** Presence ≠ correctness: что header *есть* и непуст —
детерминируемо, что он *актуален* — нет, и это уже записано в `project-map.md`
§«Presence ≠ correctness» (здесь ссылка, не вторая копия). Расхождение header ↔ реальное
назначение ловит человек на ревью.

**Скоуп производен от glob, а не от списка** — чтобы следующий arch-док попал под правило
автоматически, а не через ручной перечень, который забудут дополнить (та же логика, что в
`test_agent_frontmatter.py`, #407). Границы скоупа — спека, её дом `project-map.md`
§«Конвенция-заголовков»; здесь исполнение.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Каталоги картируемых `.md`. Канон границы — `project-map.md` §«Конвенция-заголовков».
_SCOPED_DIRS = (
    _REPO_ROOT / "docs" / "architecture",
    _REPO_ROOT / ".claude" / "rules",
)

# Маркер header'а — на языке самого дока. Принимаются оба варианта: репо двуязычен
# (`testing.md`/`principles.md` англоязычны, остальные — русские), и требовать один
# язык значило бы гнать churn-дифф с переводом ради формы. Это денилист наоборот:
# набор закрыт, расширять его — правка спеки в `project-map.md`, а не подгонка теста
# под файл.
_MARKERS = (
    "**На какой вопрос отвечает этот файл:**",
    "**Question this document answers:**",
)

# Минимум содержательного текста после маркера: пустой «header» — это отсутствие
# header'а с галочкой, ровно тот зелёный-но-пустой сигнал, против которого написан
# `D419` для `.py`.
_MIN_ANSWER_CHARS = 20


def _frontmatter(path: Path) -> dict[str, object]:
    """Frontmatter файла или `{}`, если его нет/он не мэппинг."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    raw, _, _ = rest.partition("\n---")
    parsed = yaml.safe_load(raw)
    return parsed if isinstance(parsed, dict) else {}


def _carries_header_in_frontmatter(path: Path) -> bool:
    """У файла с frontmatter `description:` header живёт **там**.

    Зачисление идёт по **свойству файла**, а не по каталогу: `.claude/agents/*.md` и
    `.claude/commands/*.md` одинаково несут `description:`, и перечисление каталогов
    оставило бы следующий агент/команду в серой зоне. Обратное тоже важно —
    `.claude/rules/testing.md` несёт frontmatter (`paths:`), но **не** `description:`,
    поэтому остаётся в скоупе и обязан нести строку-маркер.
    """
    return bool(str(_frontmatter(path).get("description", "")).strip())


def _mapped_docs() -> list[Path]:
    return sorted(
        path
        for directory in _SCOPED_DIRS
        for path in directory.glob("*.md")
        if not _carries_header_in_frontmatter(path)
    )


def _header_region(path: Path) -> str:
    """Текст до первого `## ` — там обязан быть header.

    Граница семантическая, а не «первые N строк»: N разваливается на файле с
    многострочной шапкой (`operations.md` — header + блок «Чего здесь нет», 12 строк).
    """
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            break
        lines.append(line)
    return "\n".join(lines)


class TestMappedDocsCarryHeader:
    @pytest.mark.parametrize("directory", _SCOPED_DIRS, ids=lambda d: d.name)
    def test_every_scoped_directory_contributes(self, directory: Path) -> None:
        """Гард на пустой glob, по каталогу а не по объединению.

        Проверять непустоту объединения мало: переезд одного из двух каталогов оставил
        бы тест зелёным за счёт второго — «нечего проверять» стало бы неотличимо от
        «всё в порядке» (§IV). Прецедент — `test_agent_frontmatter.py`.
        """
        assert list(directory.glob("*.md")), f"no .md found under {directory}"

    @pytest.mark.parametrize("path", _mapped_docs(), ids=lambda p: p.name)
    def test_mapped_doc_carries_header(self, path: Path) -> None:
        region = _header_region(path)
        marker = next((m for m in _MARKERS if m in region), None)
        assert marker is not None, (
            f"{path.name}: нет header'а до первого `## `. Header — канон того, на какой "
            f"вопрос отвечает файл ('при дрейфе header wins'), и без него спор о том, "
            f"какому файлу принадлежит секция, **нечем разрешить**: сверяться остаётся "
            f"только с производной строкой 'Карты файлов', которая по собственному "
            f"правилу проигрывает header'у (#421, триггер — #418). Ожидался один из "
            f"маркеров: {' | '.join(_MARKERS)}"
        )
        answer = region.partition(marker)[2].strip()
        assert len(answer) >= _MIN_ANSWER_CHARS, (
            f"{path.name}: header есть, но пуст ({len(answer)} симв. после маркера). "
            f"Пустой header — отсутствие канона с галочкой; тот же дефект, ради которого "
            f"для `.py` выбран `D419`, а не только `D100`"
        )
