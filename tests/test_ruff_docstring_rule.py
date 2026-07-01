"""Anti-drift guard for the module-docstring ruff rules (#253).

The bespoke `scripts/check_headers.py` (module-docstring presence gate) was
replaced by ruff `D100` (missing-docstring-in-public-module), `D104`
(missing-docstring-in-public-package — covers `__init__.py`, which D100 does
NOT) and `D419` (empty-docstring — the `doc.strip()` half the script enforced).
Together they reproduce the script's contract "every source .py under src,
including `__init__.py`, carries a *non-empty* module docstring".

This guard asserts the rules stay *active*: present in the effective select,
not globally ignored, and not neutralised for `src`/`scripts` via
`per-file-ignores` (the `tests/**` ignore is legitimate — tests were never
docstring-checked). Mirrors `test_ruff_silence_rules.py` (#231) /
`test_complexity_ratchet.py` (#233) / `test_ruff_dead_code_rule.py` (#235) — it
pins *enforcement*, not mere declaration, so a future agent cannot quietly drop
the gate through any disable vector. It deliberately does not re-run ruff green
— that is redundant with the live `check_lint` gate (testing.md "when a test is
not worth writing").
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

# Codes that reproduce check_headers.py's contract.
_DOCSTRING_CODES = {"D100", "D104", "D419"}
# Representative source paths the gate MUST keep covering — a per-file-ignore
# that silences a docstring code for any of these guts the gate (#253 threat).
_PROTECTED_PATHS = (
    "src/kinozal_scraper/__init__.py",
    "src/kinozal_scraper/generic_pipeline.py",
    "scripts/ci_check.py",
    "scripts/__init__.py",
)


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
        assert not (_DOCSTRING_CODES & ignored), (
            "module-docstring codes must not appear in ruff `ignore` (silently "
            f"disables the #253 gate): {_DOCSTRING_CODES & ignored}"
        )

        # (c) not neutralised for a protected src/scripts path via
        # per-file-ignores. Matched by real path coverage (fnmatch), not a
        # literal "tests/" check — the tests/** ignore is legitimate, but an
        # ignore whose glob also catches a src/scripts file is a leak.
        per_file: dict[str, list[str]] = lint.get("per-file-ignores", {})
        leaks: dict[str, dict[str, list[str]]] = {}
        for pattern, codes in per_file.items():
            disabled = _DOCSTRING_CODES & set(codes)
            if not disabled:
                continue
            hit = [p for p in _PROTECTED_PATHS if fnmatch.fnmatch(p, pattern)]
            if hit:
                leaks[pattern] = {"disables": sorted(disabled), "covers": hit}
        assert not leaks, (
            "per-file-ignores must not disable module-docstring codes for "
            f"src/scripts paths (silently guts the #253 gate): {leaks}"
        )
