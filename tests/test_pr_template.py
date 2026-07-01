"""Anti-drift guard for the PR template's required H2 sections.

WHAT THIS PINS — and what it deliberately does NOT: this test asserts the
presence of *human-facing prompts* (H2 headers) in the markdown PR template.
Those headers have **no machine enforcement downstream** — there is no gate
that parses a PR body, and building one is consciously out of scope (#258:
cosmetic failure, solo-repo, priority-3 → work-for-work). So this is **not**
an analogue of `test_complexity_ratchet`, which pins a *live ruff gate* that
actually reddens new code; dropping a header here weakens a checklist, not an
enforced rule.

Its only justification is §IV (visibility over silence): a silent drop of the
`Risk & Rollback` prompt — the one delivery-only section (blast-radius / rollback
/ cron-monitoring) that has no issue counterpart — becomes a visible red instead
of quiet drift. Do NOT read this as a live gate and start building a PR-body
parser around it; that would be the work-for-work #258 explicitly rejected.
"""

from __future__ import annotations

from pathlib import Path

REQUIRED_PR_SECTIONS: tuple[str, ...] = (
    "Summary",
    "Test plan",
    "Risk & Rollback",
    "Docs touched",
)


def _template_text() -> str:
    path = Path(__file__).resolve().parent.parent / ".github" / "pull_request_template.md"
    return path.read_text(encoding="utf-8")


class TestPrTemplate:
    def test_all_required_sections_present(self) -> None:
        text = _template_text()
        missing = [name for name in REQUIRED_PR_SECTIONS if f"## {name}" not in text]
        assert not missing, f"PR template missing required H2 sections: {missing}"
