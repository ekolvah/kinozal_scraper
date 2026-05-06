import unittest
from typing import Any

from generic_pipeline import ROW_HEADERS, build_notification, extract_from_html
from sheets_storage import InMemoryStorage
from telegram_notifier import InMemoryNotifier

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
    "message_template": "<b>{title}</b>\n{image_url}\n{url}",
}

_SOURCES_CONFIG: dict[str, Any] = {"version": 1, "sources": [_SOLDOUT_SOURCE]}


def _run(
    html: str = _SOLDOUT_HTML,
    existing_keys: set[str] | None = None,
    sources_config: dict[str, Any] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    if existing_keys:
        for key in existing_keys:
            storage._keys["events"].add(key)
    notifier = InMemoryNotifier()
    config = sources_config or _SOURCES_CONFIG

    event_sources = [
        s for s in config["sources"] if s.get("enabled") and s["id"].startswith("soldout_")
    ]
    for source in event_sources:
        result = extract_from_html(html, source)
        if not result.ok:
            continue
        sheet_tab: str = source["sheet_tab"]
        existing = storage.get_existing_keys(sheet_tab)
        new_items = [i for i in result.items if i.dedupe_key not in existing]
        if not new_items:
            continue
        storage.append_rows(sheet_tab, ROW_HEADERS, [i.to_row() for i in new_items])
        notifications = [build_notification(item, source["message_template"]) for item in new_items]
        notifier.send_items(notifications)

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
        from events_pipeline import run_events_pipeline

        config: dict[str, Any] = {
            "version": 1,
            "sources": [{**_SOLDOUT_SOURCE, "url": ""}],
        }
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        run_events_pipeline(storage, notifier, sources_config=config)
        self.assertEqual(storage.stored_rows("events"), [])
        self.assertEqual(notifier.sent, [])


if __name__ == "__main__":
    unittest.main()
