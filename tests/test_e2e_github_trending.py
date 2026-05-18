"""E2E: fetch real github.com/trending?since=daily and verify selectors still work.

Always runs. Skipped only if the live request fails with a network error.
"""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

import requests

from generic_pipeline import extract_from_html
from github_trending_pipeline import _FETCH_HEADERS, _normalize_items
from pipeline_config import load_sources_config


class TestGitHubTrendingE2E(unittest.TestCase):
    source: ClassVar[dict[str, Any]]
    html: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        config = load_sources_config()
        trending = [
            s for s in config["sources"] if s.get("enabled") and s["id"] == "github_trending"
        ]
        if not trending:
            raise unittest.SkipTest("github_trending source not enabled in sources.json")
        cls.source = trending[0]
        try:
            resp = requests.get(cls.source["url"], headers=_FETCH_HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise unittest.SkipTest(f"network unavailable: {exc}") from exc
        cls.html = resp.text

    def test_extracts_at_least_one_row(self) -> None:
        result = extract_from_html(self.html, self.source)
        items = _normalize_items(result.items)
        self.assertGreaterEqual(
            len(items),
            1,
            f"expected ≥1 trending row; errors={result.errors}",
        )

    def test_row_shape_is_owner_repo(self) -> None:
        result = extract_from_html(self.html, self.source)
        items = _normalize_items(result.items)
        for item in items:
            with self.subTest(dedupe_key=item.dedupe_key):
                self.assertRegex(item.dedupe_key, r"^[\w.\-]+/[\w.\-]+$")
                self.assertTrue(item.url.startswith("https://github.com/"), item.url)


if __name__ == "__main__":
    unittest.main()
