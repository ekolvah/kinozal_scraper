"""Anti-drift guard for the module-docstring ruff rules (#253).

The bespoke `scripts/check_headers.py` (module-docstring presence gate) was
replaced by ruff `D100` (missing-docstring-in-public-module), `D104`
(missing-docstring-in-public-package — covers `__init__.py`, which D100 does
NOT) and `D419` (empty-docstring — the `doc.strip()` half the script enforced).
Together they reproduce the script's contract "every source .py under src,
including `__init__.py`, carries a *non-empty* module docstring".

This guard asserts the rules stay *active*: present in the effective select,
not globally ignored, and not neutralised via `per-file-ignores` under **any**
pattern — the gate has no legitimate per-file exemption left (#433 revoked the
`tests/**` one). Mirrors `test_ruff_silence_rules.py` (#231) /
`test_complexity_ratchet.py` (#233) / `test_ruff_dead_code_rule.py` (#235) — it
pins *enforcement*, not mere declaration, so a future agent cannot quietly drop
the gate through any disable vector. It deliberately does not re-run ruff green
— that is redundant with the live `check_lint` gate (testing.md "when a test is
not worth writing").
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

# Codes that reproduce check_headers.py's contract.
_DOCSTRING_CODES = {"D100", "D104", "D419"}
# ruff also accepts prefix selectors, so `"D"` or `"D1"` in an ignore list
# disables the codes while leaving the exact strings absent — the same threat
# model as the `ERA` prefix in test_ruff_dead_code_rule.py.
_DISABLE_TOKENS = {c[:i] for c in _DOCSTRING_CODES for i in range(1, len(c) + 1)}


def _lint_config() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data["tool"]["ruff"]["lint"])


class TestRuffDocstringRule:
    def test_docstring_rule_active(self) -> None:
        lint = _lint_config()

        # (a) present in effective select (select ∪ extend-select).
        selected = set(lint.get("select", [])) | set(lint.get("extend-select", []))
        assert selected >= _DOCSTRING_CODES, (
            "module-docstring codes must be in ruff select (#253 gate); "
            f"missing: {_DOCSTRING_CODES - selected}"
        )

        # (b) not neutralised via global ignore.
        ignored = set(lint.get("ignore", []))
        assert not (_DISABLE_TOKENS & ignored), (
            "module-docstring codes must not appear in ruff `ignore` (silently "
            f"disables the #253 gate): {_DISABLE_TOKENS & ignored}"
        )

        # (c) not neutralised via per-file-ignores under ANY pattern. Not a
        # path-coverage probe against sentinel paths (#433): a narrow
        # re-exemption like "tests/test_x.py" = ["D100"] would slip past one,
        # and no legitimate per-file exemption is left to carve room for.
        per_file = lint.get("per-file-ignores", {})
        leaked = {
            path: sorted(_DISABLE_TOKENS & set(codes))
            for path, codes in per_file.items()
            if _DISABLE_TOKENS & set(codes)
        }
        assert not leaked, (
            "module-docstring codes must not appear in `per-file-ignores` for "
            f"any path (silently guts the #253 gate): {leaked}"
        )
