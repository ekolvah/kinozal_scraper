"""Budget for always-loaded context (#375).

**What is guarded.** `CLAUDE.md` and `.claude/rules/*.md` files **without** `paths:` in frontmatter
load in full into every session, before the task's first word (`project-map.md` §“Honestly about
tokens”). This is an unconditional charge levied independently of a session's topic—therefore a direct
subject of objective-function priority (2). On 2026-07-29 it was 21,135 B (~5.3k tokens),
and it grew to that figure **silently**: #416/#417 added ~3.8 KB of tactics to
`mindset.md`. Recurrence is observed rather than hypothetical—hence the gate.

**This is a tripwire, not a quality standard.** The test does not claim “the text is good” and cannot:
“how much is rule and how much is rationale prose” is a semantic judgment the repository
deliberately does not script (`project-map.md`). It asserts exactly one thing: growth of the charge is
an **intentional one-line edit in review**, not invisible drift. This is what distinguishes it
from the “document no longer than N lines” gate rejected in ledger entry **AA**: there, lines were a
*proxy* for document quality (below the threshold the wording shrinks, not the archaeology → the gate is green
while the defect is masked); here, bytes are the charge itself and the threshold is a ratchet.

**What the gate does NOT catch (scope boundaries).** The session preamble is wider than this set: it also
includes subagent and slash-command `description:` fields and the `MEMORY.md` index. A green test
therefore does **not** mean “the whole charge is under control”—it will not see cost shifting into
those carriers (nor moving text into `docs/architecture/*`, which the agent still reads on demand).
Expanding the scope is a separate decision; this boundary is simply named honestly here.

**It also cannot see the main thing: stated charge ≠ paid charge.** Bytes are multiplied by the number
of turns through `cache_read`, and this multiplier is not measured here at all. Actual consumption is measured
by `scripts/token_trend.py` (#464); its tests are `test_token_trend.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_ROOT_MEMORY = _REPO_ROOT / "CLAUDE.md"
_RULES_DIR = _REPO_ROOT / ".claude" / "rules"

# These files are intentionally in scope; see `test_expected_files_are_in_scope`.
_EXPECTED_ALWAYS_LOAD = (
    _ROOT_MEMORY,
    _RULES_DIR / "mindset.md",
    _RULES_DIR / "workflow.md",
)

# The threshold is current size plus a small allowance. It is a review ratchet,
# not permission to spend: substantive growth should name its byte cost, while
# narrative belongs in the issue or PR. Lowering the threshold after moving rules
# to on-demand documents prevents banking the freed space for silent growth.
_BUDGET_BYTES = 10_000

_FRONTMATTER_PATHS_KEY = re.compile(r"^paths\s*:", re.MULTILINE)


def _frontmatter(text: str) -> str:
    """Return the block between the first two `---` lines, or empty text."""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end != -1 else ""


def _is_path_scoped(path: Path) -> bool:
    return bool(_FRONTMATTER_PATHS_KEY.search(_frontmatter(path.read_text(encoding="utf-8"))))


def _rule_files() -> list[Path]:
    """Use `rglob`, not a flat `glob`, as in `test_doc_headers.py` (#421).

    The specification (`project-map.md` tier table) writes the scope as `.claude/rules/*.md`, so
    a flat glob would not formally contradict it. But then two guards over the same directory would
    answer “what is a rules file?” differently: a future `.claude/rules/<sub>/x.md` would enter the
    header guard and **not** the budget, silently moving out from under the charge.
    """
    return sorted(_RULES_DIR.rglob("*.md"))


def _always_load_files() -> list[Path]:
    """Derive the set so every new always-load rule enters the budget."""
    return [_ROOT_MEMORY, *(p for p in _rule_files() if not _is_path_scoped(p))]


def _normalized_size(path: Path) -> int:
    """Count bytes with LF line endings.

    The repository has no `.gitattributes`, while the maintainer has `core.autocrlf=true`: the worktree is
    locally CRLF and CI is LF. The difference across this set is ~230 B, about 10% of the whole
    #375 gain; without normalization, the gate would be “green for me, red in CI”.
    """
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


class TestAlwaysLoadBudget:
    def test_total_within_budget(self) -> None:
        files = _always_load_files()
        sizes = {p.name: _normalized_size(p) for p in files}
        total = sum(sizes.values())
        assert total <= _BUDGET_BYTES, (
            f"always-load бюджет превышен: {total} B > {_BUDGET_BYTES} B ({sizes}). "
            f"Это плата с КАЖДОЙ сессии, независимо от темы. Прежде чем поднимать порог — "
            f"проверь, не нарратив ли добавлен: «как мы к этому пришли» живёт в теле issue/PR, "
            f"в правиле остаётся указатель `(#N)` (#375). Поднятие порога легитимно, но это "
            f"осознанное решение на ревью, а не побочный эффект правки."
        )

    @pytest.mark.parametrize("path", _EXPECTED_ALWAYS_LOAD, ids=lambda p: p.name)
    def test_expected_files_are_in_scope(self, path: Path) -> None:
        """Pin by name, rather than checking “the set is non-empty”.

        The primary way to zero out the budget is to add `paths:` to `mindset.md` frontmatter:
        the file leaves the set, the sum **falls**, the test turns green, and always-load rules
        are silently disabled (§IV). A non-empty check permits this—the other two files would
        remain in the set. Therefore the guard is written by name for the whole catalogue, not
        for one file and not for “the set is non-empty” (#416, #375).
        """
        assert path in _always_load_files(), (
            f"{path.name} выпал из always-load набора — вероятно, у него появился `paths:` "
            f"во frontmatter. Тогда правила из него больше не грузятся в каждую сессию: "
            f"бюджет упал, но не потому, что текст ужали (#416, #375)"
        )

    def test_path_scoped_rule_is_excluded(self) -> None:
        """Pin the filter mechanism rather than a specific scoped file.

        If no scoped rule remains, the failure says the filter has no witness.
        """
        path_scoped = [p for p in _rule_files() if _is_path_scoped(p)]
        assert path_scoped, (
            "в `.claude/rules/` не осталось ни одного path-scoped файла — фильтр `paths:` "
            "больше ничем не проверяется, и его поломка прошла бы незаметно"
        )
        assert not set(path_scoped) & set(_always_load_files()), (
            f"path-scoped файлы попали в бюджет: {[p.name for p in path_scoped]}. Бюджет "
            f"обязан считать именно always-load плату, а не все `.md` подряд"
        )
