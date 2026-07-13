"""Anti-drift: every scraper __main__ must gate sys.exit(1) on report_failures (#310).

Mirrors tests/test_settings_hooks.py — a static source scan, no imports/network.
A bare substring check on `report_failures(` would pass even if a scraper called it
but dropped the `sys.exit(1)`, silently regressing the §IV non-zero-exit invariant;
so we assert the full `if report_failures(...): sys.exit(1)` shape (architect S2).
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

_GUARD = re.compile(r"if\s+report_failures\([^\n]*\):\s*\n\s*sys\.exit\(1\)")


class TestScrapersReportFailures:
    @pytest.mark.parametrize("module", _SCRAPERS)
    def test_all_five_scrapers_guard_exit_on_report_failures(self, module: str) -> None:
        source = (_SRC / module).read_text(encoding="utf-8")
        assert _GUARD.search(source), (
            f"{module} __main__ must gate `sys.exit(1)` on `report_failures(...)` "
            "(anti-drift for the §IV non-zero-exit invariant)"
        )
