import unittest

from kinozal_scraper.generic_pipeline import (
    NormalizedItem,
    PipelineResult,
    _selector_css_part,
    build_notification,
    extract_from_html,
    extract_from_json,
)

_JSON_CONFIG = {
    "id": "test_src",
    "type": "json",
    "limit": 10,
    "dedupe_key": "id",
    "fields": {
        "title": "name",
        "url": "link",
        "description": "desc",
        "metric": "score",
        "image_url": None,
    },
}

_HTML_CONFIG = {
    "id": "test_html",
    "type": "html",
    "limit": 10,
    "row_selector": "tr.item",
    "dedupe_key": "td.key",
    "fields": {
        "title": "td.title",
        "url": "a@href",
        "description": "td.desc",
        "metric": None,
        "image_url": None,
    },
}

_MINIMAL_HTML = """
<table>
  <tr class="item">
    <td class="key">k1</td>
    <td class="title">Film One</td>
    <td class="desc">Action</td>
    <td><a href="https://example.com/1">link</a></td>
  </tr>
  <tr class="item">
    <td class="key">k2</td>
    <td class="title">Film Two</td>
    <td class="desc">Drama</td>
    <td><a href="https://example.com/2">link</a></td>
  </tr>
</table>
"""


class TestExtractFromJson(unittest.TestCase):
    def _records(self, n: int = 2) -> list[dict]:
        return [
            {
                "id": str(i),
                "name": f"Item {i}",
                "link": f"https://x.com/{i}",
                "desc": "d",
                "score": str(i * 10),
            }
            for i in range(1, n + 1)
        ]

    def test_basic_extraction(self) -> None:
        result = extract_from_json(self._records(), _JSON_CONFIG)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0].dedupe_key, "1")
        self.assertEqual(result.items[0].title, "Item 1")
        self.assertEqual(result.items[0].url, "https://x.com/1")
        self.assertEqual(result.items[0].metric, "10")
        self.assertEqual(result.items[0].source_id, "test_src")

    def test_optional_fields_absent(self) -> None:
        records = [{"id": "1", "name": "Only Required"}]
        result = extract_from_json(records, _JSON_CONFIG)
        self.assertTrue(result.ok)
        self.assertEqual(result.items[0].url, "")
        self.assertEqual(result.items[0].description, "")
        self.assertEqual(result.items[0].image_url, "")

    def test_limit_applied(self) -> None:
        config = {**_JSON_CONFIG, "limit": 1}
        result = extract_from_json(self._records(5), config)
        self.assertEqual(len(result.items), 1)

    def test_dedupe_key_stripped(self) -> None:
        records = [{"id": "  spaced  ", "name": "Title"}]
        result = extract_from_json(records, _JSON_CONFIG)
        self.assertEqual(result.items[0].dedupe_key, "spaced")

    def test_missing_dedupe_key_goes_to_errors(self) -> None:
        records = [{"name": "No Key"}]
        result = extract_from_json(records, _JSON_CONFIG)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.items), 0)
        self.assertTrue(any("dedupe_key" in e for e in result.errors))

    def test_missing_title_goes_to_errors(self) -> None:
        records = [{"id": "1"}]
        result = extract_from_json(records, _JSON_CONFIG)
        self.assertFalse(result.ok)
        self.assertTrue(any("title" in e for e in result.errors))

    def test_zero_records_quality_failure(self) -> None:
        result = extract_from_json([], _JSON_CONFIG)
        self.assertFalse(result.ok)
        self.assertTrue(any("zero items" in e for e in result.errors))

    def test_mostly_invalid_records(self) -> None:
        records = [{"name": "no key"} for _ in range(4)] + [{"id": "ok", "name": "Good"}]
        result = extract_from_json(records, _JSON_CONFIG)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(result.errors), 4)

    def test_raw_preserved(self) -> None:
        records = [{"id": "1", "name": "T", "extra": "data"}]
        result = extract_from_json(records, _JSON_CONFIG)
        self.assertEqual(result.items[0].raw["extra"], "data")


class TestExtractFromHtml(unittest.TestCase):
    def test_basic_extraction(self) -> None:
        result = extract_from_html(_MINIMAL_HTML, _HTML_CONFIG)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0].dedupe_key, "k1")
        self.assertEqual(result.items[0].title, "Film One")
        self.assertEqual(result.items[0].description, "Action")
        self.assertEqual(result.items[0].url, "https://example.com/1")

    def test_limit_applied(self) -> None:
        config = {**_HTML_CONFIG, "limit": 1}
        result = extract_from_html(_MINIMAL_HTML, config)
        self.assertEqual(len(result.items), 1)

    def test_limit_ge_rows_extracts_all(self) -> None:
        # Boundary contract: a limit >= the row count does not truncate — the
        # whole page is extracted. Guards the #173 fix (limit raised to the
        # page size) against a future off-by-one that silently drops rows.
        config = {**_HTML_CONFIG, "limit": 50}
        result = extract_from_html(_MINIMAL_HTML, config)  # _MINIMAL_HTML has 2 rows
        self.assertEqual(len(result.items), 2)

    def test_optional_field_absent(self) -> None:
        result = extract_from_html(_MINIMAL_HTML, _HTML_CONFIG)
        self.assertEqual(result.items[0].metric, "")
        self.assertEqual(result.items[0].image_url, "")

    def test_missing_row_selector_is_error(self) -> None:
        config = {**_HTML_CONFIG, "row_selector": ""}
        result = extract_from_html(_MINIMAL_HTML, config)
        self.assertFalse(result.ok)
        self.assertTrue(any("row_selector" in e for e in result.errors))

    def test_empty_html_quality_failure(self) -> None:
        result = extract_from_html("<html></html>", _HTML_CONFIG)
        self.assertFalse(result.ok)
        self.assertTrue(any("zero items" in e for e in result.errors))

    def test_attr_extraction(self) -> None:
        html = '<table><tr class="item"><td class="key">k</td><td class="title">T</td><td><a href="https://x.com">x</a></td></tr></table>'
        result = extract_from_html(html, _HTML_CONFIG)
        self.assertEqual(result.items[0].url, "https://x.com")


class TestPipelineResult(unittest.TestCase):
    def test_ok_true_when_no_errors(self) -> None:
        r = PipelineResult(source_id="s")
        self.assertTrue(r.ok)

    def test_ok_false_when_errors(self) -> None:
        r = PipelineResult(source_id="s", errors=["bad"])
        self.assertFalse(r.ok)


class TestBuildNotificationRawFallback(unittest.TestCase):
    def test_raw_field_resolved_in_template(self) -> None:
        item = NormalizedItem(
            dedupe_key="user/repo",
            title="user/repo",
            source_id="test",
            url="https://github.com/user/repo",
            metric="42",
            raw={"language": "Python", "forks": 10},
        )
        note = build_notification(item, "<b>{title}</b> | {language} | {forks}")
        self.assertIn("Python", note.text)
        self.assertIn("10", note.text)

    def test_missing_raw_key_resolves_to_empty(self) -> None:
        item = NormalizedItem(dedupe_key="x", title="x", source_id="t", raw={})
        note = build_notification(item, "{title} | {nonexistent}")
        self.assertIn("x |", note.text)
        self.assertNotIn("nonexistent", note.text)

    def test_none_raw_value_resolves_to_empty(self) -> None:
        item = NormalizedItem(dedupe_key="x", title="x", source_id="t", raw={"language": None})
        note = build_notification(item, "{title} | {language}")
        self.assertNotIn("None", note.text)


class TestBuildNotificationNewlineCollapse(unittest.TestCase):
    def test_empty_field_does_not_leave_double_newline(self) -> None:
        item = NormalizedItem(
            dedupe_key="user/repo",
            title="user/repo",
            source_id="test",
            url="https://github.com/user/repo",
            metric="42",
            raw={"summary_ru": "", "language": "Go"},
        )
        note = build_notification(
            item, "<b>{title}</b>\n{summary_ru}\n⭐ {metric} | {language}\n{url}"
        )
        self.assertNotIn("\n\n", note.text)
        self.assertIn("⭐ 42 | Go", note.text)

    def test_filled_field_preserves_single_newlines(self) -> None:
        item = NormalizedItem(
            dedupe_key="user/repo",
            title="user/repo",
            source_id="test",
            url="https://github.com/user/repo",
            metric="42",
            raw={"summary_ru": "Крутой проект", "language": "Go"},
        )
        note = build_notification(
            item, "<b>{title}</b>\n{summary_ru}\n⭐ {metric} | {language}\n{url}"
        )
        self.assertIn("Крутой проект\n⭐ 42", note.text)


class TestBuildNotificationLinks(unittest.TestCase):
    def _item(self, title: str, url: str = "", trailer_url: str = "") -> NormalizedItem:
        return NormalizedItem(
            dedupe_key=title,
            title=title,
            source_id="test",
            url=url,
            trailer_url=trailer_url,
        )

    def test_title_link_is_clickable_anchor(self) -> None:
        item = self._item("Фильм / 2026", url="https://kinozal.tv/details.php?id=1")
        note = build_notification(item, "{title_link}")
        self.assertIn('<a href="https://kinozal.tv/details.php?id=1">', note.text)
        self.assertIn("Фильм / 2026", note.text)

    def test_title_link_escapes_special_chars_in_title(self) -> None:
        item = self._item("Film <2026>", url="https://example.com/")
        note = build_notification(item, "{title_link}")
        self.assertIn("&lt;2026&gt;", note.text)
        self.assertNotIn("<2026>", note.text)

    def test_title_link_escapes_href(self) -> None:
        item = self._item("Film", url='https://example.com/?a="bad"')
        note = build_notification(item, "{title_link}")
        self.assertNotIn('"bad"', note.text)

    def test_title_link_without_url_is_plain_escaped_title(self) -> None:
        item = self._item("Film <X>", url="")
        note = build_notification(item, "{title_link}")
        self.assertNotIn("<a href", note.text)
        self.assertIn("Film &lt;X&gt;", note.text)

    def test_trailer_link_is_clickable_trailer_word(self) -> None:
        item = self._item("Film", trailer_url="https://www.youtube.com/watch?v=abc")
        note = build_notification(item, "{trailer_link}")
        self.assertIn('<a href="https://www.youtube.com/watch?v=abc">Trailer</a>', note.text)

    def test_trailer_link_empty_when_no_trailer(self) -> None:
        item = self._item("Film", trailer_url="")
        note = build_notification(item, "{trailer_link}")
        self.assertEqual(note.text, "")

    def test_kinozal_template(self) -> None:
        item = self._item(
            "Фильм / 2026 / WEB",
            url="https://kinozal.tv/details.php?id=1",
            trailer_url="https://www.youtube.com/watch?v=xyz",
        )
        note = build_notification(item, "{title_link}\n{trailer_link}")
        self.assertIn('<a href="https://kinozal.tv/details.php?id=1">', note.text)
        self.assertIn('<a href="https://www.youtube.com/watch?v=xyz">Trailer</a>', note.text)


class TestSelectorCssPart(unittest.TestCase):
    """Contract of the shared `field_selector → css part` extraction reused by
    both `_html_field` (runtime) and `validate_sources_config` (load-time)."""

    def test_css_with_attr(self) -> None:
        self.assertEqual(_selector_css_part("h2 a@href"), "h2 a")

    def test_attr_only_has_no_css(self) -> None:
        self.assertFalse(_selector_css_part("@href"))

    def test_plain_css_passthrough(self) -> None:
        self.assertEqual(_selector_css_part("p"), "p")

    def test_broken_at_keeps_css_part(self) -> None:
        self.assertEqual(_selector_css_part("div[bad@href"), "div[bad")

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_selector_css_part(None))


if __name__ == "__main__":
    unittest.main()
