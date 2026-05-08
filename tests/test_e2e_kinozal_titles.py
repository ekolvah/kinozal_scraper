"""E2E: fetch real kinozal.tv page and verify title cleaning.

Skip automatically when env var is absent (CI has no credentials).
Run locally:
    KINOZAL_TOP_URL=https://kinozal.tv/top.php python -m pytest tests/test_e2e_kinozal_titles.py -v
"""
from __future__ import annotations

import unittest

from kinozal_pipeline import _extract_kinozal_items, _fetch_html, _kinozal_urls
from pipeline_config import load_sources_config


class TestKinozalTitlesE2E(unittest.TestCase):
    items: list

    @classmethod
    def setUpClass(cls) -> None:
        urls = _kinozal_urls()
        if not urls:
            raise unittest.SkipTest("set URLS or KINOZAL_TOP_URL to run e2e tests")
        config = load_sources_config()
        kinozal_sources = [
            s for s in config["sources"]
            if s.get("enabled") and s["id"].startswith("kinozal_")
        ]
        if not kinozal_sources:
            raise unittest.SkipTest("no enabled kinozal sources in sources.json")
        html = _fetch_html(urls[0])
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
