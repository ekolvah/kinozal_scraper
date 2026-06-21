"""Storage Protocol: Google Sheets + InMemoryStorage, дедуп/row-schema."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

import gspread
import gspread.exceptions
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from generic_pipeline import ROW_HEADERS


class SchemaError(ValueError):
    """Raised when an existing worksheet has an incompatible column schema."""


def _is_sheets_rate_limit(exc: BaseException) -> bool:
    return isinstance(exc, gspread.exceptions.APIError) and exc.code == 429


@runtime_checkable
class Storage(Protocol):
    def get_existing_keys(self, tab_name: str) -> set[str]: ...

    def append_rows(self, tab_name: str, headers: list[str], rows: list[list[Any]]) -> None: ...


class SheetsStorage:
    """Google Sheets backed storage. Accepts an already-authenticated gspread.Client."""

    def __init__(self, client: gspread.Client, spreadsheet_url: str) -> None:
        self._spreadsheet = client.open_by_url(spreadsheet_url)

    def _get_or_create_worksheet(self, tab_name: str, headers: list[str]) -> gspread.Worksheet:
        try:
            return self._spreadsheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
            ws.append_row(headers)
            return ws

    def get_existing_keys(self, tab_name: str) -> set[str]:
        ws = self._get_or_create_worksheet(tab_name, ROW_HEADERS)
        headers = ws.row_values(1)

        missing = set(ROW_HEADERS) - set(headers)
        if missing:
            raise SchemaError(
                f"Tab '{tab_name}' is missing columns: {sorted(missing)}. "
                f"Expected: {ROW_HEADERS}. Found: {headers}"
            )

        key_col = headers.index("dedupe_key") + 1  # 1-based
        col_values = ws.col_values(key_col)
        return {str(v).strip() for v in col_values[1:] if v and str(v).strip()}

    def append_rows(self, tab_name: str, headers: list[str], rows: list[list[Any]]) -> None:
        if not rows:
            return
        ws = self._get_or_create_worksheet(tab_name, headers)
        self._ws_append_rows(ws, rows)

    @retry(
        retry=retry_if_exception(_is_sheets_rate_limit),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, max=60),
        reraise=True,
    )
    def _ws_append_rows(self, ws: gspread.Worksheet, rows: list[list[Any]]) -> None:
        ws.append_rows(rows, value_input_option=gspread.utils.ValueInputOption.user_entered)


class InMemoryStorage:
    """In-memory Storage for use in tests and dry runs."""

    def __init__(self) -> None:
        self._keys: dict[str, set[str]] = defaultdict(set)
        self._rows: dict[str, list[list[Any]]] = defaultdict(list)

    def get_existing_keys(self, tab_name: str) -> set[str]:
        return set(self._keys[tab_name])

    def append_rows(self, tab_name: str, headers: list[str], rows: list[list[Any]]) -> None:
        for row in rows:
            self._rows[tab_name].append(row)
            if row:
                self._keys[tab_name].add(str(row[0]))

    def seed_existing(self, tab_name: str, keys: Iterable[str]) -> None:
        """Pre-populate dedupe keys for tests without going through append_rows."""
        self._keys[tab_name].update(keys)

    def stored_rows(self, tab_name: str) -> list[list[Any]]:
        return list(self._rows[tab_name])
