from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

from kinozal_scraper.gemini_enricher import QuotaExhausted
from kinozal_scraper.generic_pipeline import NormalizedItem, PipelineResult
from kinozal_scraper.sheets_storage import InMemoryStorage
from kinozal_scraper.steam_pipeline import run_steam_pipeline
from kinozal_scraper.telegram_notifier import InMemoryNotifier

_SOURCE: dict[str, Any] = {
    "id": "steam_charts_mostplayed",
    "enabled": True,
    "type": "steam_charts",
    "url": "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/",
    "limit": 3,
    "sheet_tab": "steam_games",
    "dedupe_key": "appid",
    "fields": {
        "title": "name",
        "url": "store_url",
        "description": "short_description",
        "metric": "peak_in_game",
        "image_url": None,
    },
    "message_template": (
        "<b>{title_link}</b>\n{description}\nPeak players: {metric}\n"
        "Rank: {rank} (last week: {last_week_rank})"
    ),
}

_SOURCES_CONFIG: dict[str, Any] = {"version": 1, "sources": [_SOURCE]}

_CHARTS_RESPONSE: dict[str, Any] = {
    "response": {
        "rollup_date": 1779062400,
        "ranks": [
            {"rank": 1, "appid": 730, "last_week_rank": 1, "peak_in_game": 1313208},
            {"rank": 2, "appid": 578080, "last_week_rank": 2, "peak_in_game": 769347},
            {"rank": 3, "appid": 570, "last_week_rank": 3, "peak_in_game": 559307},
            {"rank": 4, "appid": 1962700, "last_week_rank": -1, "peak_in_game": 213101},
        ],
    },
}

_APPDETAILS: dict[int, dict[str, Any] | None] = {
    730: {"name": "Counter-Strike 2", "short_description": "Free shooter from Valve"},
    578080: {"name": "PUBG: BATTLEGROUNDS", "short_description": "Battle royale"},
    570: {"name": "Dota 2", "short_description": "MOBA"},
    1962700: {"name": "Banana", "short_description": "Clicker"},
}


def _run(
    charts: dict[str, Any] | None = None,
    existing_keys: set[str] | None = None,
    sources_config: dict[str, Any] | None = None,
    appdetails: dict[int, dict[str, Any] | None] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    if existing_keys:
        storage.seed_existing("steam_games", existing_keys)
    notifier = InMemoryNotifier()
    config = sources_config or _SOURCES_CONFIG
    appd_map = appdetails if appdetails is not None else _APPDETAILS

    def fake_appdetails(appid: int) -> dict[str, Any] | None:
        return appd_map.get(appid)

    with (
        unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_charts",
            return_value=charts if charts is not None else _CHARTS_RESPONSE,
        ),
        unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_appdetails",
            side_effect=fake_appdetails,
        ),
    ):
        run_steam_pipeline(storage, notifier, sources_config=config)
    return storage, notifier


class TestExtraction(unittest.TestCase):
    def test_extracts_top_n_with_names(self) -> None:
        storage, notifier = _run()
        self.assertEqual(len(notifier.sent), 3)
        rows = storage.stored_rows("steam_games")
        self.assertEqual(len(rows), 3)
        # row schema (ROW_HEADERS): dedupe_key, title, url, metric, source_id, notified_at
        appids = [row[0] for row in rows]
        self.assertEqual(appids, ["730", "578080", "570"])
        names = [row[1] for row in rows]
        self.assertIn("Counter-Strike 2", names)
        self.assertIn("Dota 2", names)

    def test_metric_column_is_peak_in_game(self) -> None:
        storage, _ = _run()
        rows = storage.stored_rows("steam_games")
        metrics = {row[0]: row[3] for row in rows}
        self.assertEqual(metrics["730"], "1313208")
        self.assertEqual(metrics["570"], "559307")

    def test_source_id_recorded(self) -> None:
        storage, _ = _run()
        rows = storage.stored_rows("steam_games")
        for row in rows:
            self.assertEqual(row[4], "steam_charts_mostplayed")


class TestNotificationTemplate(unittest.TestCase):
    def test_renders_rank_peak_and_appid_link(self) -> None:
        _, notifier = _run()
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Counter-Strike 2", cs2.text)
        self.assertIn("1313208", cs2.text)
        self.assertIn("Rank: 1", cs2.text)
        self.assertIn(
            '<a href="https://store.steampowered.com/app/730">Counter-Strike 2</a>',
            cs2.text,
        )

    def test_no_standalone_url_line(self) -> None:
        """The store URL must appear only inside the title's anchor href,
        not as a separate trailing line that duplicates the title."""
        _, notifier = _run()
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertNotIn("\nhttps://store.steampowered.com/app/730", cs2.text)

    def test_new_entry_last_week_normalised(self) -> None:
        """`last_week_rank: -1` is the API's marker for new entries; the template
        must surface a friendlier label, never `-1`."""
        source = {**_SOURCE, "limit": 4}
        sources_config = {"version": 1, "sources": [source]}
        _, notifier = _run(sources_config=sources_config)
        new_entry = next(n for n in notifier.sent if n.id == "1962700")
        self.assertNotIn("-1", new_entry.text)


class TestDedup(unittest.TestCase):
    def test_skips_appid_already_in_tab(self) -> None:
        _, notifier = _run(existing_keys={"730"})
        sent_ids = {n.id for n in notifier.sent}
        self.assertNotIn("730", sent_ids)
        self.assertEqual(len(notifier.sent), 2)

    def test_no_new_items_no_send(self) -> None:
        _, notifier = _run(existing_keys={"730", "578080", "570"})
        self.assertEqual(notifier.sent, [])


class TestAppDetailsFailure(unittest.TestCase):
    """Decision 4 audit fix: degraded items must reach Telegram visibly, never
    silent-drop — see [[feedback_visibility_over_silence]]."""

    def test_appdetails_miss_renders_visible_marker(self) -> None:
        """appdetails miss → name carries a visible ⚠️ anomaly marker, not a
        silent 'Game #N' that looks like a real title (Principle IV). Item is
        still sent (no silent drop) and a WARNING is logged."""
        appd: dict[int, dict[str, Any] | None] = {
            730: None,
            578080: _APPDETAILS[578080],
            570: _APPDETAILS[570],
        }
        with self.assertLogs("kinozal_scraper.steam_pipeline", level="WARNING") as caplog:
            _, notifier = _run(appdetails=appd)
        self.assertEqual(len(notifier.sent), 3)
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("⚠️", cs2.text)
        self.assertIn("Game #730", cs2.text)
        self.assertIn("no name", "\n".join(caplog.output))

    def test_appdetails_exception_still_sends_visible_marker(self) -> None:
        """A raised exception from `_fetch_appdetails` must not crash the run —
        the item still ships with the ⚠️ placeholder."""

        def boom(appid: int) -> dict[str, Any] | None:
            if appid == 730:
                raise RuntimeError("503 Service Unavailable")
            return _APPDETAILS.get(appid)

        with (
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE
            ),
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_appdetails", side_effect=boom
            ),
        ):
            storage = InMemoryStorage()
            notifier = InMemoryNotifier()
            run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)

        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("⚠️", cs2.text)
        self.assertIn("Game #730", cs2.text)


class TestVisibility(unittest.TestCase):
    def test_empty_ranks_marks_failure(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_charts",
                return_value={"response": {"rollup_date": 0, "ranks": []}},
            ),
        ):
            results = run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertTrue(any(not r.ok for r in results))

    def test_charts_fetch_exception_marks_failure(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_charts", side_effect=RuntimeError("boom")
        ):
            results = run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertTrue(any(not r.ok for r in results))
        self.assertTrue(
            any("charts fetch failed" in err for r in results for err in r.errors),
            f"expected 'charts fetch failed' in errors, got: {[r.errors for r in results]}",
        )

    def test_successful_run_does_not_mark_failure(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()

        def fake_appdetails(appid: int) -> dict[str, Any] | None:
            return _APPDETAILS.get(appid)

        with (
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE
            ),
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_appdetails", side_effect=fake_appdetails
            ),
        ):
            results = run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual([r.source_id for r in results], ["steam_charts_mostplayed"])
        self.assertIsInstance(results[0], PipelineResult)


class TestLimit(unittest.TestCase):
    def test_only_fetches_top_n_appdetails(self) -> None:
        """Limit=2 must short-circuit appdetails calls — saves HTTP work."""
        source = {**_SOURCE, "limit": 2}
        sources_config = {"version": 1, "sources": [source]}
        calls: list[int] = []

        def tracking_appdetails(appid: int) -> dict[str, Any] | None:
            calls.append(appid)
            return _APPDETAILS.get(appid)

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE
            ),
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_appdetails", side_effect=tracking_appdetails
            ),
        ):
            run_steam_pipeline(storage, notifier, sources_config=sources_config)
        self.assertEqual(calls, [730, 578080])


_RU_SOURCE: dict[str, Any] = {
    **_SOURCE,
    "enrich": {
        "field": "description_ru",
        "prompt": "Переведи описание игры на русский язык: $description",
        "parameters": {"temperature": 0.2, "max_tokens": 300},
        "on_error": "",
    },
    "message_template": (
        "<b>{title_link}</b>\n{description_ru}\nPeak players: {metric}\n"
        "Rank: {rank} (last week: {last_week_rank})"
    ),
}


class _FakeTranslator:
    def __init__(self, translation: str) -> None:
        self._translation = translation
        self.calls: list[str] = []

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        self.calls.append(item.dedupe_key)
        return self._translation


class _QuotaAfterFirst:
    """Returns translation for the first item, raises QuotaExhausted on every
    subsequent call — to assert that remaining items still reach Telegram with
    English fallback (visibility-over-silence)."""

    def __init__(self, translation: str) -> None:
        self._translation = translation
        self._call_count = 0

    def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
        self._call_count += 1
        if self._call_count > 1:
            raise QuotaExhausted
        return self._translation


def _run_with_enricher(
    enricher: Any | None,
    sources_config: dict[str, Any] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    notifier = InMemoryNotifier()
    config = sources_config or {"version": 1, "sources": [_RU_SOURCE]}
    with (
        unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE
        ),
        unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_appdetails",
            side_effect=lambda appid: _APPDETAILS.get(appid),
        ),
    ):
        run_steam_pipeline(storage, notifier, sources_config=config, enricher=enricher)
    return storage, notifier


class TestSteamRussianDescription(unittest.TestCase):
    """Translation of Steam `short_description` to Russian via Enricher — #124."""

    def test_uses_translated_description_when_enricher_present(self) -> None:
        translator = _FakeTranslator("Бесплатный шутер от Valve")
        _, notifier = _run_with_enricher(translator)
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Бесплатный шутер от Valve", cs2.text)
        self.assertNotIn("Free shooter from Valve", cs2.text)
        self.assertEqual(len(translator.calls), 3)

    def test_falls_back_to_english_when_enricher_is_none(self) -> None:
        _, notifier = _run_with_enricher(enricher=None)
        self.assertEqual(len(notifier.sent), 3)
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Free shooter from Valve", cs2.text)
        pubg = next(n for n in notifier.sent if n.id == "578080")
        self.assertIn("Battle royale", pubg.text)

    def test_falls_back_to_english_on_quota_exhausted(self) -> None:
        translator = _QuotaAfterFirst("РУ перевод первого")
        _, notifier = _run_with_enricher(translator)
        self.assertEqual(
            len(notifier.sent), 3, "all items must reach Telegram even if quota dies mid-loop"
        )
        sent_by_id = {n.id: n.text for n in notifier.sent}
        self.assertIn("РУ перевод первого", sent_by_id["730"])
        self.assertIn("Battle royale", sent_by_id["578080"])
        self.assertIn("MOBA", sent_by_id["570"])

    def test_falls_back_to_english_on_marker_or_empty(self) -> None:
        """`FALLBACK_MARKER` (from `TruncatedResponse`) and empty string
        (from `on_error`) are distinct return paths — both must fall back to
        English. Claude-review #126 flagged this as an untested branch."""
        from kinozal_scraper.gemini_enricher import FALLBACK_MARKER

        class _MarkerThenEmpty:
            def __init__(self) -> None:
                self._call = 0

            def enrich(self, item: NormalizedItem, enrich_config: dict[str, Any]) -> str:
                self._call += 1
                if self._call == 1:
                    return FALLBACK_MARKER
                return ""

        with self.assertLogs("kinozal_scraper.steam_pipeline", level="WARNING") as caplog:
            _, notifier = _run_with_enricher(_MarkerThenEmpty())
        self.assertEqual(len(notifier.sent), 3)
        sent_by_id = {n.id: n.text for n in notifier.sent}
        self.assertIn("Free shooter from Valve", sent_by_id["730"])
        self.assertIn("Battle royale", sent_by_id["578080"])
        warnings = "\n".join(caplog.output)
        self.assertIn("730", warnings)
        self.assertIn("578080", warnings)


# ── delivery truthfulness (Principle III, issue #132) ─────────────────────────


def _run_results(
    fail_ids: set[str] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier, list[PipelineResult]]:
    """Invoke run_steam_pipeline directly with a controllable-failure notifier,
    returning the PipelineResult list for delivery-truthfulness assertions."""
    storage = InMemoryStorage()
    notifier = InMemoryNotifier(fail_ids=fail_ids)
    with (
        unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE
        ),
        unittest.mock.patch(
            "kinozal_scraper.steam_pipeline._fetch_appdetails",
            side_effect=lambda appid: _APPDETAILS.get(appid),
        ),
    ):
        results = run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
    return storage, notifier, results


class TestDeliveryTruthfulness(unittest.TestCase):
    """Only confirmed-delivered items may be persisted (Principle III). A failed
    Telegram send must mark result not-ok and leave the item unstored to retry."""

    def test_failed_notifications_excluded_from_storage(self) -> None:
        storage, notifier, _ = _run_results(fail_ids={"730"})
        stored_keys = {row[0] for row in storage.stored_rows("steam_games")}
        self.assertNotIn("730", stored_keys)
        self.assertEqual(stored_keys, {"578080", "570"})
        self.assertEqual({n.id for n in notifier.failed}, {"730"})

    def test_failed_notifications_mark_result_not_ok(self) -> None:
        _, _, results = _run_results(fail_ids={"730"})
        self.assertTrue(any(not r.ok for r in results))
        self.assertTrue(
            any(r.errors for r in results),
            f"expected delivery failure in errors, got: {[r.errors for r in results]}",
        )

    def test_all_failed_writes_nothing(self) -> None:
        storage, _, results = _run_results(fail_ids={"730", "578080", "570"})
        self.assertEqual(storage.stored_rows("steam_games"), [])
        self.assertTrue(any(not r.ok for r in results))

    def test_all_sent_writes_all_rows(self) -> None:
        storage, notifier, results = _run_results()
        self.assertEqual(len(storage.stored_rows("steam_games")), 3)
        self.assertEqual(len(notifier.sent), 3)
        self.assertTrue(all(r.ok for r in results))


class TestSourceIsolation(unittest.TestCase):
    """An *unhandled* error in one source must not abort the whole run — it
    yields a not-ok PipelineResult and the next source still ships. Mirrors
    github_popular_pipeline's top-level isolation (test_one_source_error_does_not_block_others).
    The error is raised in a stage with no per-stage guard (dedup), so only the
    top-level wrapper can contain it."""

    def test_unhandled_error_isolated_per_source(self) -> None:
        broken = {**_SOURCE, "id": "steam_broken", "sheet_tab": "broken"}
        config = {"version": 1, "sources": [broken, _SOURCE]}

        class _FlakyStorage(InMemoryStorage):
            def get_existing_keys(self, tab_name: str) -> set[str]:
                if tab_name == "broken":
                    raise RuntimeError("sheets API down")
                return super().get_existing_keys(tab_name)

        storage = _FlakyStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE
            ),
            unittest.mock.patch(
                "kinozal_scraper.steam_pipeline._fetch_appdetails",
                side_effect=lambda appid: _APPDETAILS.get(appid),
            ),
        ):
            results = run_steam_pipeline(storage, notifier, sources_config=config)

        broken_result = next(r for r in results if r.source_id == "steam_broken")
        self.assertFalse(broken_result.ok)
        self.assertTrue(any("unhandled error" in e for e in broken_result.errors))
        # the second source still delivered its 3 items despite the first erroring
        self.assertEqual(len(notifier.sent), 3)


if __name__ == "__main__":
    unittest.main()
