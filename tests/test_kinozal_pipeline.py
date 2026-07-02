import os
import re
import unittest
import unittest.mock
from typing import Any

import kinozal_scraper.kinozal_pipeline as kp
from kinozal_scraper.generic_pipeline import NormalizedItem, PipelineResult, extract_from_html
from kinozal_scraper.kinozal_auth import KinozalLoginError
from kinozal_scraper.kinozal_pipeline import (
    _kinozal_title,
    _kinozal_urls,
    enrich_with_trailer,
    run_kinozal_pipeline,
)
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import InMemoryStorage
from kinozal_scraper.telegram_notifier import InMemoryNotifier
from kinozal_scraper.text_utils import title_year_matches as _title_year_matches

# ── minimal synthetic HTML matching kinozal_movies row_selector ──────────────

_KINOZAL_HTML = """
<html><body>
<a href="/details.php?id=1" title="Film One / 2024 / BDRip"><img src="/img/p1.jpg"></a>
<a href="/details.php?id=2" title="Film Two"><img src="https://cdn.example.com/p2.jpg"></a>
</body></html>
"""

_KINOZAL_SOURCE: dict[str, Any] = {
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
    def test_falls_back_to_KINOZAL_TOP_URL(self) -> None:
        env = {"KINOZAL_TOP_URL": "https://kinozal.tv/top.php"}
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("KINOZAL_URLS", None)
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php"])

    def test_returns_empty_when_nothing_configured(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KINOZAL_URLS", None)
            os.environ.pop("KINOZAL_TOP_URL", None)
            urls = _kinozal_urls()
        self.assertEqual(urls, [])

    def test_KINOZAL_URLS_takes_priority_over_KINOZAL_TOP_URL(self) -> None:
        env = {
            "KINOZAL_URLS": "label|https://kinozal.tv/top.php",
            "KINOZAL_TOP_URL": "https://other.example.com",
        }
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php"])

    def test_reads_KINOZAL_URLS(self) -> None:
        # After the URLS→KINOZAL_URLS rename (#263): the new variable is the one read.
        with unittest.mock.patch.dict(
            os.environ,
            {"KINOZAL_URLS": "топ|https://kinozal.tv/top.php;новинки|https://kinozal.tv/new.php"},
            clear=False,
        ):
            os.environ.pop("URLS", None)
            os.environ.pop("KINOZAL_TOP_URL", None)
            urls = _kinozal_urls()
        self.assertEqual(urls, ["https://kinozal.tv/top.php", "https://kinozal.tv/new.php"])

    def test_old_URLS_not_read(self) -> None:
        # Clean cut (#263): the legacy URLS name is no longer a fallback. With only
        # URLS set and no KINOZAL_URLS/KINOZAL_TOP_URL, the pipeline sees no URLs.
        with unittest.mock.patch.dict(
            os.environ,
            {"URLS": "топ|https://kinozal.tv/top.php"},
            clear=False,
        ):
            os.environ.pop("KINOZAL_URLS", None)
            os.environ.pop("KINOZAL_TOP_URL", None)
            urls = _kinozal_urls()
        self.assertEqual(urls, [])


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
    """Run the real run_kinozal_pipeline with HTTP patched and KINOZAL_URLS env set.

    This invokes production code directly so tests fail if the pipeline
    behaviour changes — no inline copy of the orchestration logic.
    """
    storage = storage if storage is not None else InMemoryStorage()
    if existing_keys:
        storage.seed_existing("movies", existing_keys)
    notifier = notifier if notifier is not None else InMemoryNotifier(fail_ids=fail_ids)

    with (
        unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_html", return_value=html),
        unittest.mock.patch.dict(
            os.environ,
            {"KINOZAL_URLS": "top|https://test.example/top.php"},
            clear=False,
        ),
    ):
        # Ambient KINOZAL_* creds are cleared globally by the autouse fixture in
        # conftest.py, so a failed fetch_html here never triggers a real login().
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


def _html_with_n_films(n: int) -> str:
    """Top-page HTML with n distinct films (distinct clean titles → no dedup-collapse)."""
    rows = "\n".join(
        f'<a href="/details.php?id={i}" title="Movie {i} / 2024 / BDRip"><img src="/p{i}.jpg"></a>'
        for i in range(1, n + 1)
    )
    return f"<html><body>{rows}</body></html>"


class TestKinozalSourceConfig(unittest.TestCase):
    """Regression guard for #173: the real sources.json must let the whole top
    page through, not just the first 10. Loads the actual config (so it also
    goes through §VI fail-fast validation) and asserts the limit covers a full
    top.php page (50 films)."""

    _FULL_PAGE = 50

    def test_kinozal_movies_limit_covers_full_page(self) -> None:
        config = load_sources_config()
        kinozal = next(s for s in config["sources"] if s["id"] == "kinozal_movies")
        self.assertGreaterEqual(int(kinozal["limit"]), self._FULL_PAGE)


class TestPipelineCoverage(unittest.TestCase):
    def test_all_top_films_notified_not_truncated(self) -> None:
        # End-to-end against the REAL sources.json: with the production limit a
        # 15-film page must yield 15 notifications. Before the fix (limit:10)
        # only 10 go out — this reproduces the #173 defect.
        html = _html_with_n_films(15)
        storage, notifier = _run(html=html, sources_config=load_sources_config())
        self.assertEqual(len(notifier.sent), 15)
        self.assertEqual(len(storage.stored_rows("movies")), 15)

    def test_extraction_coverage_logged(self) -> None:
        # §IV: every run logs its coverage (extracted / new / already-seen) so a
        # future "film vanished" reads in the Actions log instead of looking
        # like "no new films". _KINOZAL_HTML has 2 films, none pre-existing.
        with self.assertLogs("kinozal_scraper.kinozal_pipeline", level="INFO") as cm:
            _run()
        joined = "\n".join(cm.output)
        self.assertRegex(joined, r"2 extracted.*2 new.*0 already-seen")

    def test_coverage_logged_even_when_no_new_items(self) -> None:
        # The "0 new" path is the most common silent case — coverage must still
        # surface there, before the early return.
        with self.assertLogs("kinozal_scraper.kinozal_pipeline", level="INFO") as cm:
            _run(existing_keys={"Film One", "Film Two"})
        joined = "\n".join(cm.output)
        self.assertRegex(joined, r"2 extracted.*0 new.*2 already-seen")

    def test_trailer_failure_still_notifies(self) -> None:
        # Burst 10→50 raises YouTube-quota exhaustion risk. A trailer lookup
        # failure must degrade visibly (§IV): the film still ships, sans
        # trailer, with an ERROR logged — never a silent drop.
        with self.assertLogs("kinozal_scraper.kinozal_pipeline", level="ERROR") as cm:
            storage, notifier = _run(youtube=_RaisingYoutube())
        self.assertEqual(len(notifier.sent), 2)
        self.assertTrue(any("trailer lookup failed" in line for line in cm.output))


class TestPipelineNotificationContent(unittest.TestCase):
    def test_build_notification_used_not_hardcoded(self) -> None:
        """Template from sources.json drives the message, not f-strings."""
        storage, notifier = _run()
        text = notifier.sent[0].text
        self.assertIn("<b>", text)
        # Link host follows the origin that served the listing (#247); _run mocks
        # the fetch at https://test.example, so the resolved link does too.
        self.assertIn("test.example/details.php", text)

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
        """Pipeline early-exits when neither KINOZAL_URLS nor KINOZAL_TOP_URL is set."""
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", return_value=_KINOZAL_HTML
            ),
            unittest.mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("KINOZAL_URLS", None)
            os.environ.pop("KINOZAL_TOP_URL", None)
            run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])

    def test_fetch_failure_isolated_pipeline_continues(self) -> None:
        """A failed fetch_html for one URL shouldn't crash the pipeline."""
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=RuntimeError("boom")
            ),
            unittest.mock.patch.dict(
                os.environ,
                {"KINOZAL_URLS": "top|https://test.example/top.php"},
                clear=False,
            ),
        ):
            run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertEqual(notifier.sent, [])


# ── exit-code surface (issue #97) ─────────────────────────────────────────────


class TestKinozalPipelineExitCodeSurface(unittest.TestCase):
    """run_kinozal_pipeline must return list[PipelineResult] so __main__ can
    sys.exit(1) on failed source. Previously fetch errors were silent — #97."""

    def test_fetch_failure_returns_not_ok_result(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=RuntimeError("boom")
            ),
            unittest.mock.patch.dict(
                os.environ,
                {"KINOZAL_URLS": "top|https://test.example/top.php"},
                clear=False,
            ),
        ):
            results = run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) >= 1)
        self.assertIsInstance(results[0], PipelineResult)
        self.assertTrue(any(not r.ok for r in results))
        self.assertTrue(
            any("fetch failed" in err for r in results for err in r.errors),
            f"expected 'fetch failed' in any result's errors, got: {[r.errors for r in results]}",
        )

    def test_successful_run_returns_all_ok_results(self) -> None:
        storage, notifier = _run()
        # Re-invoke directly to capture return value (helper discards it).
        storage2 = InMemoryStorage()
        notifier2 = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", return_value=_KINOZAL_HTML
            ),
            unittest.mock.patch.dict(
                os.environ,
                {"KINOZAL_URLS": "top|https://test.example/top.php"},
                clear=False,
            ),
        ):
            results = run_kinozal_pipeline(storage2, notifier2, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertTrue(all(r.ok for r in results))

    def test_extraction_failure_propagates_to_result_errors(self) -> None:
        """HTML drift (selector matches zero rows) must surface as result.errors,
        not be swallowed silently. Previously _extract_kinozal_items logged and
        returned [], hiding the failure from __main__'s exit-code surface (review
        finding on PR #102)."""
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", return_value="<html></html>"
            ),
            unittest.mock.patch.dict(
                os.environ,
                {"KINOZAL_URLS": "top|https://test.example/top.php"},
                clear=False,
            ),
        ):
            results = run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        self.assertTrue(
            any(not r.ok for r in results),
            f"expected at least one not-ok result, got: {[(r.source_id, r.ok) for r in results]}",
        )
        self.assertTrue(
            any(r.errors for r in results),
            f"expected extraction errors to propagate, got: {[r.errors for r in results]}",
        )


class TestKinozalEmptyUrlGuard(unittest.TestCase):
    def test_url_field_drift_logs_warning_but_still_notifies(self) -> None:
        """Empty url after extraction must surface to the user (notification still sent,
        just without a link) AND to logs (WARNING). Silently skipping would look like
        \"no new films\" — visible failure is the user's only way to report drift.
        """
        drifted_source: dict[str, Any] = {
            **_KINOZAL_SOURCE,
            "fields": {**_KINOZAL_SOURCE["fields"], "url": "@data-link"},
        }
        config = {"version": 1, "sources": [drifted_source]}
        with self.assertLogs("kinozal_scraper.kinozal_pipeline", level="WARNING") as logs:
            storage, notifier = _run(sources_config=config)
        self.assertEqual(len(storage.stored_rows("movies")), 2)
        self.assertEqual(len(notifier.sent), 2)
        for notif in notifier.sent:
            self.assertNotIn("kinozal.tv/details", notif.text)
        self.assertTrue(
            any("empty url field" in msg for msg in logs.output),
            f"expected 'empty url field' warning in logs: {logs.output}",
        )


# ── delivery truthfulness (Principle III, issue #132) ─────────────────────────


def _run_results(
    html: str = _KINOZAL_HTML,
    fail_ids: set[str] | None = None,
    existing_keys: set[str] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier, list[PipelineResult]]:
    """Invoke run_kinozal_pipeline directly, returning the PipelineResult list
    so delivery-truthfulness assertions can inspect ok / errors."""
    storage = InMemoryStorage()
    if existing_keys:
        storage.seed_existing("movies", existing_keys)
    notifier = InMemoryNotifier(fail_ids=fail_ids)
    with (
        unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_html", return_value=html),
        unittest.mock.patch.dict(
            os.environ,
            {"KINOZAL_URLS": "top|https://test.example/top.php"},
            clear=False,
        ),
    ):
        results = run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
    return storage, notifier, results


class TestDeliveryTruthfulness(unittest.TestCase):
    """Persisted dedupe state must reflect confirmed delivery (Principle III).
    Failed Telegram delivery must be a visible anomaly (result.ok False +
    errors), and failed items must NOT be stored so they retry next run."""

    def test_failed_notifications_excluded_from_storage(self) -> None:
        storage, notifier, _ = _run_results(fail_ids={"Film One"})
        stored_keys = {row[0] for row in storage.stored_rows("movies")}
        self.assertEqual(stored_keys, {"Film Two"})
        self.assertEqual({n.id for n in notifier.sent}, {"Film Two"})
        self.assertEqual({n.id for n in notifier.failed}, {"Film One"})

    def test_failed_notifications_mark_result_not_ok(self) -> None:
        _, _, results = _run_results(fail_ids={"Film One"})
        self.assertTrue(any(not r.ok for r in results))
        self.assertTrue(
            any(r.errors for r in results),
            f"expected delivery failure in errors, got: {[r.errors for r in results]}",
        )

    def test_all_failed_writes_nothing(self) -> None:
        storage, _, results = _run_results(fail_ids={"Film One", "Film Two"})
        self.assertEqual(storage.stored_rows("movies"), [])
        self.assertTrue(any(not r.ok for r in results))

    def test_all_sent_writes_all_rows(self) -> None:
        storage, notifier, results = _run_results()
        self.assertEqual(len(storage.stored_rows("movies")), 2)
        self.assertEqual(len(notifier.sent), 2)
        self.assertTrue(all(r.ok for r in results))


class TestKinozalKnownBugs(unittest.TestCase):
    """Documents current behaviour for scenarios that should ideally be louder."""

    def test_youtube_quota_exhausted_pipeline_continues_with_empty_trailer(self) -> None:
        """YouTube quota → enrich_with_trailer swallows the exception → trailer=''.

        Pipeline still publishes items, but their notification text carries no
        trailer link. Documented as a quiet degradation (G in the taxonomy).
        """

        class _QuotaExhaustedYoutube:
            def get_trailer_url(self, film: str, year: int | None = None) -> str:
                raise RuntimeError("quotaExceeded")

        storage, notifier = _run(youtube=_QuotaExhaustedYoutube())
        self.assertEqual(len(storage.stored_rows("movies")), 2)
        self.assertEqual(len(notifier.sent), 2)
        for notif in notifier.sent:
            self.assertNotIn("youtube.com", notif.text)


class TestMirrorUrl(unittest.TestCase):
    def test_swaps_host_keeps_path_and_query(self) -> None:
        from kinozal_scraper.kinozal_pipeline import _mirror_url

        self.assertEqual(
            _mirror_url("https://kinozal.tv/top.php?j=&t=0&d=14"),
            "https://kinozal.guru/top.php?j=&t=0&d=14",
        )


class TestPipelineAuth(unittest.TestCase):
    """Mirror failover (issue #227): anonymous kinozal.tv is primary; the
    authenticated kinozal.guru mirror is a lazy fallback used only when a
    primary fetch fails. Login happens at most once per run, on first fallback.
    Failover and both-failed are visible (§IV); credentials are an optional
    backup, not a hard requirement."""

    _URLS = {"KINOZAL_URLS": "top|https://kinozal.tv/top.php?d=14"}

    def _run_with_env(
        self, env: dict[str, str], urls: str | None = None
    ) -> tuple[list[PipelineResult], InMemoryStorage, InMemoryNotifier]:
        # Ambient KINOZAL_* creds are cleared by the conftest.py autouse fixture;
        # `env` sets exactly the credentials each test wants to exercise.
        full = dict(self._URLS)
        if urls is not None:
            full["KINOZAL_URLS"] = urls
        full.update(env)
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch.dict(os.environ, full, clear=False):
            results = run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        return results, storage, notifier

    def test_primary_success_skips_login_and_mirror(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", return_value=_KINOZAL_HTML
            ) as mfetch,
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login") as mlogin,
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_authenticated") as mauth,
        ):
            self._run_with_env({"KINOZAL_USERNAME": "u", "KINOZAL_PASSWORD": "p"})
        mfetch.assert_called()
        mlogin.assert_not_called()  # healthy .tv run pays no login cost
        mauth.assert_not_called()

    def test_primary_failure_falls_back_to_authenticated_mirror(self) -> None:
        sentinel = unittest.mock.Mock()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html",
                side_effect=RuntimeError("HTTP Error 522"),
            ),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.login", return_value=sentinel
            ) as mlogin,
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=_KINOZAL_HTML
            ) as mauth,
        ):
            _, _, notifier = self._run_with_env({"KINOZAL_USERNAME": "u", "KINOZAL_PASSWORD": "p"})
        mlogin.assert_called_once()
        mirror_url = mauth.call_args[0][1]
        self.assertIn("kinozal.guru", mirror_url)  # fetched the mirror host
        self.assertEqual(len(notifier.sent), 2)  # items served from mirror

    def test_login_is_lazy_and_once_across_urls(self) -> None:
        sentinel = unittest.mock.Mock()
        two_urls = "a|https://kinozal.tv/top.php?d=14;b|https://kinozal.tv/top.php?d=0"
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=RuntimeError("522")
            ),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.login", return_value=sentinel
            ) as mlogin,
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=_KINOZAL_HTML
            ) as mauth,
        ):
            self._run_with_env({"KINOZAL_USERNAME": "u", "KINOZAL_PASSWORD": "p"}, urls=two_urls)
        mlogin.assert_called_once()  # one login for the whole run
        self.assertEqual(mauth.call_count, 2)  # both urls retried via mirror

    def test_no_credentials_primary_failure_surfaces_without_mirror(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html",
                side_effect=RuntimeError("HTTP Error 522"),
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login") as mlogin,
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_authenticated") as mauth,
        ):
            results, _, _ = self._run_with_env({})
        mlogin.assert_not_called()
        mauth.assert_not_called()
        errs = [e for r in results for e in r.errors]
        self.assertTrue(any("522" in e for e in errs), errs)
        self.assertTrue(any("mirror" in e.lower() and "disabled" in e.lower() for e in errs), errs)
        self.assertTrue(any(not r.ok for r in results))  # → exit 1

    def test_mirror_login_failure_surfaces_visible_error(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=RuntimeError("522")
            ),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.login", side_effect=KinozalLoginError("bad creds")
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_authenticated") as mauth,
        ):
            results, _, _ = self._run_with_env({"KINOZAL_USERNAME": "u", "KINOZAL_PASSWORD": "p"})
        mauth.assert_not_called()
        errs = [e for r in results for e in r.errors]
        self.assertTrue(any("login failed" in e.lower() for e in errs), errs)
        self.assertTrue(any(not r.ok for r in results))  # → exit 1

    def test_both_primary_and_mirror_fail_records_combined(self) -> None:
        sentinel = unittest.mock.Mock()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=RuntimeError("522")
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login", return_value=sentinel),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated",
                side_effect=RuntimeError("mirror 500"),
            ),
        ):
            results, _, _ = self._run_with_env({"KINOZAL_USERNAME": "u", "KINOZAL_PASSWORD": "p"})
        errs = [e for r in results for e in r.errors]
        self.assertTrue(any("522" in e and "mirror" in e.lower() for e in errs), errs)
        self.assertTrue(any(not r.ok for r in results))  # → exit 1

    def test_partial_credentials_warn_and_disable_mirror(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=RuntimeError("522")
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login") as mlogin,
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_authenticated") as mauth,
            self.assertLogs("kinozal_scraper.kinozal_pipeline", level="WARNING") as cm,
        ):
            results, _, _ = self._run_with_env({"KINOZAL_USERNAME": "u"})
        mlogin.assert_not_called()  # partial creds → mirror disabled, no login
        mauth.assert_not_called()
        self.assertTrue(any("partial credentials" in line for line in cm.output))
        self.assertTrue(any(not r.ok for r in results))  # primary 522 still red


_POSTER_TV = "https://kinozal.tv/i/poster/2/7/2136727.jpg"


class TestFetchPoster(unittest.TestCase):
    """Poster download shares the listing's origin→mirror failover (#241).

    The kinozal.tv origin can 522 while the same poster path is served 200 by
    the kinozal.guru mirror — anonymously (verified). Before #241 the poster was
    fetched by the generic notifier straight at the dead origin, so it dropped to
    text even though the mirror had it. `Kinozal.fetch_poster` closes that split:
    one object, one origin-vs-mirror decision, for HTML *and* posters."""

    def _kinozal(self) -> Any:
        from kinozal_scraper.kinozal_pipeline import Kinozal

        return Kinozal("u", "p")

    def test_primary_success_returns_origin_bytes_no_mirror(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_bytes", return_value=b"PRIMARY"
            ) as mfetch,
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login") as mlogin,
        ):
            data = self._kinozal().fetch_poster(_POSTER_TV)
        self.assertEqual(data, b"PRIMARY")
        mfetch.assert_called_once_with(_POSTER_TV)
        mlogin.assert_not_called()

    def test_kinozal_host_failure_falls_back_to_mirror_host(self) -> None:
        calls: list[str] = []

        def _fetch(url: str) -> bytes:
            calls.append(url)
            if "kinozal.tv" in url:
                raise RuntimeError("HTTP Error 522")
            return b"MIRROR"

        with unittest.mock.patch(
            "kinozal_scraper.kinozal_pipeline.fetch_bytes", side_effect=_fetch
        ):
            data = self._kinozal().fetch_poster(_POSTER_TV)
        self.assertEqual(data, b"MIRROR")
        self.assertEqual(len(calls), 2)
        self.assertIn("kinozal.guru", calls[1])

    def test_mirror_host_swap_preserves_path_and_query(self) -> None:
        calls: list[str] = []

        def _fetch(url: str) -> bytes:
            calls.append(url)
            if "kinozal.tv" in url:
                raise RuntimeError("522")
            return b"M"

        with unittest.mock.patch(
            "kinozal_scraper.kinozal_pipeline.fetch_bytes", side_effect=_fetch
        ):
            self._kinozal().fetch_poster("https://kinozal.tv/i/poster/2/7/2136727.jpg?x=1")
        self.assertEqual(calls[1], "https://kinozal.guru/i/poster/2/7/2136727.jpg?x=1")

    def test_mirror_poster_fetch_is_anonymous(self) -> None:
        def _fetch(url: str) -> bytes:
            if "kinozal.tv" in url:
                raise RuntimeError("522")
            return b"M"

        with (
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_bytes", side_effect=_fetch),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login") as mlogin,
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_authenticated") as mauth,
        ):
            self._kinozal().fetch_poster(_POSTER_TV)
        mlogin.assert_not_called()  # poster mirror-fetch is anonymous (verified 200)
        mauth.assert_not_called()

    def test_third_party_host_failure_propagates(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_bytes",
                side_effect=RuntimeError("boom"),
            ) as mfetch,
            self.assertRaises(RuntimeError),
        ):
            self._kinozal().fetch_poster("https://i123.fastpic.org/big/x.jpg")
        mfetch.assert_called_once()  # third-party host: no kinozal-mirror retry

    def test_both_primary_and_mirror_fail_propagates(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_bytes",
                side_effect=RuntimeError("522 everywhere"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self._kinozal().fetch_poster(_POSTER_TV)  # double-fail surfaces (§IV)

    def test_already_mirror_host_not_reswapped(self) -> None:
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_bytes",
                side_effect=RuntimeError("522"),
            ) as mfetch,
            self.assertRaises(RuntimeError),
        ):
            self._kinozal().fetch_poster("https://kinozal.guru/i/poster/x.jpg")
        mfetch.assert_called_once()  # already on mirror — no pointless re-swap retry


class TestNotifierWiring(unittest.TestCase):
    """Payoff guard (#241): proves prod actually routes posters through the
    mirror-aware fetcher, not the default `fetch_bytes`. The bug lived in the
    `__main__` wiring (notifier built without `image_fetcher`); a test that
    re-built the notifier by hand would only prove the seam, not the wiring.
    This asserts the `__main__` factory wires `Kinozal.fetch_poster`."""

    def test_prod_factory_routes_poster_through_mirror(self) -> None:
        from kinozal_scraper.kinozal_pipeline import Kinozal, _build_notifier

        kinozal = Kinozal("u", "p")
        notifier = _build_notifier("tok", "chat", kinozal)

        def _fetch(url: str) -> bytes:
            if "kinozal.tv" in url:
                raise RuntimeError("522")
            return b"MIRROR"

        with unittest.mock.patch(
            "kinozal_scraper.kinozal_pipeline.fetch_bytes", side_effect=_fetch
        ):
            data = notifier._image_fetcher(_POSTER_TV)
        self.assertEqual(data, b"MIRROR")  # poster reached via mirror, not dropped


# ── link origin follows the serving host (issue #247) ─────────────────────────

# Distinct-title fixtures for the mixed-origin run: same-title collapse would
# make "which host won" ambiguous, so each URL yields its own title.
_HTML_A = (
    '<html><body><a href="/details.php?id=1" title="Alpha Film / 2024">'
    '<img src="/img/a.jpg"></a></body></html>'
)
_HTML_B = (
    '<html><body><a href="/details.php?id=2" title="Beta Film / 2024">'
    '<img src="/img/b.jpg"></a></body></html>'
)


class TestFetchListingOrigin(unittest.TestCase):
    """`fetch_listing` surfaces the origin that actually served the HTML (#247):
    kinozal.tv on primary success, kinozal.guru on mirror fallback — so the
    pipeline can resolve links against the host the listing truly came from."""

    def test_primary_success_returns_tv_base_url(self) -> None:
        from kinozal_scraper.kinozal_pipeline import Kinozal

        with unittest.mock.patch(
            "kinozal_scraper.kinozal_pipeline.fetch_html", return_value=_KINOZAL_HTML
        ):
            html, base = Kinozal("u", "p").fetch_listing("https://kinozal.tv/top.php?d=14")
        self.assertEqual(html, _KINOZAL_HTML)
        self.assertEqual(base, "https://kinozal.tv")

    def test_mirror_fallback_returns_guru_base_url(self) -> None:
        from kinozal_scraper.kinozal_pipeline import Kinozal

        sentinel = unittest.mock.Mock()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html",
                side_effect=RuntimeError("HTTP Error 522"),
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login", return_value=sentinel),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=_KINOZAL_HTML
            ),
        ):
            html, base = Kinozal("u", "p").fetch_listing("https://kinozal.tv/top.php?d=14")
        self.assertEqual(html, _KINOZAL_HTML)
        self.assertEqual(base, "https://kinozal.guru")


class TestLinkOriginFollowsHost(unittest.TestCase):
    """End-to-end (#247): notification links resolve against the host that served
    the listing. Injection stays on the HTTP boundary (fetch_html / login /
    fetch_authenticated) — never mock Kinozal.fetch_listing/_from_mirror (§II)."""

    _CREDS = {"KINOZAL_USERNAME": "u", "KINOZAL_PASSWORD": "p"}

    def _run(self, urls: str) -> InMemoryNotifier:
        full = {"KINOZAL_URLS": urls, **self._CREDS}
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch.dict(os.environ, full, clear=False):
            run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
        return notifier

    def test_mirror_fallback_links_use_guru_origin(self) -> None:
        sentinel = unittest.mock.Mock()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html",
                side_effect=RuntimeError("HTTP Error 522"),
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login", return_value=sentinel),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=_KINOZAL_HTML
            ),
        ):
            notifier = self._run("top|https://kinozal.tv/top.php?d=14")
        texts = "\n".join(n.text for n in notifier.sent)
        self.assertIn("kinozal.guru/details.php", texts)
        self.assertNotIn("kinozal.tv/details.php", texts)

    def test_mirror_fallback_poster_uses_guru_origin(self) -> None:
        sentinel = unittest.mock.Mock()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html",
                side_effect=RuntimeError("HTTP Error 522"),
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login", return_value=sentinel),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=_KINOZAL_HTML
            ),
        ):
            notifier = self._run("top|https://kinozal.tv/top.php?d=14")
        posters = [n.image_url for n in notifier.sent]
        # item 1 has a relative poster (/img/p1.jpg); the mirror origin must win.
        self.assertIn("https://kinozal.guru/img/p1.jpg", posters)

    def test_mixed_origin_each_link_matches_its_listing(self) -> None:
        def _fake_fetch(url: str) -> str:
            if "d=14" in url:  # URL-A: primary .tv succeeds
                return _HTML_A
            raise RuntimeError("HTTP Error 522")  # URL-B: 522 → mirror

        sentinel = unittest.mock.Mock()
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=_fake_fetch
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login", return_value=sentinel),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=_HTML_B
            ),
        ):
            notifier = self._run(
                "a|https://kinozal.tv/top.php?d=14;b|https://kinozal.tv/top.php?d=0"
            )
        alpha = next(n for n in notifier.sent if "Alpha Film" in n.text)
        beta = next(n for n in notifier.sent if "Beta Film" in n.text)
        self.assertIn("kinozal.tv/details.php?id=1", alpha.text)  # served by primary
        self.assertIn("kinozal.guru/details.php?id=2", beta.text)  # served by mirror


# ── genre exclusion filter (issue #263) ───────────────────────────────────────


def _details_html(genre: str) -> str:
    """Synthetic kinozal details page carrying a `Жанр:` field, matching the
    real structure (`<h2>...<b>Жанр:</b> <value> ...</h2>`, verified by PoC)."""
    return f"<html><body><h2><b>Жанр:</b> {genre}</h2></body></html>"


# Listing with two distinct items → two details pages keyed by id.
_GENRE_LISTING = (
    "<html><body>"
    '<a href="/details.php?id=1" title="Game A / RU / Hidden objects / 2024 / PC">'
    '<img src="/p1.jpg"></a>'
    '<a href="/details.php?id=2" title="Movie B / 2025 / BDRip"><img src="/p2.jpg"></a>'
    "</body></html>"
)
_GENRE_BY_ID = {"1": "Hidden objects", "2": "драма"}


class TestParseGenre(unittest.TestCase):
    """`_parse_genre` reads the `Жанр:` value off a details page (pure)."""

    def test_extracts_genre_from_details_html(self) -> None:
        self.assertEqual(kp._parse_genre(_details_html("Hidden objects")), "Hidden objects")

    def test_returns_empty_when_no_genre_field(self) -> None:
        self.assertEqual(kp._parse_genre("<html><body><h2>no genre here</h2></body></html>"), "")


class TestGenreMatching(unittest.TestCase):
    """`_genre_excluded` splits a (possibly multi-valued) genre string and tests
    membership against the denylist, case-insensitively and trimmed (pure)."""

    def test_multivalue_comma_any_match(self) -> None:
        self.assertTrue(kp._genre_excluded("боевик, триллер", {"триллер"}))

    def test_case_insensitive_trim(self) -> None:
        self.assertTrue(kp._genre_excluded(" Hidden Objects ", {"hidden objects"}))

    def test_not_excluded(self) -> None:
        self.assertFalse(kp._genre_excluded("драма", {"hidden objects"}))


class TestExcludedGenresEnv(unittest.TestCase):
    """`_excluded_genres` parses the `KINOZAL_EXCLUDED_GENRES` env into a
    normalized set (`;`-separated, lower/trim); unset → empty set."""

    def test_parses_semicolon_list(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {"KINOZAL_EXCLUDED_GENRES": "Hidden objects; Эротика"}, clear=False
        ):
            self.assertEqual(kp._excluded_genres(), {"hidden objects", "эротика"})

    def test_empty_when_unset(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KINOZAL_EXCLUDED_GENRES", None)
            self.assertEqual(kp._excluded_genres(), set())


def _run_genre_filter(
    excluded: str | None,
    listing: str = _GENRE_LISTING,
    genre_by_id: dict[str, str] | None = None,
    details_error: bool = False,
) -> tuple[InMemoryStorage, InMemoryNotifier, list[str]]:
    """Drive run_kinozal_pipeline with the genre filter, injecting at the HTTP
    boundary (`fetch_html`) like the #247 origin tests — NOT by mocking the
    Kinozal facade (§II). `fetch_html` dispatches by URL: a details.php URL
    returns that item's genre page (and is recorded), anything else the listing.
    Returns (storage, notifier, list-of-details-URLs-fetched).
    """
    genre_by_id = genre_by_id if genre_by_id is not None else _GENRE_BY_ID
    details_calls: list[str] = []

    def _fetch(url: str) -> str:
        if "details.php" in url:
            details_calls.append(url)
            if details_error:
                raise RuntimeError("details fetch boom")
            match = re.search(r"id=(\d+)", url)
            gid = match.group(1) if match else ""
            return _details_html(genre_by_id.get(gid, "unknown"))
        return listing

    storage = InMemoryStorage()
    notifier = InMemoryNotifier()
    env = {"KINOZAL_URLS": "top|https://kinozal.tv/top.php"}
    if excluded is not None:
        env["KINOZAL_EXCLUDED_GENRES"] = excluded
    with (
        unittest.mock.patch("kinozal_scraper.kinozal_pipeline.fetch_html", side_effect=_fetch),
        unittest.mock.patch.dict(os.environ, env, clear=False),
    ):
        os.environ.pop("KINOZAL_TOP_URL", None)
        if excluded is None:
            os.environ.pop("KINOZAL_EXCLUDED_GENRES", None)
        run_kinozal_pipeline(storage, notifier, _FakeYoutube(), _SOURCES_CONFIG)
    return storage, notifier, details_calls


class TestGenreFilter(unittest.TestCase):
    """#263: new items whose details-page genre ∈ KINOZAL_EXCLUDED_GENRES are
    dropped from notifications but still stored (dedup), fetch degrades visibly."""

    def test_excluded_genre_item_not_notified(self) -> None:
        _, notifier, _ = _run_genre_filter(excluded="Hidden objects")
        sent_ids = {n.id for n in notifier.sent}
        self.assertNotIn("Game A", sent_ids)  # Hidden objects → filtered
        self.assertIn("Movie B", sent_ids)  # драма → kept

    def test_non_excluded_item_notified(self) -> None:
        _, notifier, _ = _run_genre_filter(excluded="Hidden objects")
        self.assertIn("Movie B", {n.id for n in notifier.sent})

    def test_filtered_item_is_stored(self) -> None:
        storage, notifier, _ = _run_genre_filter(excluded="Hidden objects")
        stored = {row[0] for row in storage.stored_rows("movies")}
        self.assertIn("Game A", stored)  # stored so it isn't re-fetched next run
        self.assertNotIn("Game A", {n.id for n in notifier.sent})  # but not notified

    def test_all_filtered_still_stored(self) -> None:
        # Every new item excluded → kept empty, sent empty; filtered must STILL be
        # stored (store-guard keys on items_to_store, not on sent) and not crash.
        storage, notifier, _ = _run_genre_filter(excluded="Hidden objects; драма")
        self.assertEqual(notifier.sent, [])
        stored = {row[0] for row in storage.stored_rows("movies")}
        self.assertEqual(stored, {"Game A", "Movie B"})

    def test_details_fetch_failure_fails_open_and_warns(self) -> None:
        # §IV: unknown genre (fetch failed) must not silently drop the item —
        # it ships (fail-open) with a WARNING tripwire.
        with self.assertLogs("kinozal_scraper.kinozal_pipeline", level="WARNING") as cm:
            _, notifier, _ = _run_genre_filter(excluded="Hidden objects", details_error=True)
        self.assertEqual({n.id for n in notifier.sent}, {"Game A", "Movie B"})
        self.assertTrue(any("genre" in line.lower() for line in cm.output), cm.output)

    def test_no_details_fetch_when_denylist_empty(self) -> None:
        # Zero runtime overhead on the healthy default: empty denylist → no
        # details fetch at all. (Guard; passes trivially pre-implementation.)
        _, notifier, details_calls = _run_genre_filter(excluded=None)
        self.assertEqual(details_calls, [])
        self.assertEqual({n.id for n in notifier.sent}, {"Game A", "Movie B"})

    def test_filter_count_logged(self) -> None:
        with self.assertLogs("kinozal_scraper.kinozal_pipeline", level="INFO") as cm:
            _run_genre_filter(excluded="Hidden objects")
        joined = "\n".join(cm.output)
        self.assertRegex(joined, r"(?i)filter.*1|1.*excluded genre")


class TestKinozalFacade(unittest.TestCase):
    """`Kinozal.fetch_details` shares the listing origin→mirror failover (#263),
    injected at the HTTP boundary like the #247 fetch_listing tests (§II)."""

    def test_fetch_details_falls_back_to_mirror(self) -> None:
        from kinozal_scraper.kinozal_pipeline import Kinozal

        sentinel = unittest.mock.Mock()
        mirror_html = _details_html("Hidden objects")
        with (
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_html",
                side_effect=RuntimeError("HTTP Error 522"),
            ),
            unittest.mock.patch("kinozal_scraper.kinozal_pipeline.login", return_value=sentinel),
            unittest.mock.patch(
                "kinozal_scraper.kinozal_pipeline.fetch_authenticated", return_value=mirror_html
            ),
        ):
            html = Kinozal("u", "p").fetch_details("https://kinozal.tv/details.php?id=1")
        self.assertEqual(html, mirror_html)


if __name__ == "__main__":
    unittest.main()
