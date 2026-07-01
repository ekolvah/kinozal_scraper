"""Anti-drift guard for the complexity ratchet ruff rules (#233).

Method sprawl is held back by ruff `C901` (mccabe cyclomatic), `PLR0912`
(too-many-branches) and `PLR0915` (too-many-statements), riding on the existing
`check_lint` gate. This guard pins that the ratchet stays *active*: the codes are
in the effective select and the mccabe threshold keeps its load-bearing value —
so a future agent cannot quietly drop the gate by editing `pyproject.toml`.

Mirrors `test_ruff_silence_rules.py` (silence gate, #231) and
`test_import_contracts.py` (§II boundaries, #234): it pins *enforcement*, not
mere declaration. It deliberately does not re-run ruff green — that is redundant
with `check_lint` (testing.md "when a test is not worth writing").
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

# Codes that must stay selected for the ratchet to bite new code.
_COMPLEXITY_CODES = {"C901", "PLR0912", "PLR0915"}
# Load-bearing: C901 aligned with PLR0912's default branch threshold (12), not
# tuned to today's code. Changing it silently reshapes the gate — pin the value.
_MCCABE_MAX_COMPLEXITY = 12


def _lint_config() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data["tool"]["ruff"]["lint"])


class TestComplexityRatchet:
    def test_rules_selected(self) -> None:
        lint = _lint_config()
        # Effective select (select ∪ extend-select) — robust to a future move to
        # extend-select (mirrors test_ruff_silence_rules.py).
        selected = set(lint.get("select", [])) | set(lint.get("extend-select", []))
        assert selected >= _COMPLEXITY_CODES, (
            "complexity ratchet codes must be in ruff select (#233 gate); "
            f"missing: {_COMPLEXITY_CODES - selected}"
        )

    def test_mccabe_threshold_is_12(self) -> None:
        lint = _lint_config()
        mccabe = lint.get("mccabe", {})
        assert mccabe.get("max-complexity") == _MCCABE_MAX_COMPLEXITY, (
            "mccabe max-complexity must stay aligned with PLR0912 default (12); "
            f"got {mccabe.get('max-complexity')!r}"
        )
