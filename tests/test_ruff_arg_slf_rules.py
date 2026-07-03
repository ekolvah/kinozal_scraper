"""Anti-drift guard for the unused-arg / private-access ruff rules (#236).

Three rules with a *mixed* signal were triaged and enabled: `ARG001`
(unused-function-argument), `ARG002` (unused-method-argument) and `SLF001`
(private-member-access). The 110 existing hits split into two categories that
demand *different* silencing mechanisms, and this guard pins the distinction so
a future agent cannot quietly collapse it:

- **`tests/**` is a categorical exemption** (blanket `per-file-ignores`): tests
  legitimately reach into private members (§II mandates calling internal
  helpers directly in white-box tests) and carry mock signatures whose params
  are dictated by the mocked callable, not by usage. This is *unlike* `ERA001`,
  where tests are NOT exempt (commented-out code is dead regardless of file
  role) — so the surface pattern "tests always get a per-file-ignore" is wrong
  and must not be cargo-culted from here.
- **Individual false positives inside in-scope `src/` files** get a per-site
  `# noqa` (the two Protocol-conformance stubs whose param is required by the
  interface but unused by that one implementation). A per-site noqa is the
  escape hatch for a *genuine* FP — never for a real detector hit (that would
  train the hatch on a non-exception, §IV). The two `SLF001` src hits were a
  *real* §II leak and were root-caused (public `model_name` property), not
  noqa'd — so `SLF001` has zero surviving src hits.

This guard asserts the rules stay *active*: present in the effective select,
not globally ignored, and not neutralised for any `src`/`scripts` path via
`per-file-ignores` (the `tests/**` ignore is legitimate). Mirrors
`test_ruff_docstring_rule.py` (#253) / `test_complexity_ratchet.py` (#233) /
`test_ruff_dead_code_rule.py` (#235) — it pins *enforcement*, not mere
declaration, so a future agent cannot quietly drop the gate through any disable
vector. It deliberately does not re-run ruff green — that is redundant with the
live `check_lint` gate (testing.md "when a test is not worth writing").
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

# The triaged rules (#236).
_ARG_SLF_CODES = {"ARG001", "ARG002", "SLF001"}
# Representative source paths the gate MUST keep covering — a per-file-ignore
# that silences one of these codes for any of these guts the gate (#236 threat).
_PROTECTED_PATHS = (
    "src/kinozal_scraper/__init__.py",
    "src/kinozal_scraper/gemini_enricher.py",
    "src/kinozal_scraper/sheets_storage.py",
    "scripts/ci_check.py",
)


def _lint_config() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data["tool"]["ruff"]["lint"])


class TestRuffArgSlfRules:
    def test_arg_slf_rules_active(self) -> None:
        lint = _lint_config()

        # (a) present in effective select (select ∪ extend-select).
        selected = set(lint.get("select", [])) | set(lint.get("extend-select", []))
        assert selected >= _ARG_SLF_CODES, (
            "unused-arg / private-access codes must be in ruff select (#236 gate); "
            f"missing: {_ARG_SLF_CODES - selected}"
        )

        # (b) not neutralised via global ignore.
        ignored = set(lint.get("ignore", []))
        assert not (_ARG_SLF_CODES & ignored), (
            "unused-arg / private-access codes must not appear in ruff `ignore` "
            f"(silently disables the #236 gate): {_ARG_SLF_CODES & ignored}"
        )

        # (c) not neutralised for a protected src/scripts path via
        # per-file-ignores. Matched by real path coverage (fnmatch), not a
        # literal "tests/" check — the tests/** ignore is a legitimate
        # categorical exemption, but an ignore whose glob also catches a
        # src/scripts file is a leak.
        per_file: dict[str, list[str]] = lint.get("per-file-ignores", {})
        leaks: dict[str, dict[str, list[str]]] = {}
        for pattern, codes in per_file.items():
            disabled = _ARG_SLF_CODES & set(codes)
            if not disabled:
                continue
            hit = [p for p in _PROTECTED_PATHS if fnmatch.fnmatch(p, pattern)]
            if hit:
                leaks[pattern] = {"disables": sorted(disabled), "covers": hit}
        assert not leaks, (
            "per-file-ignores must not disable unused-arg / private-access codes "
            f"for src/scripts paths (silently guts the #236 gate): {leaks}"
        )
