import unittest
import unittest.mock
from typing import Any

from kinozal_scraper.events_pipeline import run_events_pipeline
from kinozal_scraper.generic_pipeline import PipelineResult, extract_from_html
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import InMemoryStorage
from kinozal_scraper.telegram_notifier import InMemoryNotifier

_SOLDOUT_HTML = """
<html><body>
<div class="homeBoxEvent">
  <div class="homeBoxEventTop">
    <a href="/easyconsole.cfm/page/details/id/1">Event One</a>
    <h2><a href="/easyconsole.cfm/page/details/id/1">Event One</a></h2>
    <img class="imgEvent" src="/img/event1.jpg">
  </div>
</div>
<div class="homeBoxEvent">
  <div class="homeBoxEventTop">
    <a href="/easyconsole.cfm/page/details/id/2">Event Two</a>
    <h2><a href="/easyconsole.cfm/page/details/id/2">Event Two</a></h2>
    <img class="imgEvent" src="https://cdn.example.com/event2.jpg">
  </div>
</div>
</body></html>
"""

_SOLDOUT_SOURCE: dict[str, Any] = {
    "id": "soldout_events",
    "enabled": True,
    "type": "html",
    "url": "https://www.soldoutticketbox.com/easyconsole.cfm/page/category/cat_id/17/lang/ru",
    "base_url": "https://www.soldoutticketbox.com",
    "params": {},
    "row_selector": "div.homeBoxEvent",
    "limit": 20,
    "sheet_tab": "events",
    "dedupe_key": "h2 a",
    "fields": {
        "title": "h2 a",
        "url": ".homeBoxEventTop a@href",
        "description": None,
        "metric": None,
        "image_url": ".imgEvent@src",
    },
    "message_template": "<b>{title_link}</b>",
}

_SOURCES_CONFIG: dict[str, Any] = {"version": 1, "sources": [_SOLDOUT_SOURCE]}


def _run(
    html: str = _SOLDOUT_HTML,
    existing_keys: set[str] | None = None,
    sources_config: dict[str, Any] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    """Run the real run_events_pipeline with HTTP patched.

    Invokes production code directly so tests fail if pipeline behaviour drifts.
    """
    storage = InMemoryStorage()
    if existing_keys:
        storage.seed_existing("events", existing_keys)
    notifier = InMemoryNotifier()
    config = sources_config or _SOURCES_CONFIG

    with unittest.mock.patch("kinozal_scraper.events_pipeline.fetch_html", return_value=html):
        run_events_pipeline(storage, notifier, sources_config=config)

    return storage, notifier


# ── extraction ────────────────────────────────────────────────────────────────


class TestSoldoutExtraction(unittest.TestCase):
    def _extract(self, html: str = _SOLDOUT_HTML) -> list[Any]:
        result = extract_from_html(html, _SOLDOUT_SOURCE)
        self.assertTrue(result.ok, result.errors)
        return result.items

    def test_extracts_two_items(self) -> None:
        items = self._extract()
        self.assertEqual(len(items), 2)

    def test_title_extracted(self) -> None:
        items = self._extract()
        self.assertEqual(items[0].title, "Event One")

    def test_relative_url_prefixed_with_base_url(self) -> None:
        items = self._extract()
        self.assertTrue(
            items[0].url.startswith("https://www.soldoutticketbox.com"),
            items[0].url,
        )

    def test_relative_image_url_prefixed(self) -> None:
        items = self._extract()
        self.assertTrue(
            items[0].image_url.startswith("https://www.soldoutticketbox.com"),
            items[0].image_url,
        )

    def test_absolute_image_url_passthrough(self) -> None:
        items = self._extract()
        self.assertEqual(items[1].image_url, "https://cdn.example.com/event2.jpg")

    def test_dedupe_key_is_title(self) -> None:
        items = self._extract()
        self.assertEqual(items[0].dedupe_key, "Event One")


# ── transport (issue #217) ────────────────────────────────────────────────────


class TestEventsFetchTransport(unittest.TestCase):
    """The pipeline must fetch through the shared http_fetch.fetch_html helper
    (curl_cffi + impersonate), not a local requests wrapper — issue #217."""

    def test_pipeline_uses_shared_fetch_html(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.events_pipeline.fetch_html", return_value=_SOLDOUT_HTML
        ) as mfetch:
            run_events_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        mfetch.assert_called_once()


# ── pipeline deduplication ────────────────────────────────────────────────────


class TestEventsPipelineDeduplication(unittest.TestCase):
    def test_new_items_stored_and_notified(self) -> None:
        storage, notifier = _run()
        self.assertEqual(len(storage.stored_rows("events")), 2)
        self.assertEqual(len(notifier.sent), 2)

    def test_already_existing_item_not_re_notified(self) -> None:
        storage, notifier = _run(existing_keys={"Event One"})
        self.assertEqual(len(storage.stored_rows("events")), 1)
        self.assertEqual(notifier.sent[0].id, "Event Two")

    def test_all_existing_no_notifications(self) -> None:
        storage, notifier = _run(existing_keys={"Event One", "Event Two"})
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertEqual(notifier.sent, [])


# ── notification content ──────────────────────────────────────────────────────


class TestEventsPipelineNotificationContent(unittest.TestCase):
    def test_title_in_notification(self) -> None:
        _, notifier = _run()
        self.assertIn("Event One", notifier.sent[0].text)

    def test_image_url_on_notification(self) -> None:
        _, notifier = _run()
        self.assertTrue(notifier.sent[0].image_url.startswith("https://"))

    def test_url_in_notification_text(self) -> None:
        _, notifier = _run()
        self.assertIn("soldoutticketbox.com", notifier.sent[0].text)

    def test_title_is_clickable_bold_hyperlink(self) -> None:
        # #229: title must render as a bold clickable anchor like every other
        # source (<b>{title_link}</b>), not a bare bold string + raw url line.
        # assertNotIn("\n") pins acceptance #2 — the old template put the raw url
        # on its own line; the fixed one is a single clickable title.
        _, notifier = _run()
        text = notifier.sent[0].text
        self.assertIn('<b><a href="', text)
        self.assertIn(">Event One</a>", text)
        self.assertNotIn("\n", text)


class TestEventsRealConfig(unittest.TestCase):
    """#229 real-config guard: exercise the PRODUCTION sources.json, not the
    fixture copy. The two tests above run through _SOLDOUT_SOURCE, whose template
    this PR also edits — so they'd stay green even if the sources.json edit were
    botched. This guard loads load_sources_config() (mirrors the #173 pattern in
    test_kinozal_pipeline.py) so a wrong/leftover placeholder in the real artifact
    reddens CI."""

    def test_real_sources_renders_hyperlink_no_raw_url(self) -> None:
        # SOLDOUT_URL must be set or {{SOLDOUT_URL}} → "" and the source is skipped
        # (see test_missing_url_skips_source). The fetch itself is patched in _run.
        with unittest.mock.patch.dict(
            "os.environ", {"SOLDOUT_URL": "https://www.soldoutticketbox.com/events"}
        ):
            config = load_sources_config()
        _, notifier = _run(sources_config=config)
        self.assertTrue(notifier.sent, "no events notification produced from real config")
        text = notifier.sent[0].text
        self.assertIn('<b><a href="', text)
        self.assertNotIn("\n", text)


# ── edge cases ────────────────────────────────────────────────────────────────


class TestEventsPipelineEdgeCases(unittest.TestCase):
    def test_empty_html_no_crash(self) -> None:
        storage, notifier = _run(html="<html></html>")
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertEqual(notifier.sent, [])

    def test_no_enabled_sources_does_nothing(self) -> None:
        config: dict[str, Any] = {
            "version": 1,
            "sources": [{**_SOLDOUT_SOURCE, "enabled": False}],
        }
        storage, notifier = _run(sources_config=config)
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertEqual(notifier.sent, [])

    def test_missing_url_skips_source(self) -> None:
        config: dict[str, Any] = {
            "version": 1,
            "sources": [{**_SOLDOUT_SOURCE, "url": ""}],
        }
        storage, notifier = _run(sources_config=config)
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertEqual(notifier.sent, [])

    def test_fetch_failure_isolated_pipeline_continues(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.events_pipeline.fetch_html", side_effect=RuntimeError("boom")
        ):
            run_events_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertEqual(notifier.sent, [])


# ── exit-code surface (issue #97) ─────────────────────────────────────────────


class TestEventsPipelineExitCodeSurface(unittest.TestCase):
    """The runner must return list[PipelineResult] so __main__ can sys.exit(1)
    on any failed source. Previously fetch errors were silent — see issue #97."""

    def test_fetch_failure_returns_not_ok_result(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.events_pipeline.fetch_html", side_effect=RuntimeError("boom")
        ):
            results = run_events_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], PipelineResult)
        self.assertFalse(results[0].ok)
        self.assertTrue(
            any("fetch failed" in err for err in results[0].errors),
            f"expected 'fetch failed' in errors, got: {results[0].errors}",
        )

    def test_successful_run_returns_all_ok_results(self) -> None:
        storage, notifier = _run()
        # Re-invoke directly to capture the return value (helper discards it).
        storage2 = InMemoryStorage()
        notifier2 = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.events_pipeline.fetch_html", return_value=_SOLDOUT_HTML
        ):
            results = run_events_pipeline(storage2, notifier2, sources_config=_SOURCES_CONFIG)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual([r.source_id for r in results], ["soldout_events"])


# ── delivery truthfulness (Principle III, issue #132) ─────────────────────────


def _run_results(
    fail_ids: set[str] | None = None,
    html: str = _SOLDOUT_HTML,
) -> tuple[InMemoryStorage, InMemoryNotifier, list[PipelineResult]]:
    """Invoke run_events_pipeline directly with a controllable-failure notifier,
    returning the PipelineResult list for delivery-truthfulness assertions."""
    storage = InMemoryStorage()
    notifier = InMemoryNotifier(fail_ids=fail_ids)
    with unittest.mock.patch("kinozal_scraper.events_pipeline.fetch_html", return_value=html):
        results = run_events_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)
    return storage, notifier, results


class TestDeliveryTruthfulness(unittest.TestCase):
    """Only confirmed-delivered items may be persisted (Principle III). A failed
    Telegram send must mark result not-ok and leave the item unstored to retry."""

    def test_failed_notifications_excluded_from_storage(self) -> None:
        storage, notifier, _ = _run_results(fail_ids={"Event One"})
        stored_keys = {row[0] for row in storage.stored_rows("events")}
        self.assertEqual(stored_keys, {"Event Two"})
        self.assertEqual({n.id for n in notifier.sent}, {"Event Two"})
        self.assertEqual({n.id for n in notifier.failed}, {"Event One"})

    def test_failed_notifications_mark_result_not_ok(self) -> None:
        _, _, results = _run_results(fail_ids={"Event One"})
        self.assertTrue(any(not r.ok for r in results))
        self.assertTrue(
            any(r.errors for r in results),
            f"expected delivery failure in errors, got: {[r.errors for r in results]}",
        )

    def test_all_failed_writes_nothing(self) -> None:
        storage, _, results = _run_results(fail_ids={"Event One", "Event Two"})
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertTrue(any(not r.ok for r in results))

    def test_all_sent_writes_all_rows(self) -> None:
        storage, notifier, results = _run_results()
        self.assertEqual(len(storage.stored_rows("events")), 2)
        self.assertEqual(len(notifier.sent), 2)
        self.assertTrue(all(r.ok for r in results))


if __name__ == "__main__":
    unittest.main()
