import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import gspread
import gspread.exceptions
import requests

from kinozal_scraper.generic_pipeline import ROW_HEADERS, NormalizedItem
from kinozal_scraper.sheets_storage import InMemoryStorage, SchemaError, SheetsStorage, Storage


def _api_error(code: int, message: str) -> gspread.exceptions.APIError:
    """Build a gspread APIError without hitting the network."""
    resp = MagicMock(spec=requests.Response)
    resp.json.return_value = {"error": {"code": code, "message": message}}
    return gspread.exceptions.APIError(resp)


def _validate_schema(headers: list[str], tab_name: str = "tab") -> None:
    """Pure extraction of SheetsStorage schema validation logic — testable without gspread."""
    missing = set(ROW_HEADERS) - set(headers)
    if missing:
        raise SchemaError(f"Tab '{tab_name}' is missing columns: {sorted(missing)}.")


def _item(dedupe_key: str = "k1", source_id: str = "src") -> NormalizedItem:
    return NormalizedItem(
        dedupe_key=dedupe_key,
        title="Title",
        source_id=source_id,
        url="https://example.com",
        metric="42",
    )


class TestToRow(unittest.TestCase):
    def test_row_length_matches_headers(self) -> None:
        row = _item().to_row()
        self.assertEqual(len(row), len(ROW_HEADERS))

    def test_row_fields_order(self) -> None:
        item = _item(dedupe_key="dk")
        row = item.to_row()
        self.assertEqual(row[ROW_HEADERS.index("dedupe_key")], "dk")
        self.assertEqual(row[ROW_HEADERS.index("title")], "Title")
        self.assertEqual(row[ROW_HEADERS.index("url")], "https://example.com")
        self.assertEqual(row[ROW_HEADERS.index("metric")], "42")
        self.assertEqual(row[ROW_HEADERS.index("source_id")], "src")

    def test_notified_at_injected(self) -> None:
        ts = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
        row = _item().to_row(notified_at=ts)
        self.assertIn("2024-03-15", row[ROW_HEADERS.index("notified_at")])

    def test_notified_at_defaults_to_now(self) -> None:
        row = _item().to_row()
        notified_at = row[ROW_HEADERS.index("notified_at")]
        self.assertIsInstance(notified_at, str)
        self.assertTrue(notified_at)


class TestInMemoryStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = InMemoryStorage()

    def test_implements_storage_protocol(self) -> None:
        self.assertIsInstance(self.storage, Storage)

    def test_empty_tab_returns_empty_set(self) -> None:
        self.assertEqual(self.storage.get_existing_keys("movies"), set())

    def test_append_then_get_keys(self) -> None:
        rows = [_item("k1").to_row(), _item("k2").to_row()]
        self.storage.append_rows("movies", ROW_HEADERS, rows)
        keys = self.storage.get_existing_keys("movies")
        self.assertIn("k1", keys)
        self.assertIn("k2", keys)

    def test_existing_key_lookup(self) -> None:
        self.storage.append_rows("movies", ROW_HEADERS, [_item("existing").to_row()])
        self.assertIn("existing", self.storage.get_existing_keys("movies"))
        self.assertNotIn("new", self.storage.get_existing_keys("movies"))

    def test_tabs_are_isolated(self) -> None:
        self.storage.append_rows("tab_a", ROW_HEADERS, [_item("k1").to_row()])
        self.storage.append_rows("tab_b", ROW_HEADERS, [_item("k2").to_row()])
        self.assertNotIn("k2", self.storage.get_existing_keys("tab_a"))
        self.assertNotIn("k1", self.storage.get_existing_keys("tab_b"))

    def test_append_empty_rows_noop(self) -> None:
        self.storage.append_rows("movies", ROW_HEADERS, [])
        self.assertEqual(self.storage.stored_rows("movies"), [])

    def test_stored_rows_accessible(self) -> None:
        row = _item("k1").to_row()
        self.storage.append_rows("movies", ROW_HEADERS, [row])
        self.assertEqual(self.storage.stored_rows("movies"), [row])

    def test_multiple_appends_accumulate(self) -> None:
        self.storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.storage.append_rows("movies", ROW_HEADERS, [_item("k2").to_row()])
        self.assertEqual(len(self.storage.stored_rows("movies")), 2)

    def test_seed_existing_populates_keys_without_rows(self) -> None:
        self.storage.seed_existing("movies", ["pre-existing-1", "pre-existing-2"])
        self.assertEqual(
            self.storage.get_existing_keys("movies"),
            {"pre-existing-1", "pre-existing-2"},
        )
        # seed_existing does not append rows — only the dedupe set is touched
        self.assertEqual(self.storage.stored_rows("movies"), [])


class TestSheetsStorageRetryTransient(unittest.TestCase):
    """Every SheetsStorage network call retries on transient Sheets errors
    (429 quota + 5xx 500/502/503/504) with exponential backoff, via a single
    per-call retry layer. Non-transient 4xx (permission/not-found) fail fast.
    """

    def _build(self, side_effect: list[Any]) -> tuple[SheetsStorage, MagicMock]:
        """Storage whose worksheet().append_rows raises the given side_effect."""
        client = MagicMock(spec=gspread.Client)
        worksheet = MagicMock()
        worksheet.append_rows.side_effect = side_effect
        client.open_by_url.return_value.worksheet.return_value = worksheet
        return SheetsStorage(client, "https://sheets.example/url"), worksheet

    def _storage_with_spreadsheet(self) -> tuple[SheetsStorage, MagicMock]:
        """Storage constructed cleanly; returns (storage, spreadsheet mock) so a
        test can drive worksheet()/row_values() seams directly."""
        spreadsheet = MagicMock()
        client = MagicMock(spec=gspread.Client)
        client.open_by_url.return_value = spreadsheet
        return SheetsStorage(client, "https://sheets.example/url"), spreadsheet

    # --- 429 kept green: broadening must not break the original quota retry ---

    @patch("tenacity.nap.time.sleep")
    def test_429_then_success_retries_and_succeeds(self, _sleep: MagicMock) -> None:
        storage, worksheet = self._build([_api_error(429, "Quota exceeded"), None])
        storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.assertEqual(worksheet.append_rows.call_count, 2)

    @patch("tenacity.nap.time.sleep")
    def test_429_repeated_eventually_raises_after_5_attempts(self, _sleep: MagicMock) -> None:
        storage, worksheet = self._build([_api_error(429, "Quota exceeded")] * 5)
        with self.assertRaises(gspread.exceptions.APIError) as ctx:
            storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.assertEqual(ctx.exception.code, 429)
        self.assertEqual(worksheet.append_rows.call_count, 5)

    # --- 5xx on write ---

    @patch("tenacity.nap.time.sleep")
    def test_503_then_success_retries_on_append(self, _sleep: MagicMock) -> None:
        storage, worksheet = self._build([_api_error(503, "unavailable"), None])
        storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.assertEqual(worksheet.append_rows.call_count, 2)

    @patch("tenacity.nap.time.sleep")
    def test_500_then_success_retries_on_append(self, _sleep: MagicMock) -> None:
        storage, worksheet = self._build([_api_error(500, "internal"), None])
        storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.assertEqual(worksheet.append_rows.call_count, 2)

    @patch("tenacity.nap.time.sleep")
    def test_append_5xx_retries_exactly_5_attempts(self, _sleep: MagicMock) -> None:
        # Single retry layer: 5 attempts, NOT 5x5=25 from nested @retry.
        storage, worksheet = self._build([_api_error(503, "unavailable")] * 5)
        with self.assertRaises(gspread.exceptions.APIError) as ctx:
            storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(worksheet.append_rows.call_count, 5)

    # --- 5xx on construction (the actual crash site) ---

    @patch("tenacity.nap.time.sleep")
    def test_open_by_url_503_then_success_retries(self, _sleep: MagicMock) -> None:
        client = MagicMock(spec=gspread.Client)
        client.open_by_url.side_effect = [_api_error(503, "unavailable"), MagicMock()]
        SheetsStorage(client, "https://sheets.example/url")  # must not raise
        self.assertEqual(client.open_by_url.call_count, 2)

    # --- 5xx on worksheet lookup (shared seam — covers read AND append paths) ---

    @patch("tenacity.nap.time.sleep")
    def test_worksheet_lookup_503_then_success_retries(self, _sleep: MagicMock) -> None:
        storage, spreadsheet = self._storage_with_spreadsheet()
        ws = MagicMock()
        ws.row_values.return_value = list(ROW_HEADERS)
        ws.col_values.return_value = ["dedupe_key", "k1"]
        spreadsheet.worksheet.side_effect = [_api_error(503, "unavailable"), ws]
        keys = storage.get_existing_keys("movies")
        self.assertEqual(spreadsheet.worksheet.call_count, 2)
        self.assertIn("k1", keys)

    # --- 5xx on read ---

    @patch("tenacity.nap.time.sleep")
    def test_get_existing_keys_502_then_success_retries(self, _sleep: MagicMock) -> None:
        storage, spreadsheet = self._storage_with_spreadsheet()
        ws = MagicMock()
        ws.row_values.side_effect = [_api_error(502, "bad gateway"), list(ROW_HEADERS)]
        ws.col_values.return_value = ["dedupe_key", "k1"]
        spreadsheet.worksheet.return_value = ws
        keys = storage.get_existing_keys("movies")
        self.assertEqual(ws.row_values.call_count, 2)
        self.assertIn("k1", keys)

    # --- fail-fast on non-transient 4xx (guard against over-retry) ---

    def test_non_transient_403_not_retried_on_open(self) -> None:
        client = MagicMock(spec=gspread.Client)
        client.open_by_url.side_effect = [_api_error(403, "Forbidden")]
        with self.assertRaises(gspread.exceptions.APIError) as ctx:
            SheetsStorage(client, "https://sheets.example/url")
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(client.open_by_url.call_count, 1)

    def test_non_transient_404_not_retried_on_read(self) -> None:
        storage, spreadsheet = self._storage_with_spreadsheet()
        spreadsheet.worksheet.side_effect = [_api_error(404, "Not Found")]
        with self.assertRaises(gspread.exceptions.APIError) as ctx:
            storage.get_existing_keys("movies")
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(spreadsheet.worksheet.call_count, 1)

    def test_non_transient_401_not_retried_on_append(self) -> None:
        storage, worksheet = self._build([_api_error(401, "Unauthorized")])
        with self.assertRaises(gspread.exceptions.APIError) as ctx:
            storage.append_rows("movies", ROW_HEADERS, [_item("k1").to_row()])
        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(worksheet.append_rows.call_count, 1)


class TestSchemaValidation(unittest.TestCase):
    def test_valid_schema_passes(self) -> None:
        _validate_schema(ROW_HEADERS)

    def test_missing_column_raises(self) -> None:
        incomplete = [h for h in ROW_HEADERS if h != "dedupe_key"]
        with self.assertRaises(SchemaError) as ctx:
            _validate_schema(incomplete)
        self.assertIn("dedupe_key", str(ctx.exception))

    def test_extra_columns_allowed(self) -> None:
        extended = ROW_HEADERS + ["extra_col"]
        _validate_schema(extended)  # should not raise

    def test_multiple_missing_columns_reported(self) -> None:
        with self.assertRaises(SchemaError) as ctx:
            _validate_schema(["dedupe_key"])
        error_msg = str(ctx.exception)
        for col in set(ROW_HEADERS) - {"dedupe_key"}:
            self.assertIn(col, error_msg)

    def test_empty_headers_raises(self) -> None:
        with self.assertRaises(SchemaError):
            _validate_schema([])

    def test_tab_name_included_in_error(self) -> None:
        with self.assertRaises(SchemaError) as ctx:
            _validate_schema([], tab_name="steam_games")
        self.assertIn("steam_games", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
