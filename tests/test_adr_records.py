"""Структурный гард каталога `docs/adr/` (#426).

**Что стережём.** Обоснования решений живут записями MADR в `docs/adr/`, и весь смысл
механизма — **стабильный ID, у которого есть тело в репо и статус**: только тогда
state-док может сослаться на решение, а не пересказывать его рядом с собой. Гард
защищает ровно те свойства, без которых ID перестаёт быть адресом: имя по конвенции,
уникальный номер, известный статус, `superseded by`, ведущий в существующую запись,
и обязательный минимум секций.

**Закрытый набор статусов — наша политика, а не MADR.** Апстрим отдаёт `status`
свободной строкой («These are optional metadata elements»); без закрытого набора не
выражается append-only-дисциплина (смена решения = новая запись со ссылкой вперёд,
а не правка старой). Канон набора и правило входа в каталог — запись
`docs/adr/0001-record-architecture-decisions.md`; здесь константа, которая ей подчинена.

**Границы гарда, честно.** Проверки структурные: запись-заготовку с незаполненными
`{placeholder}` из шаблона гард **не отличит** от настоящей — секции на месте, статус
известен, зелено. Он не судит и о том, достойно ли решение записи (cost-of-change —
семантический вопрос) и не актуально ли обоснование. Presence ≠ correctness, ровно как
в `test_doc_headers.py`: гард гарантирует, что **есть с чем спорить** на ревью.

**Скоуп производен от glob**, а не от списка: следующая запись попадает под инвариант
сама. Гард на пустой каталог — против того же §IV-вакуума, что в `test_doc_headers.py`
и `test_agent_frontmatter.py`: «нечего проверять» обязано отличаться от «всё в порядке».
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from scripts.validate_issue_sections import find_gaps

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ADR_DIR = _REPO_ROOT / "docs" / "adr"
_TEMPLATE = _ADR_DIR / "template.md"

# `NNNN-slug.md` — конвенция MADR. Номер отдельной группой: он и есть адрес записи.
_RECORD_NAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")

# Статусы MADR минус свободная форма. Дом набора — запись 0001 (см. docstring модуля).
_STATIC_STATUSES = frozenset({"proposed", "rejected", "accepted", "deprecated"})
_SUPERSEDED_BY = re.compile(r"^superseded by ADR-(\d{4})$")

# Состав MADR **minimal** (`template/adr-template-minimal.md`, тег 4.0.0). `Consequences`
# и `Confirmation` — `###` под `## Decision Outcome`, поэтому h2-парсер их не видит и
# требовать не должен: это шаблон апстрима, а не наше послабление.
_REQUIRED_SECTIONS = ("Context and Problem Statement", "Considered Options", "Decision Outcome")


def _record_files() -> list[Path]:
    """Все `.md` каталога, кроме шаблона.

    Фильтровать по `_RECORD_NAME` здесь нельзя: файл с кривым именем тогда молча
    выпал бы из набора и `test_filename_matches_convention` не покраснел бы никогда.
    """
    return sorted(p for p in _ADR_DIR.glob("*.md") if p.name != _TEMPLATE.name)


def _frontmatter_status(text: str) -> str | None:
    """Значение `status` из YAML-frontmatter записи, либо `None`.

    Парсер — `yaml`, как в `test_agent_frontmatter.py`: третью реализацию разбора
    frontmatter в репо не заводим.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    return status if isinstance(status, str) else None


def _filename_problem(name: str) -> str | None:
    """Описание нарушения конвенции имени, либо `None`."""
    if _RECORD_NAME.match(name):
        return None
    return (
        f"имя '{name}' не по конвенции MADR `NNNN-slug-in-kebab-case.md`: номер — это адрес "
        f"записи, по нему на неё ссылаются state-доки"
    )


def _status_problem(status: str | None) -> str | None:
    """Описание нарушения формы статуса, либо `None`. Резолв цели — не здесь."""
    if status is None:
        return (
            "нет строкового `status` в YAML-frontmatter — по записи нельзя понять, действует "
            "решение или отменено, а без этого ссылка на неё ничего не гарантирует"
        )
    if status in _STATIC_STATUSES or _SUPERSEDED_BY.match(status):
        return None
    return (
        f"статус '{status}' вне закрытого набора {sorted(_STATIC_STATUSES)} и не имеет формы "
        f"`superseded by ADR-NNNN` (канон набора — запись 0001)"
    )


def _superseded_target(status: str | None) -> str | None:
    """Номер записи, на которую указывает `superseded by`, либо `None`."""
    if status is None:
        return None
    match = _SUPERSEDED_BY.match(status)
    return match.group(1) if match else None


def _dangling_superseded(status: str | None, known_numbers: frozenset[str]) -> str | None:
    """Номер цели `superseded by`, которой нет среди записей, либо `None`.

    Отдельной функцией, а не проверкой в теле теста: пока ни одна запись не отменена,
    тест на реальном каталоге скипается, и без синтетики логика резолва не была бы
    проверена **вовсе** — зелёный гейт по пустому множеству.
    """
    target = _superseded_target(status)
    if target is None or target in known_numbers:
        return None
    return target


def _missing_sections(text: str) -> list[str]:
    """Обязательные MADR-секции, которых в записи нет или которые пусты."""
    return find_gaps(text, required=_REQUIRED_SECTIONS)


def _duplicate_numbers(names: Sequence[str]) -> list[str]:
    """Номера, встретившиеся больше одного раза."""
    numbers = [match.group(1) for name in names if (match := _RECORD_NAME.match(name))]
    return sorted(number for number, count in Counter(numbers).items() if count > 1)


class TestAdrCatalogue:
    def test_catalogue_is_not_empty(self) -> None:
        """Пустой каталог = гард ничего не проверяет, оставаясь зелёным (§IV)."""
        assert _record_files(), (
            f"в {_ADR_DIR} нет ни одной записи — параметризованные проверки ниже "
            f"проверяют пустой набор, и 'нечего проверять' становится неотличимо от "
            f"'всё в порядке'"
        )

    def test_template_exists_and_is_not_a_record(self) -> None:
        """Шаблон обязан лежать рядом и обязан быть невалиден как запись.

        Он и есть источник `{placeholder}`-ов: попади он в набор записей, гард
        краснел бы на нём вечно, а обойти это фильтром «кроме заготовок» значило бы
        завести тихий opt-out для настоящих записей.
        """
        assert _TEMPLATE.is_file(), (
            f"нет {_TEMPLATE}: запись создаётся копированием шаблона, без него формат "
            f"воспроизводится по памяти"
        )
        assert _TEMPLATE not in _record_files()
        assert _filename_problem(_TEMPLATE.name) is not None

    def test_record_numbers_are_unique(self) -> None:
        """Дубль номера делает ссылку `ADR-NNNN` неоднозначной при зелёном гарде.

        Случай не гипотетический: две параллельные ветки заводят по записи, каждая
        берёт «следующий свободный» номер, и обе правы поодиночке.
        """
        duplicates = _duplicate_numbers([p.name for p in _record_files()])
        assert not duplicates, (
            f"номера записей повторяются: {duplicates}. Ссылка `ADR-{duplicates[0]}` "
            f"перестала быть адресом — а на адресуемости держится весь механизм"
        )


class TestAdrRecord:
    @pytest.mark.parametrize("path", _record_files(), ids=lambda p: p.name)
    def test_filename_matches_convention(self, path: Path) -> None:
        problem = _filename_problem(path.name)
        assert problem is None, f"{path.name}: {problem}"

    @pytest.mark.parametrize("path", _record_files(), ids=lambda p: p.name)
    def test_status_is_known(self, path: Path) -> None:
        status = _frontmatter_status(path.read_text(encoding="utf-8"))
        problem = _status_problem(status)
        assert problem is None, f"{path.name}: {problem}"

    @pytest.mark.parametrize("path", _record_files(), ids=lambda p: p.name)
    def test_superseded_by_resolves_to_existing_record(self, path: Path) -> None:
        known = frozenset(p.name[:4] for p in _record_files())
        status = _frontmatter_status(path.read_text(encoding="utf-8"))
        dangling = _dangling_superseded(status, known)
        assert dangling is None, (
            f"{path.name}: `superseded by ADR-{dangling}`, но записи с таким номером нет. "
            f"Висячая ссылка вперёд хуже отсутствия статуса: читатель считает решение "
            f"отменённым и не находит, чем"
        )

    @pytest.mark.parametrize("path", _record_files(), ids=lambda p: p.name)
    def test_required_madr_sections_present(self, path: Path) -> None:
        missing = _missing_sections(path.read_text(encoding="utf-8"))
        assert not missing, f"{path.name}: нет обязательных секций MADR: {missing}"


class TestRecordPredicates:
    """Негативные ветки — на синтетике: реальный каталог валиден по построению,
    и без этих кейсов гард доказывал бы лишь сам себя."""

    @pytest.mark.parametrize(
        "name",
        [
            "1-short-number.md",
            "0001_underscore.md",
            "0001-Capital.md",
            "no-number.md",
            "0001-trailing-.md",
            "0001-record.txt",
        ],
    )
    def test_bad_filename_reported(self, name: str) -> None:
        assert _filename_problem(name) is not None

    def test_good_filename_accepted(self) -> None:
        assert _filename_problem("0042-record-architecture-decisions.md") is None

    @pytest.mark.parametrize(
        "status",
        [None, "", "Accepted", "superseded", "superseded by ADR-7", "in progress"],
    )
    def test_unknown_status_reported(self, status: str | None) -> None:
        assert _status_problem(status) is not None

    @pytest.mark.parametrize("status", sorted(_STATIC_STATUSES) + ["superseded by ADR-0007"])
    def test_known_status_accepted(self, status: str) -> None:
        assert _status_problem(status) is None

    def test_superseded_target_extracted(self) -> None:
        assert _superseded_target("superseded by ADR-0007") == "0007"
        assert _superseded_target("accepted") is None

    def test_dangling_superseded_reported(self) -> None:
        known = frozenset({"0001"})
        assert _dangling_superseded("superseded by ADR-0007", known) == "0007"
        assert _dangling_superseded("superseded by ADR-0001", known) is None
        assert _dangling_superseded("accepted", known) is None

    def test_frontmatter_status_read(self) -> None:
        text = '---\nstatus: "accepted"\ndate: 2026-07-30\n---\n\n# Title\n'
        assert _frontmatter_status(text) == "accepted"

    def test_frontmatter_absent_gives_none(self) -> None:
        assert _frontmatter_status("# Title\n\n## Context and Problem Statement\n\nx\n") is None

    def test_missing_section_reported(self) -> None:
        text = (
            "# T\n\n## Context and Problem Statement\n\nПочему.\n\n## Considered Options\n\nA, B.\n"
        )
        assert _missing_sections(text) == ["Decision Outcome"]

    def test_full_record_has_no_missing_sections(self) -> None:
        text = "\n".join(
            f"## {s}\n\nСодержательный текст секции {s}.\n" for s in _REQUIRED_SECTIONS
        )
        assert _missing_sections(text) == []

    def test_duplicate_numbers_reported(self) -> None:
        names = ["0001-a.md", "0002-b.md", "0002-c.md"]
        assert _duplicate_numbers(names) == ["0002"]

    def test_unique_numbers_give_no_duplicates(self) -> None:
        assert _duplicate_numbers(["0001-a.md", "0002-b.md"]) == []
