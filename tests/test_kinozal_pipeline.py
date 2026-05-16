import os
import unittest
import unittest.mock
from typing import Any

from generic_pipeline import NormalizedItem, Notification, extract_from_html
from kinozal_pipeline import (
    _kinozal_title,
    _kinozal_urls,
    enrich_with_trailer,
    run_kinozal_pipeline,
)
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
    def _item(self, raw: str) -> NormalizedItem:
        return NormalizedItem(
            dedupe_key=raw,
            title=_kinozal_title(raw),
            source_id="kinozal_movies",
            raw={"kinozal_raw_title": raw},
        )

    def test_clean_title_used_for_lookup(self) -> None:
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

    def test_year_extracted_from_dedupe_key_and_passed(self) -> None:
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

    def test_parentheses_stripped_before_youtube_query(self) -> None:
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


# ── _kinozal_title ────────────────────────────────────────────────────────────


class TestKinozalTitle(unittest.TestCase):
    def test_strips_metadata(self) -> None:
        raw = "Гнев (1 сезон: 1-7 серии из 7) / Man on Fire / 2026 / ДБ (Videofilm Int.), CT / WEB-DLRip"
        self.assertEqual(_kinozal_title(raw), "Гнев (1 сезон: 1-7 серии из 7)")

    def test_no_separator_returns_as_is(self) -> None:
        self.assertEqual(_kinozal_title("Дюна"), "Дюна")

    def test_slash_without_spaces_not_split(self) -> None:
        self.assertEqual(_kinozal_title("ДБ (Videofilm/Int.)"), "ДБ (Videofilm/Int.)")


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
        with unittest.mock.patch.dict(
            os.environ,
            {"URLS": "топ|https://kinozal.tv/top.php;новинки|https://kinozal.tv/new.php"},
            clear=False,
        ):
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php", "https://kinozal.tv/new.php"])

    def test_falls_back_to_KINOZAL_TOP_URL(self) -> None:
        env = {"KINOZAL_TOP_URL": "https://kinozal.tv/top.php"}
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("URLS", None)
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php"])

    def test_returns_empty_when_nothing_configured(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            urls = _kinozal_urls()
        self.assertEqual(urls, [])

    def test_URLS_takes_priority_over_KINOZAL_TOP_URL(self) -> None:
        env = {
            "URLS": "label|https://kinozal.tv/top.php",
            "KINOZAL_TOP_URL": "https://other.example.com",
        }
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php"])


# ── run_kinozal_pipeline (direct invocation, no helper duplicating prod logic) ─


def _run(
    html: str = _KINOZAL_HTML,
    existing_keys: set[str] | None = None,
    fail_ids: set[str] | None = None,
    youtube: Any = None,
    sources_config: dict[str, Any] | None = None,
    notifier: InMemoryNotifier | None = None,
    storage: InMemoryStorage | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    """Run the real run_kinozal_pipeline with HTTP patched and URLS env set.

    This invokes production code directly so tests fail if the pipeline
    behaviour changes — no inline copy of the orchestration logic.
    """
    storage = storage if storage is not None else InMemoryStorage()
    if existing_keys:
        for key in existing_keys:
            storage._keys["movies"].add(key)
    notifier = notifier if notifier is not None else InMemoryNotifier(fail_ids=fail_ids)

    with (
        unittest.mock.patch("kinozal_pipeline._fetch_html", return_value=html),
        unittest.mock.patch.dict(
            os.environ,
            {"URLS": "top|https://test.example/top.php"},
            clear=False,
        ),
    ):
        run_kinozal_pipeline(
            storage,
            notifier,
            youtube or _FakeYoutube(),
            sources_config or _SOURCES_CONFIG,
        )
    return storage, notifier


class TestPipelineDeduplication(unittest.TestCase):
    def test_new_items_stored_and_notified(self) -> None:
        storage, notifier = _run()
        self.assertEqual(len(storage.stored_rows("movies")), 2)
        self.assertEqual(len(notifier.sent), 2)

    def test_already_existing_item_not_re_notified(self) -> None:
        storage, notifier = _run(existing_keys={"Film One"})
        self.assertEqual(len(storage.stored_rows("movies")), 1)
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(notifier.sent[0].id, "Film Two")

    def test_all_existing_no_notifications(self) -> None:
        keys = {"Film One", "Film Two"}
        storage, notifier = _run(existing_keys=keys)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])

    def test_multiple_repacks_same_title_one_notification(self) -> None:
        html = """
        <html><body>
        <a href="/details.php?id=1" title="Great Film / 2025 / Portable"><img src="/p1.jpg"></a>
        <a href="/details.php?id=2" title="Great Film / 2025 / RePack (FitGirl)"><img src="/p2.jpg"></a>
        <a href="/details.php?id=3" title="Great Film / 2025 / RePack (другой)"><img src="/p3.jpg"></a>
        </body></html>
        """
        storage, notifier = _run(html=html)
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(notifier.sent[0].id, "Great Film")
        self.assertEqual(len(storage.stored_rows("movies")), 1)

    def test_stored_dedupe_key_is_clean_title(self) -> None:
        storage, notifier = _run()
        row = storage.stored_rows("movies")[0]
        dedupe_key, title = row[0], row[1]
        self.assertEqual(dedupe_key, "Film One")
        self.assertEqual(title, "Film One")


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


class TestPipelineWriteBeforeNotify(unittest.TestCase):
    def test_storage_written_before_notifications_sent(self) -> None:
        """Rows must be in storage before notifier.send_items is called."""
        storage = InMemoryStorage()
        rows_visible_at_send: list[int] = []

        class _OrderCheckNotifier(InMemoryNotifier):
            def send_items(
                self, notifications: list[Notification]
            ) -> tuple[list[Notification], list[Notification]]:
                rows_visible_at_send.append(len(storage.stored_rows("movies")))
                return super().send_items(notifications)

        notifier = _OrderCheckNotifier()
        _run(storage=storage, notifier=notifier)
        self.assertTrue(rows_visible_at_send, "notifier never called")
        self.assertGreater(rows_visible_at_send[0], 0)


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

    def test_no_urls_configured_does_nothing(self) -> None:
        """Pipeline early-exits when neither URLS nor KINOZAL_TOP_URL is set."""
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch("kinozal_pipeline._fetch_html", return_value=_KINOZAL_HTML),
            unittest.mock.patch.dict(os.environ, {}, clear=True),
        ):
            run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])

    def test_fetch_failure_isolated_pipeline_continues(self) -> None:
        """A failed _fetch_html for one URL shouldn't crash the pipeline."""
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch("kinozal_pipeline._fetch_html", side_effect=RuntimeError("boom")),
            unittest.mock.patch.dict(
                os.environ,
                {"URLS": "top|https://test.example/top.php"},
                clear=False,
            ),
        ):
            run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])


if __name__ == "__main__":
    unittest.main()
