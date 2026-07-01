"""Anti-drift guard for the dead-code detection ruff rule (#235).

Commented-out code is caught by ruff `ERA001` as a **preventive ratchet**: the
repo measured clean (the sole hit was an illustrative schema comment, a false
positive fixed by rewording — no cross-module dead code found, so `vulture` was
consciously dropped, see `docs/architecture/ci.md`). Value is a forcing-function
on *new* commented-out code, not cleanup of existing.

This guard asserts the code stays *active*: present in the effective select AND
not silently neutralised via `ignore` / `per-file-ignores`. Mirrors
`test_ruff_silence_rules.py` (#231) and `test_complexity_ratchet.py` (#233) — it
pins *enforcement*, not mere declaration, so a future agent cannot quietly drop
the gate through any of the disable vectors. It deliberately does not re-run ruff
green — that is redundant with the live `check_lint` gate (testing.md "when a
test is not worth writing").
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

# Code that must be selected. ruff config may carry it prefix or full.
_DEAD_CODE_CODES = {"ERA001"}
# Any of these tokens, if present in ignore / per-file-ignores, disables the
# dead-code rule while leaving `select` untouched — the threat-model of #235.
_DISABLE_TOKENS = {"ERA", "ERA001"}


def _lint_config() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data["tool"]["ruff"]["lint"])


class TestRuffDeadCodeRule:
    def test_dead_code_rule_active(self) -> None:
        lint = _lint_config()

        # (a) present in effective select (select ∪ extend-select) — robust to
        # a future move to extend-select (mirrors test_ruff_silence_rules.py).
        selected = set(lint.get("select", [])) | set(lint.get("extend-select", []))
        assert selected >= _DEAD_CODE_CODES, (
            "dead-code detection code must be in ruff select (#235 gate); "
            f"missing: {_DEAD_CODE_CODES - selected}"
        )

        # (b) not neutralised via ignore.
        ignored = set(lint.get("ignore", []))
        assert not (_DISABLE_TOKENS & ignored), (
            "dead-code code must not appear in ruff `ignore` (silently disables "
            f"the #235 gate): {_DISABLE_TOKENS & ignored}"
        )

        # (c) not neutralised via per-file-ignores.
        per_file = lint.get("per-file-ignores", {})
        leaked = {
            path: sorted(_DISABLE_TOKENS & set(codes))
            for path, codes in per_file.items()
            if _DISABLE_TOKENS & set(codes)
        }
        assert not leaked, (
            "dead-code code must not appear in `per-file-ignores` (silently "
            f"disables the #235 gate): {leaked}"
        )
