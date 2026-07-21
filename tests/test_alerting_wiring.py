"""Anti-drift: every scraper __main__ must gate sys.exit(1) on report_failures (#310).

Mirrors tests/test_settings_hooks.py — a static source scan, no imports/network.
A bare substring check on `report_failures(` would pass even if a scraper called it
but dropped the `sys.exit(1)`, silently regressing the §IV non-zero-exit invariant;
so we assert one of two shapes (architect S2):
  1. inline — `if report_failures(...): sys.exit(1)` (non-enriching scrapers);
  2. assigned — `failures = report_failures(...)` gated by `if ... failures ...: sys.exit(1)`
     (the enriching pipelines that ALSO exit on `alert_config_rejections`, #340).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "kinozal_scraper"

_SCRAPERS = [
    "soldout_pipeline.py",
    "kinozal_pipeline.py",
    "steam_pipeline.py",
    "github_popular_pipeline.py",
    "github_trending_pipeline.py",
]

_INLINE_GUARD = re.compile(r"if\s+report_failures\([^\n]*\):\s*\n\s*sys\.exit\(1\)")
_ASSIGNED_CALL = re.compile(r"failures\s*=\s*report_failures\([^\n]*\)")
_ASSIGNED_EXIT = re.compile(r"if\s+[^\n]*\bfailures\b[^\n]*:\s*\n\s*sys\.exit\(1\)")


def _guards_exit_on_report_failures(source: str) -> bool:
    if _INLINE_GUARD.search(source):
        return True
    # Assigned shape: the call result must be captured AND gate sys.exit(1) — a
    # scraper that assigns `failures` but drops the exit still fails the guard.
    return bool(_ASSIGNED_CALL.search(source) and _ASSIGNED_EXIT.search(source))


class TestScrapersReportFailures:
    @pytest.mark.parametrize("module", _SCRAPERS)
    def test_all_five_scrapers_guard_exit_on_report_failures(self, module: str) -> None:
        source = (_SRC / module).read_text(encoding="utf-8")
        assert _guards_exit_on_report_failures(source), (
            f"{module} __main__ must gate `sys.exit(1)` on `report_failures(...)` "
            "(anti-drift for the §IV non-zero-exit invariant)"
        )
