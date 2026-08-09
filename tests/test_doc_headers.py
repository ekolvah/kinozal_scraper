"""Presence guard for headers in mapped `.md` files (#421).

**What is guarded.** `docs/architecture/project-map.md` §“Header convention” declares that
every mapped file carries a header with the single question it answers, and that header is
the **canon** (“when they drift, header wins”), while the “File Map” is a derived
index. Presence of this canon is already gated for `.py` by ruff `D100`/`D104`/`D419` in
`check_lint` (#253, formerly bespoke `scripts/check_headers.py`). There was no equivalent for `.md`:
the rule had lived in prose since #164 and was followed less than half the time. This is exactly
the `principles.md` §Scripts over instructions case—the deterministic step “ensure that a header
exists” becomes an exit code.

**Why a test, not an entry in `CHECKS`.** `tests/test_ci_check.py::TestStepParity` requires
`_ci_yml_check_names() == set(CHECKS)`, so a new registry entry would also require an
`--only` step in `ci.yml`—an extra parity element for a static check already run by
`check_pytest`. Its genre is `test_repo_layout.py` / `test_agent_frontmatter.py`.

**Scope is the glob itself, with no second filter over it.** The first version
filtered out files with frontmatter `description:` to “admit them by property rather than by
directory”. There was nothing to filter out: `.claude/agents/*.md` and `.claude/commands/*.md`—the
very `description:` set—are not enumerated by this glob at all, so they are excluded earlier
and unconditionally. The filter's only live effect inside scope was a **silent
opt-out**: add `description:` to an architecture document's preamble and it silently drops out of parameterization,
without failing a test—the exact §IV defect guarded against by the empty-scope check.
The `description:` property remains what it essentially was: the **rationale**
for the boundary in the specification (`project-map.md`), not the mechanism here.

**Guard boundaries, honestly.** Presence ≠ correctness: whether a header *exists* and is non-empty is
deterministic, whether it is *current* is not, and that is already recorded in `project-map.md`
§“Presence ≠ correctness” (a reference here, not a second copy). A person catches divergence
between the header and the actual purpose in review.

**Scope is derived from the glob, not from a list** so the next architecture document enters the rule
automatically, rather than through a manual enumeration someone will forget to extend (the same logic as
`test_agent_frontmatter.py`, #407).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Mapped Markdown directories; `project-map.md` defines the canonical boundary.
_SCOPED_DIRS = (
    _REPO_ROOT / "docs" / "architecture",
    _REPO_ROOT / ".claude" / "rules",
)

# English is the repository documentation language (ADR-0005); the marker set is closed.
_MARKERS = ("**Question this document answers:**",)

# Stop at any section heading; otherwise a later prose mention could count as a header.
_SECTION_HEADING = re.compile(r"^#{2,6} ")

# Require substantive text after the marker, paralleling Python's `D419`.
_MIN_ANSWER_CHARS = 20


def _mapped_docs_in(directory: Path) -> list[Path]:
    """Use `rglob`, not `glob`: the specification says “`.md` **under** the directory”.

    A flat `glob` would silently leave future `docs/architecture/<subdirectory>/*.md` outside the invariant—the
    same form of silent vacuum as moving the directory itself. The MADR record catalogue lives
    **not** here (`docs/adr/`) and is intentionally outside this guard: records have their own
    preamble, the canonical boundary is `project-map.md` §“What counts as a mapped file”, and their
    own invariant is `test_adr_records.py` (#426).
    """
    return sorted(directory.rglob("*.md"))


def _mapped_docs() -> list[Path]:
    return [path for directory in _SCOPED_DIRS for path in _mapped_docs_in(directory)]


def _header_region(path: Path) -> list[str]:
    """Return lines before the first section heading, where the header belongs.

    The boundary is semantic rather than a fixed number of lines, which would
    fail for documents with multi-line introductory blocks.
    """
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _SECTION_HEADING.match(line):
            break
        lines.append(line)
    return lines


def _header_answer(lines: list[str]) -> str | None:
    """Return text after a marker **at the start** of a line, otherwise `None`.

    This is anchored at the start of the line rather than `marker in text`: otherwise a document
    that merely mentions the marker (such as `project-map.md`, which quotes both variants in the specification text itself)
    would count as carrying a header. The leading `> ` is removed—`testing.md` holds its
    header in a blockquote, and that is a legitimate form.
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
        """Guard each directory against an empty scope, not only their union.

        Checking the union for non-emptiness is insufficient: moving one of two directories
        would leave the test green thanks to the other—“nothing to check” would become indistinguishable
        from “everything is fine” (§IV). The precedent is `test_agent_frontmatter.py`. The assertion is
        about the **same** list that parameterizes the checks below: a guard that looks at
        a wider set than is actually scanned would itself be a vacuum.
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
