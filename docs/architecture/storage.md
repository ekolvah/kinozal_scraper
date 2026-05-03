# Storage architecture

## Pattern: Protocol + implementations

```python
# sheets_storage.py
class Storage(Protocol):
    def get_existing_keys(self, tab_name: str) -> set[str]: ...
    def append_rows(self, tab_name: str, rows: list[list[Any]]) -> None: ...

class SheetsStorage:      # production
class InMemoryStorage:    # tests and dry-runs
```

Callers type-hint against `Storage`, not `SheetsStorage`. This makes any
code that uses storage trivially testable without touching gspread.

## Dependency injection

`SheetsStorage` accepts a ready `gspread.Client`, not credentials dict.
Construction of the client (auth, spreadsheet URL) is the caller's
responsibility. Reason: separates configuration from execution, makes
the class easier to compose and test.

```python
# correct
client = gspread.service_account_from_dict(credentials)
storage = SheetsStorage(client, spreadsheet_url)

# wrong — mixes auth into the storage layer
storage = SheetsStorage(credentials_dict, spreadsheet_url)
```

## EAFP worksheet creation

Use try/except, not check-then-create. Reduces API calls 2–3× for the
common case (tab already exists), avoids Google Sheets quota errors.

```python
try:
    ws = spreadsheet.worksheet(tab_name)
except WorksheetNotFound:
    ws = spreadsheet.add_worksheet(...)
    ws.append_row(ROW_HEADERS)
```

## Schema validation

`get_existing_keys` validates that existing worksheet headers contain all
`ROW_HEADERS` columns. Raises `SchemaError` on mismatch — fails fast instead
of silently writing rows with wrong column count.

When `_get_or_create_worksheet` creates a new tab, it writes `ROW_HEADERS`
as the first row — new tabs always have a valid schema.

## Dedupe key column lookup

Column index is found dynamically by reading the header row and searching
for `"dedupe_key"`. Never hardcode column A or index 0.

## Row schema

`ROW_HEADERS` in `generic_pipeline.py`:
`["dedupe_key", "title", "url", "metric", "source_id", "notified_at"]`

## Write ordering

Always write to Sheets BEFORE sending to Telegram. See `pipeline.md`.
This prevents duplicate Telegram notifications if the process crashes
between the two operations.
