from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

from sheets_storage import InMemoryStorage
from steam_pipeline import (
    _did_fail,
    run_steam_pipeline,
)
from telegram_notifier import InMemoryNotifier

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
        "url": None,
        "description": "short_description",
        "metric": "peak_in_game",
        "image_url": None,
    },
    "message_template": (
        "<b>{title}</b>\n{description}\nPeak players: {metric}\n"
        "Rank: {rank} (last week: {last_week_rank})\n"
        "https://store.steampowered.com/app/{appid}"
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


_APPLIST_INDEX: dict[str, str] = {
    "730": "Counter-Strike 2 (applist)",
    "578080": "PUBG: BATTLEGROUNDS (applist)",
    "570": "Dota 2 (applist)",
    "1962700": "Banana (applist)",
}


def _run(
    charts: dict[str, Any] | None = None,
    existing_keys: set[str] | None = None,
    sources_config: dict[str, Any] | None = None,
    appdetails: dict[int, dict[str, Any] | None] | None = None,
    applist_index: dict[str, str] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    if existing_keys:
        storage.seed_existing("steam_games", existing_keys)
    notifier = InMemoryNotifier()
    config = sources_config or _SOURCES_CONFIG
    appd_map = appdetails if appdetails is not None else _APPDETAILS
    name_index = applist_index if applist_index is not None else _APPLIST_INDEX

    def fake_appdetails(appid: int) -> dict[str, Any] | None:
        return appd_map.get(appid)

    with (
        unittest.mock.patch(
            "steam_pipeline._fetch_charts",
            return_value=charts if charts is not None else _CHARTS_RESPONSE,
        ),
        unittest.mock.patch(
            "steam_pipeline._fetch_appdetails",
            side_effect=fake_appdetails,
        ),
        unittest.mock.patch(
            "steam_pipeline._fetch_app_name_index",
            return_value=name_index,
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
        self.assertIn("https://store.steampowered.com/app/730", cs2.text)

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

    def test_appdetails_missing_falls_back_to_getapplist_name(self) -> None:
        """Primary fallback: appdetails 404 → use name from `GetAppList` index."""
        appd: dict[int, dict[str, Any] | None] = {
            730: None,
            578080: _APPDETAILS[578080],
            570: _APPDETAILS[570],
        }
        with self.assertLogs("steam_pipeline", level="WARNING") as caplog:
            storage, notifier = _run(appdetails=appd)
        ids = {n.id for n in notifier.sent}
        self.assertIn("730", ids)
        self.assertEqual(len(notifier.sent), 3)
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Counter-Strike 2 (applist)", cs2.text)
        self.assertIn("730", "\n".join(caplog.output))

    def test_appdetails_exception_falls_back_to_getapplist_name(self) -> None:
        """A raised exception from `_fetch_appdetails` must still fall through
        to the GetAppList index, not crash the run."""

        def boom(appid: int) -> dict[str, Any] | None:
            if appid == 730:
                raise RuntimeError("503 Service Unavailable")
            return _APPDETAILS.get(appid)

        with (
            unittest.mock.patch("steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE),
            unittest.mock.patch("steam_pipeline._fetch_appdetails", side_effect=boom),
            unittest.mock.patch(
                "steam_pipeline._fetch_app_name_index", return_value=_APPLIST_INDEX
            ),
        ):
            storage = InMemoryStorage()
            notifier = InMemoryNotifier()
            run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)

        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Counter-Strike 2 (applist)", cs2.text)

    def test_both_appdetails_and_getapplist_miss_uses_appid_placeholder(self) -> None:
        """Both lookups miss → render as 'Game #N' so the row still reaches
        Telegram as a visible anomaly."""
        appd: dict[int, dict[str, Any] | None] = {
            730: None,
            578080: _APPDETAILS[578080],
            570: _APPDETAILS[570],
        }
        with self.assertLogs("steam_pipeline", level="WARNING") as caplog:
            _, notifier = _run(appdetails=appd, applist_index={})
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Game #730", cs2.text)
        self.assertIn("no name anywhere", "\n".join(caplog.output))

    def test_getapplist_fetched_once_per_run(self) -> None:
        """Index is per-run, not per-item — verify single HTTP call regardless
        of item count."""
        calls: list[int] = []

        def tracked_applist() -> dict[str, str]:
            calls.append(1)
            return _APPLIST_INDEX

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch("steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE),
            unittest.mock.patch(
                "steam_pipeline._fetch_appdetails",
                side_effect=lambda appid: _APPDETAILS.get(appid),
            ),
            unittest.mock.patch(
                "steam_pipeline._fetch_app_name_index", side_effect=tracked_applist
            ),
        ):
            run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertEqual(len(calls), 1)

    def test_getapplist_fetch_failure_uses_appdetails_only(self) -> None:
        """If GetAppList itself errors, run continues — items with working
        appdetails still get sent; items without name get placeholder."""
        appd: dict[int, dict[str, Any] | None] = {
            730: None,
            578080: _APPDETAILS[578080],
            570: _APPDETAILS[570],
        }

        def failing_applist() -> dict[str, str]:
            raise RuntimeError("Steam API down")

        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch("steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE),
            unittest.mock.patch(
                "steam_pipeline._fetch_appdetails", side_effect=lambda appid: appd.get(appid)
            ),
            unittest.mock.patch(
                "steam_pipeline._fetch_app_name_index", side_effect=failing_applist
            ),
        ):
            run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        cs2 = next(n for n in notifier.sent if n.id == "730")
        self.assertIn("Game #730", cs2.text)


class TestVisibility(unittest.TestCase):
    def test_empty_ranks_marks_failure(self) -> None:
        _run(charts={"response": {"rollup_date": 0, "ranks": []}})
        self.assertTrue(_did_fail())

    def test_charts_fetch_exception_marks_failure(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch("steam_pipeline._fetch_charts", side_effect=RuntimeError("boom")):
            run_steam_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertTrue(_did_fail())

    def test_successful_run_does_not_mark_failure(self) -> None:
        _run()
        self.assertFalse(_did_fail())


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
            unittest.mock.patch("steam_pipeline._fetch_charts", return_value=_CHARTS_RESPONSE),
            unittest.mock.patch(
                "steam_pipeline._fetch_appdetails", side_effect=tracking_appdetails
            ),
            unittest.mock.patch(
                "steam_pipeline._fetch_app_name_index", return_value=_APPLIST_INDEX
            ),
        ):
            run_steam_pipeline(storage, notifier, sources_config=sources_config)
        self.assertEqual(calls, [730, 578080])


if __name__ == "__main__":
    unittest.main()
