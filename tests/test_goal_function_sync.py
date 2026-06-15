"""Anti-drift guard for the goal-function statement (#183).

The project's goal-function — three priorities in strict order
(bugfix/support -> tokens -> predictability) — must read identically for the
main session and the `architect-reviewer` subagent. They load DIFFERENT files:
the main session loads the always-load `.claude/rules/mindset.md`, while the
spawned subagent loads `.claude/agents/architect-reviewer.md` and NOT the
always-load rules. So the statement is necessarily inlined in both — the same
structural reason the deny-list is inlined twice (see test_settings_deny.py).

`mindset.md` §Цель-функция is the canon; `architect-reviewer.md` carries a
marked mirror. This test asserts the THREE PRIORITY TITLES match in order — a
*relation*, not a byte-for-byte block equality — so reordering / dropping /
adding a priority reddens CI, while rewording the rationale prose under a
priority does not (architect-reviewer SHOULD-FIX (a) on #183).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MINDSET = _REPO / ".claude" / "rules" / "mindset.md"
_REVIEWER = _REPO / ".claude" / "agents" / "architect-reviewer.md"

# A priority is a top-level numbered list item whose lead is bold:
# `1. **<title>** <rationale...>`. Non-greedy title capture stops at the first
# closing `**`, so trailing rationale prose is ignored.
_PRIORITY_RE = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*", re.MULTILINE)


def _priority_titles(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    titles = _PRIORITY_RE.findall(text)[:3]
    return [re.sub(r"\s+", " ", t).strip() for t in titles]


class TestGoalFunctionSync:
    def test_priority_order_matches(self) -> None:
        canon = _priority_titles(_MINDSET)
        mirror = _priority_titles(_REVIEWER)
        assert len(canon) == 3, (
            "mindset.md §Цель-функция must declare the 3 numbered priorities as the "
            f"in-repo canon, got {canon}"
        )
        assert len(mirror) == 3, (
            "architect-reviewer.md must declare exactly 3 priorities (mirror of canon), "
            f"got {mirror}"
        )
        assert canon == mirror, (
            "goal-function drift: mindset.md (canon) and architect-reviewer.md (mirror) "
            f"list different priorities or order.\n  canon : {canon}\n  mirror: {mirror}"
        )
