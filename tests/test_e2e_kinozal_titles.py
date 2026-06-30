"""E2E: fetch real kinozal.tv and verify that titles are free of technical metadata.

Uses URLS/KINOZAL_TOP_URL env var if set, falls back to the public top page.

Temporarily disabled: this test hits the live kinozal.tv top page, which is
currently returning HTTP 520 (origin down), so it fails any unrelated PR's CI.
The proper fix — skip only when the site is genuinely unreachable while still
catching markup drift when it is up — is tracked in #136. Remove this skip
there.
"""

from __future__ import annotations

import unittest
from typing import ClassVar

from kinozal_scraper.generic_pipeline import NormalizedItem
from kinozal_scraper.http_fetch import fetch_html
from kinozal_scraper.kinozal_pipeline import _extract_kinozal_items, _kinozal_urls
from kinozal_scraper.pipeline_config import load_sources_config


@unittest.skip("temporarily disabled while kinozal.tv returns 520; re-enable in #136")
class TestKinozalTitlesE2E(unittest.TestCase):
    items: ClassVar[list[NormalizedItem]]

    @classmethod
    def setUpClass(cls) -> None:
        config = load_sources_config()
        kinozal_sources = [
            s for s in config["sources"] if s.get("enabled") and s["id"].startswith("kinozal_")
        ]
        if not kinozal_sources:
            raise unittest.SkipTest("no enabled kinozal sources in sources.json")
        urls = _kinozal_urls()
        fallback_url = kinozal_sources[0]["base_url"] + "/top.php"
        url = urls[0] if urls else fallback_url
        html = fetch_html(url)
        cls.items = _extract_kinozal_items(html, kinozal_sources[0]).items

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
