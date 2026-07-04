"""Package-import contract for the `kinozal_scraper` migration (#237).

After migrating the 17 flat `src/*.py` modules into an installable package
`src/kinozal_scraper/`, every module must resolve as `kinozal_scraper.<name>`.
This is the RED-first import-resolution guard from the #237 Test plan.

**Caveat (architect-review B2):** `import_module` executes only a module's
top-level statements, NOT its `if __name__ == "__main__"` block. A mis-rewritten
import *inside* a `__main__` block of the 6 non-github entry pipelines therefore
slips past this test — the real guard for those is mypy (it type-checks the
`__main__` block too). "mypy native green" is load-bearing here, by design.
"""

from __future__ import annotations

import importlib

import pytest

# All 17 source modules. The 7 production entry points are a subset; the rest
# are libraries imported by them. Names mirror the file stems under src/.
_MODULES = [
    "crypto",
    "gemini_enricher",
    "generic_pipeline",
    "github_popular_pipeline",
    "github_trending_pipeline",
    "http_fetch",
    "kinozal_auth",
    "kinozal_pipeline",
    "pipeline_config",
    "sheets_storage",
    "soldout_pipeline",
    "steam_pipeline",
    "telegram_notifier",
    "telegram_summarizer",
    "TelegramChannelSummarizer",
    "text_utils",
    "youtube",
]


class TestPackage:
    @pytest.mark.parametrize("name", _MODULES)
    def test_entry_modules_importable(self, name: str) -> None:
        """Each module resolves under the `kinozal_scraper` package namespace."""
        module = importlib.import_module(f"kinozal_scraper.{name}")
        assert module.__name__ == f"kinozal_scraper.{name}"
