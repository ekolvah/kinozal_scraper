import unittest
import unittest.mock
from typing import Any

from generic_pipeline import ROW_HEADERS, NormalizedItem, Notification, extract_from_html
from kinozal_pipeline import _kinozal_urls, enrich_with_trailer
from sheets_storage import InMemoryStorage
from telegram_notifier import InMemoryNotifier
from text_utils import title_year_matches as _title_year_matches

# ── minimal synthetic HTML matching kinozal_movies row_selector ──────────────

_KINOZAL_HTML = """
<html><body>
<a href="/details.php?id=1" title="Film One / 2024 / BDRip"><img src="/img/p1.jpg"></a>
<a href="/details.php?id=2" title="Film Two"><img src="https://cdn.example.com/p2.jpg"></a>
</body></html>
"""

_KINOZAL_SOURCE = {
    "id": "kinozal_movies",
    "enabled": True,
    "type": "html",
    "url": "https://kinozal.tv/top.php",
    "base_url": "https://kinozal.tv",
    "params": {},
    "row_selector": "a[href^='/details.php']",
    "limit": 10,
    "sheet_tab": "movies",
    "dedupe_key": "@title",
    "fields": {
        "title": "@title",
        "url": "@href",
        "description": None,
        "metric": None,
        "image_url": "img@src",
    },
    "message_template": "<b>{title}</b>\n{url}\n{trailer_url}",
}

_SOURCES_CONFIG = {"version": 1, "sources": [_KINOZAL_SOURCE]}


class _FakeYoutube:
    def __init__(self) -> None:
        self.last_film: str = ""
        self.last_year: int | None = None

    def get_trailer_url(self, film: str, year: int | None = None) -> str:
        self.last_film = film
        self.last_year = year
        return f"https://youtube.com/watch?v={film.replace(' ', '_')}"


class _FilteringFakeYoutube:
    """Fake YouTube that applies year filtering like the real API."""

    def __init__(self, videos: list[tuple[str, str]]) -> None:
        self._videos = videos

    def get_trailer_url(self, film: str, year: int | None = None) -> str:
        for title, vid in self._videos:
            if year is None or _title_year_matches(title, year):
                return f"https://youtube.com/watch?v={vid}"
        return ""


class _RaisingYoutube:
    def get_trailer_url(self, film: str, year: int | None = None) -> str:
        raise RuntimeError("YouTube API down")


# ── base_url resolution ───────────────────────────────────────────────────────


class TestBaseUrlResolution(unittest.TestCase):
    def _extract(self, html: str = _KINOZAL_HTML) -> list[NormalizedItem]:
        result = extract_from_html(html, _KINOZAL_SOURCE)
        self.assertTrue(result.ok, result.errors)
        return result.items

    def test_relative_url_prefixed(self) -> None:
        items = self._extract()
        self.assertEqual(items[0].url, "https://kinozal.tv/details.php?id=1")

    def test_relative_image_url_prefixed(self) -> None:
        items = self._extract()
        self.assertEqual(items[0].image_url, "https://kinozal.tv/img/p1.jpg")

    def test_absolute_url_passthrough(self) -> None:
        items = self._extract()
        self.assertEqual(items[1].image_url, "https://cdn.example.com/p2.jpg")

    def test_dedupe_key_is_title_attribute(self) -> None:
        items = self._extract()
        self.assertEqual(items[0].dedupe_key, "Film One / 2024 / BDRip")

    def test_two_items_extracted(self) -> None:
        items = self._extract()
        self.assertEqual(len(items), 2)


# ── enrich_with_trailer ───────────────────────────────────────────────────────


class TestEnrichWithTrailer(unittest.TestCase):
    def _item(self, title: str) -> NormalizedItem:
        return NormalizedItem(dedupe_key=title, title=title, source_id="kinozal_movies")

    def test_title_cleaned_before_lookup(self) -> None:
        youtube = _FakeYoutube()
        item = self._item("Film One / 2024 / BDRip")
        trailer = enrich_with_trailer(item, youtube)
        self.assertIn("Film_One", trailer)
        self.assertNotIn("/", trailer.split("watch?v=")[1])

    def test_parentheses_stripped(self) -> None:
        youtube = _FakeYoutube()
        item = self._item("Film (2024)")
        trailer = enrich_with_trailer(item, youtube)
        self.assertIn("Film", trailer)
        self.assertNotIn("(2024)", trailer)

    def test_exception_returns_empty_string(self) -> None:
        item = self._item("Some Film")
        trailer = enrich_with_trailer(item, _RaisingYoutube())
        self.assertEqual(trailer, "")

    def test_year_extracted_from_title_and_passed(self) -> None:
        youtube = _FakeYoutube()
        item = self._item("Film One / 2024 / BDRip")
        enrich_with_trailer(item, youtube)
        self.assertEqual(youtube.last_year, 2024)

    def test_no_year_passes_none(self) -> None:
        youtube = _FakeYoutube()
        item = self._item("Film Without Year")
        enrich_with_trailer(item, youtube)
        self.assertIsNone(youtube.last_year)

    def test_year_in_parentheses_extracted(self) -> None:
        youtube = _FakeYoutube()
        item = self._item("Film (2023)")
        enrich_with_trailer(item, youtube)
        self.assertEqual(youtube.last_year, 2023)

    def test_clean_title_passed_without_year_slash(self) -> None:
        youtube = _FakeYoutube()
        item = self._item("Great Film / 2025 / WEB-DL")
        enrich_with_trailer(item, youtube)
        self.assertEqual(youtube.last_film, "Great Film")

    def test_2026_film_skips_2015_kingsman_trailer(self) -> None:
        youtube = _FilteringFakeYoutube(
            [
                ("Kingsman: Секретная служба (2015) Трейлер на русском", "JoKiK7Nx8Y8"),
                ("Секретная служба 2026 Официальный трейлер", "correct_id"),
            ]
        )
        item = self._item("Секретная служба / 2026 / WEB-DLRip")
        trailer = enrich_with_trailer(item, youtube)
        self.assertNotIn("JoKiK7Nx8Y8", trailer)
        self.assertIn("correct_id", trailer)


# ── _title_year_matches ───────────────────────────────────────────────────────


class TestTitleYearMatches(unittest.TestCase):
    def test_matching_year_accepted(self) -> None:
        self.assertTrue(_title_year_matches("Great Film 2026 Official Trailer", 2026))

    def test_wrong_year_rejected(self) -> None:
        self.assertFalse(
            _title_year_matches("Kingsman: Секретная служба (2015) Трейлер на русском", 2026)
        )

    def test_no_year_in_title_accepted(self) -> None:
        self.assertTrue(_title_year_matches("Секретная служба трейлер", 2026))

    def test_multiple_years_one_matches(self) -> None:
        self.assertTrue(_title_year_matches("Film 2025/2026 Official Trailer", 2026))

    def test_multiple_years_none_match(self) -> None:
        self.assertFalse(_title_year_matches("Remake 2023 vs Original 2015", 2026))


# ── _kinozal_urls ─────────────────────────────────────────────────────────────


class TestKinozalUrls(unittest.TestCase):
    def test_reads_existing_URLS_variable(self) -> None:
        import os

        with unittest.mock.patch.dict(
            os.environ,
            {"URLS": "топ|https://kinozal.tv/top.php;новинки|https://kinozal.tv/new.php"},
            clear=False,
        ):
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php", "https://kinozal.tv/new.php"])

    def test_falls_back_to_KINOZAL_TOP_URL(self) -> None:
        import os

        env = {"KINOZAL_TOP_URL": "https://kinozal.tv/top.php"}
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            # ensure URLS is absent
            os.environ.pop("URLS", None)
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php"])

    def test_returns_empty_when_nothing_configured(self) -> None:
        import os

        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            urls = _kinozal_urls()
        self.assertEqual(urls, [])

    def test_URLS_takes_priority_over_KINOZAL_TOP_URL(self) -> None:
        import os

        env = {
            "URLS": "label|https://kinozal.tv/top.php",
            "KINOZAL_TOP_URL": "https://other.example.com",
        }
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php"])


# ── run_kinozal_pipeline ──────────────────────────────────────────────────────


class _FetchingPipeline:
    """Variant of run_kinozal_pipeline that accepts pre-loaded HTML instead of fetching."""

    def __init__(self, html_by_source_id: dict[str, str]) -> None:
        self._html = html_by_source_id

    def run(
        self,
        storage: InMemoryStorage,
        notifier: InMemoryNotifier,
        youtube: Any,
        sources_config: dict[str, Any],
    ) -> None:
        from generic_pipeline import Notification, build_notification, extract_from_html

        kinozal_sources = [
            s
            for s in sources_config["sources"]
            if s.get("enabled") and s["id"].startswith("kinozal_")
        ]
        source_map = {s["id"]: s for s in kinozal_sources}
        all_items = []
        for source in kinozal_sources:
            html = self._html.get(source["id"], "")
            result = extract_from_html(html, source)
            if result.ok:
                all_items.extend(result.items)

        existing = storage.get_existing_keys("movies")
        new_items = [i for i in all_items if i.dedupe_key not in existing]
        if not new_items:
            return
        storage.append_rows("movies", ROW_HEADERS, [i.to_row() for i in new_items])
        notifications: list[Notification] = []
        for item in new_items:
            item.trailer_url = enrich_with_trailer(item, youtube)
            template = source_map[item.source_id]["message_template"]
            notifications.append(build_notification(item, template))
        notifier.send_items(notifications)


def _run(
    html: str = _KINOZAL_HTML,
    existing_keys: set[str] | None = None,
    fail_ids: set[str] | None = None,
    youtube: Any = None,
    sources_config: dict[str, Any] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    if existing_keys:
        for key in existing_keys:
            storage._keys["movies"].add(key)
    notifier = InMemoryNotifier(fail_ids=fail_ids)
    runner = _FetchingPipeline({"kinozal_movies": html})
    runner.run(storage, notifier, youtube or _FakeYoutube(), sources_config or _SOURCES_CONFIG)
    return storage, notifier


class TestPipelineDeduplication(unittest.TestCase):
    def test_new_items_stored_and_notified(self) -> None:
        storage, notifier = _run()
        self.assertEqual(len(storage.stored_rows("movies")), 2)
        self.assertEqual(len(notifier.sent), 2)

    def test_already_existing_item_not_re_notified(self) -> None:
        storage, notifier = _run(existing_keys={"Film One / 2024 / BDRip"})
        self.assertEqual(len(storage.stored_rows("movies")), 1)
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(notifier.sent[0].id, "Film Two")

    def test_all_existing_no_notifications(self) -> None:
        keys = {"Film One / 2024 / BDRip", "Film Two"}
        storage, notifier = _run(existing_keys=keys)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])


class TestPipelineNotificationContent(unittest.TestCase):
    def test_build_notification_used_not_hardcoded(self) -> None:
        """Template from sources.json drives the message, not f-strings."""
        storage, notifier = _run()
        text = notifier.sent[0].text
        self.assertIn("<b>", text)
        self.assertIn("kinozal.tv/details.php", text)

    def test_trailer_included_when_present(self) -> None:
        storage, notifier = _run()
        text = notifier.sent[0].text
        self.assertIn("youtube.com", text)

    def test_no_trailing_newlines_when_trailer_empty(self) -> None:
        storage, notifier = _run(youtube=_RaisingYoutube())
        for notif in notifier.sent:
            self.assertFalse(notif.text.endswith("\n"), repr(notif.text))

    def test_image_url_on_notification(self) -> None:
        storage, notifier = _run()
        self.assertTrue(notifier.sent[0].image_url.startswith("https://"))

    def test_source_map_routes_correct_template(self) -> None:
        second_source = {
            **_KINOZAL_SOURCE,
            "id": "kinozal_movies_2",
            "message_template": "SECOND:{title}",
        }
        html2 = '<a href="/details.php?id=99" title="Film Three"><img src="/p.jpg"></a>'
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        runner = _FetchingPipeline(
            {
                "kinozal_movies": _KINOZAL_HTML,
                "kinozal_movies_2": html2,
            }
        )
        config = {"version": 1, "sources": [_KINOZAL_SOURCE, second_source]}
        runner.run(storage, notifier, _FakeYoutube(), config)
        texts = {n.text for n in notifier.sent}
        self.assertTrue(any("SECOND:" in t for t in texts))
        self.assertTrue(any("<b>" in t for t in texts))


class TestPipelineWriteBeforeNotify(unittest.TestCase):
    def test_storage_written_before_notifications_sent(self) -> None:
        """Rows must be in storage before notifier.send_items is called."""
        written_before_send: list[int] = []

        class _OrderCheckNotifier(InMemoryNotifier):
            def send_items(
                self, notifications: list[Notification]
            ) -> tuple[list[Notification], list[Notification]]:
                written_before_send.append(len(storage.stored_rows("movies")))
                return super().send_items(notifications)

        storage = InMemoryStorage()
        notifier = _OrderCheckNotifier()
        runner = _FetchingPipeline({"kinozal_movies": _KINOZAL_HTML})
        runner.run(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertTrue(written_before_send, "notifier never called")
        self.assertGreater(written_before_send[0], 0)


class TestPipelineFailureIsolation(unittest.TestCase):
    def test_empty_html_gives_no_notifications_no_crash(self) -> None:
        storage, notifier = _run(html="<html></html>")
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])

    def test_partial_html_extracts_valid_items(self) -> None:
        html = """
        <a href="/details.php?id=1" title="Good Film"><img src="/p.jpg"></a>
        <a href="/other.php?id=2" title="Skipped">no img</a>
        """
        storage, notifier = _run(html=html)
        self.assertEqual(len(storage.stored_rows("movies")), 1)
        self.assertEqual(notifier.sent[0].id, "Good Film")

    def test_no_enabled_sources_does_nothing(self) -> None:
        config = {
            "version": 1,
            "sources": [{**_KINOZAL_SOURCE, "enabled": False}],
        }
        storage, notifier = _run(sources_config=config)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])


if __name__ == "__main__":
    unittest.main()
