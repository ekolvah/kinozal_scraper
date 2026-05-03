import unittest

from generic_pipeline import PipelineResult, extract_from_html, extract_from_json

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


if __name__ == "__main__":
    unittest.main()
