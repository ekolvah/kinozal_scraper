"""Storage Protocol: Google Sheets + InMemoryStorage, дедуп/row-schema."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Protocol, TypeVar, runtime_checkable

import gspread
import gspread.exceptions
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from kinozal_scraper.generic_pipeline import ROW_HEADERS

_T = TypeVar("_T")

# Transient Sheets API errors worth retrying: 429 quota + canonical 5xx server
# blips (500/502/503/504). NOT 4xx permission/not-found (401/403/404) — those
# are real config/permission faults and must fail fast, surfaced visibly, not
# masked behind retries (§IV/§V).
_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})


class SchemaError(ValueError):
    """Raised when an existing worksheet has an incompatible column schema."""


def _is_transient_sheets_error(exc: BaseException) -> bool:
    return isinstance(exc, gspread.exceptions.APIError) and exc.code in _TRANSIENT_CODES


@runtime_checkable
class Storage(Protocol):
    def get_existing_keys(self, tab_name: str) -> set[str]: ...

    def append_rows(self, tab_name: str, headers: list[str], rows: list[list[Any]]) -> None: ...


class SheetsStorage:
    """Google Sheets backed storage. Accepts an already-authenticated gspread.Client.

    Every gspread network call routes through the single ``_net`` retry layer, so
    a transient Sheets 5xx/429 on *any* operation — construction, worksheet
    lookup/creation, read, or write — is retried rather than crashing the run.
    Wrapping each call individually (not whole composite methods) keeps exactly
    one retry layer (no nested 5x5 blow-up) and keeps ``add_worksheet`` /
    ``append_row(headers)`` atomically separate, so a 5xx while writing headers
    retries only that call — headers always land, no empty-tab SchemaError.
    """

    def __init__(self, client: gspread.Client, spreadsheet_url: str) -> None:
        self._spreadsheet = self._net(client.open_by_url, spreadsheet_url)

    @staticmethod
    @retry(
        retry=retry_if_exception(_is_transient_sheets_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, max=60),
        reraise=True,
    )
    def _net(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        """Run a single gspread network call, retrying transient 5xx/429."""
        return fn(*args, **kwargs)

    def _get_or_create_worksheet(self, tab_name: str, headers: list[str]) -> gspread.Worksheet:
        try:
            return self._net(self._spreadsheet.worksheet, tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self._net(
                self._spreadsheet.add_worksheet, title=tab_name, rows=1000, cols=len(headers)
            )
            self._net(ws.append_row, headers)
            return ws

    def get_existing_keys(self, tab_name: str) -> set[str]:
        ws = self._get_or_create_worksheet(tab_name, ROW_HEADERS)
        headers = self._net(ws.row_values, 1)

        missing = set(ROW_HEADERS) - set(headers)
        if missing:
            raise SchemaError(
                f"Tab '{tab_name}' is missing columns: {sorted(missing)}. "
                f"Expected: {ROW_HEADERS}. Found: {headers}"
            )

        key_col = headers.index("dedupe_key") + 1  # 1-based
        col_values = self._net(ws.col_values, key_col)
        return {str(v).strip() for v in col_values[1:] if v and str(v).strip()}

    def append_rows(self, tab_name: str, headers: list[str], rows: list[list[Any]]) -> None:
        if not rows:
            return
        ws = self._get_or_create_worksheet(tab_name, headers)
        # A batch that partially writes before a 5xx can double-write on retry.
        # 5xx-after-write is likelier than for 429 (429 usually rejects before
        # writing); we accept it and rely on next-run read-dedup, not "same risk
        # as 429" (see #288 Out of scope).
        self._net(
            ws.append_rows, rows, value_input_option=gspread.utils.ValueInputOption.user_entered
        )


class InMemoryStorage:
    """In-memory Storage for use in tests and dry runs."""

    def __init__(self) -> None:
        self._keys: dict[str, set[str]] = defaultdict(set)
        self._rows: dict[str, list[list[Any]]] = defaultdict(list)

    def get_existing_keys(self, tab_name: str) -> set[str]:
        return set(self._keys[tab_name])

    def append_rows(self, tab_name: str, headers: list[str], rows: list[list[Any]]) -> None:  # noqa: ARG002
        # `headers` is unused by the in-memory double but required by the Storage
        # Protocol signature (the real SheetsStorage.append_rows does use it).
        for row in rows:
            self._rows[tab_name].append(row)
            if row:
                self._keys[tab_name].add(str(row[0]))

    def seed_existing(self, tab_name: str, keys: Iterable[str]) -> None:
        """Pre-populate dedupe keys for tests without going through append_rows."""
        self._keys[tab_name].update(keys)

    def stored_rows(self, tab_name: str) -> list[list[Any]]:
        return list(self._rows[tab_name])
