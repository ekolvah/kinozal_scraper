"""E2E: fetch real kinozal.tv and verify that titles are free of technical metadata.

Always runs. Uses URLS/KINOZAL_TOP_URL env var if set, falls back to the public top page.
"""
from __future__ import annotations

import unittest
from typing import ClassVar

from generic_pipeline import NormalizedItem
from kinozal_pipeline import _extract_kinozal_items, _fetch_html, _kinozal_urls
from pipeline_config import load_sources_config

_FALLBACK_URL = "https://kinozal.tv/top.php"


class TestKinozalTitlesE2E(unittest.TestCase):
    items: ClassVar[list[NormalizedItem]]

    @classmethod
    def setUpClass(cls) -> None:
        urls = _kinozal_urls()
        url = urls[0] if urls else _FALLBACK_URL
        config = load_sources_config()
        kinozal_sources = [
            s
            for s in config["sources"]
            if s.get("enabled") and s["id"].startswith("kinozal_")
        ]
        if not kinozal_sources:
            raise unittest.SkipTest("no enabled kinozal sources in sources.json")
        html = _fetch_html(url)
        cls.items = _extract_kinozal_items(html, kinozal_sources[0])

    def test_items_extracted(self) -> None:
        self.assertGreater(len(self.items), 0, "no items returned from kinozal")

    def test_no_title_contains_technical_suffix(self) -> None:
        for item in self.items:
            with self.subTest(title=item.title):
                self.assertNotIn(
                    " / ",
                    item.title,
                    f"title leaked technical metadata: {item.title!r}",
                )

    def test_dedupe_key_is_raw(self) -> None:
        for item in self.items:
            with self.subTest(dedupe_key=item.dedupe_key):
                self.assertTrue(
                    item.dedupe_key.startswith(item.title),
                    f"dedupe_key {item.dedupe_key!r} does not start with title {item.title!r}",
                )


if __name__ == "__main__":
    unittest.main()
