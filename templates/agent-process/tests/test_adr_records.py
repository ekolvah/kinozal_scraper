"""Structural guard for the `docs/adr/` catalogue.

**What is guarded.** Decision rationales live in MADR records in `docs/adr/`, and the entire point of
the mechanism is a **stable ID with a body in the repository and a status**: only then can a
state document refer to a decision rather than restating it beside itself. The guard protects precisely
the properties without which an ID ceases to be an address: a conventional name,
a unique number, a known status, `superseded by` that leads to an existing record,
and the mandatory minimum set of sections.

**The closed status set is our policy, not MADR's.** Upstream exposes `status` as
a free-form string (“These are optional metadata elements”); without a closed set, append-only
discipline cannot be expressed (a decision change = a new record with a forward reference,
not an edit to the old one). The canon for the set and the rule for entering the catalogue is `project-map.md`
§Canonical-home (**not** record `0001`: policy changes, while an accepted record does not);
the constant here is subordinate to that canon.

**Guard boundaries, honestly.** The checks are structural: the guard **cannot distinguish**
a draft record with unfilled `{placeholder}`s from the template from a real one—the sections are present, the status
is known, and it is green. Nor does it judge whether a decision merits a record (cost of change is a
semantic question) or whether its rationale is still current. Presence ≠ correctness, just as in
`test_doc_headers.py`: the guard ensures there is **something to debate** in review.

**Scope is derived from the glob**, not from a list: the next record enters the invariant
automatically. The guard against an empty catalogue counters the same §IV vacuum as
`test_doc_headers.py`: “nothing to check” must differ from “everything is fine”.
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

# MADR uses `NNNN-slug.md`; the captured number is the record address.
_RECORD_NAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")

# Closed MADR status set; repository policy is canonical in `project-map.md`.
_STATIC_STATUSES = frozenset({"proposed", "rejected", "accepted", "deprecated"})
_SUPERSEDED_BY = re.compile(r"^superseded by ADR-(\d{4})$")

# Minimal MADR 4.0.0 h2 sections. `Consequences` and `Confirmation` are h3
# subsections of `Decision Outcome`, so the h2 parser must not require them.
_REQUIRED_SECTIONS = ("Context and Problem Statement", "Considered Options", "Decision Outcome")


def _record_files() -> list[Path]:
    """Return every catalogue Markdown file except the template.

    Do not prefilter by `_RECORD_NAME`; malformed names must reach the test.
    """
    return sorted(p for p in _ADR_DIR.glob("*.md") if p.name != _TEMPLATE.name)


def _frontmatter_status(text: str) -> str | None:
    """Return the record's YAML-frontmatter `status`, or `None`.

    Reuse YAML parsing rather than adding another frontmatter implementation.
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


def _record_number(name: str) -> str | None:
    """Return the record number from a conventional filename, otherwise `None`."""
    match = _RECORD_NAME.match(name)
    return match.group(1) if match else None


def _filename_problem(name: str) -> str | None:
    """Return a filename-convention problem, otherwise `None`."""
    if _RECORD_NAME.match(name):
        return None
    return (
        f"имя '{name}' не по конвенции MADR `NNNN-slug-in-kebab-case.md`: номер — это адрес "
        f"записи, по нему на неё ссылаются state-доки"
    )


def _status_problem(status: str | None) -> str | None:
    """Return a status-shape problem; target resolution is handled separately."""
    if status is None:
        return (
            "нет строкового `status` в YAML-frontmatter — по записи нельзя понять, действует "
            "решение или отменено, а без этого ссылка на неё ничего не гарантирует"
        )
    if status in _STATIC_STATUSES or _SUPERSEDED_BY.match(status):
        return None
    return (
        f"статус '{status}' вне закрытого набора {sorted(_STATIC_STATUSES)} и не имеет формы "
        f"`superseded by ADR-NNNN` (канон набора — `project-map.md` §Canonical-home)"
    )


def _superseded_target(status: str | None) -> str | None:
    """Return the record number named by `superseded by`, otherwise `None`."""
    if status is None:
        return None
    match = _SUPERSEDED_BY.match(status)
    return match.group(1) if match else None


def _dangling_superseded(status: str | None, known_numbers: frozenset[str]) -> str | None:
    """Return a `superseded by` target number absent from the records, otherwise `None`.

    This is a separate function rather than a check in the test body: while no record has been
    superseded, the real-catalogue test is skipped, and without synthetic cases the resolution logic
    would not be tested **at all**—a green gate over an empty set.
    """
    target = _superseded_target(status)
    if target is None or target in known_numbers:
        return None
    return target


def _missing_sections(text: str) -> list[str]:
    """Return required MADR sections that are absent or empty."""
    return find_gaps(text, required=_REQUIRED_SECTIONS)


def _duplicate_numbers(names: Sequence[str]) -> list[str]:
    """Return record numbers that appear more than once."""
    numbers = [number for name in names if (number := _record_number(name))]
    return sorted(number for number, count in Counter(numbers).items() if count > 1)


class TestAdrCatalogue:
    def test_catalogue_is_not_empty(self) -> None:
        """An empty catalogue would leave a vacuously green guard (§IV)."""
        assert _record_files(), (
            f"в {_ADR_DIR} нет ни одной записи — параметризованные проверки ниже "
            f"проверяют пустой набор, и 'нечего проверять' становится неотличимо от "
            f"'всё в порядке'"
        )

    def test_template_exists_and_is_not_a_record(self) -> None:
        """The adjacent template must exist but remain invalid as a record.

        It is the source of `{placeholder}`s: if it entered the record set, the guard
        would stay red on it forever, while avoiding that with a “except drafts” filter would
        create a silent opt-out for real records.
        """
        assert _TEMPLATE.is_file(), (
            f"нет {_TEMPLATE}: запись создаётся копированием шаблона, без него формат "
            f"воспроизводится по памяти"
        )
        assert _TEMPLATE not in _record_files()
        assert _filename_problem(_TEMPLATE.name) is not None

    def test_record_numbers_are_unique(self) -> None:
        """A duplicate number makes the `ADR-NNNN` reference ambiguous with a green guard.

        This case is not hypothetical: two parallel branches each create a record, each
        takes the “next free” number, and each is correct in isolation.
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
        known = frozenset(n for p in _record_files() if (n := _record_number(p.name)))
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
    """Negative branches use synthetic data: the real catalogue is valid by construction,
    and without these cases the guard would prove only itself."""

    @pytest.mark.parametrize(
        "name",
        [
            "1-short-number.md",
            "0001_underscore.md",
            "0001-Capital.md",
            "no-number.md",
            "0001-trailing-.md",
            "00001-five-digits.md",
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
