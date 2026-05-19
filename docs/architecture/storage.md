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

## Column semantics — invariants

For a given Sheets tab, all sources writing into that tab MUST agree on the
meaning of each column. Mixing semantics within one column is a bug — it
shipped once (PR #85 / issue #60) where `github_trending` wrote daily-delta
("172 stars today") into the same `github_projects.metric` column that
`github_new_popular` filled with total stargazer count (`14113`), making the
column unusable for cross-source analysis. Closed by #86.

### `github_projects.metric`

- **Meaning**: total stargazers count of the repository at observation time.
- **Format**: integer string with no thousands separators (e.g. `"14113"`,
  not `"14,113"` and not `"14,113 stars today"`).
- **Sources writing here**: `github_new_popular`, `github_trending`. Both
  MUST produce this exact shape — see
  `tests/test_github_trending_pipeline.py::TestMetricColumnSemantics` for
  the pin-tests.
- **Daily delta** ("X stars today") is a Telegram-only signal. Trending
  pipeline stashes it in `item.raw["stars_today"]` so the
  `message_template` can reference `{stars_today}`; it is never written to
  the row.

Adding a third GitHub-shaped source to `github_projects` requires producing
`metric` in the same shape — otherwise extend this section first.

### `steam_games.metric`

- **Meaning**: weekly peak concurrent in-game players (`peak_in_game` from
  `ISteamChartsService/GetMostPlayedGames`) — same number as shown on
  https://store.steampowered.com/charts/mostplayed.
- **Format**: integer string with no thousands separators
  (e.g. `"1313208"`).
- **Sources writing here**: `steam_charts_mostplayed`. The legacy SteamSpy
  source `steam_top_games` was retired in #95 — it wrote SteamSpy's `ccu`
  field (rolling 2-week average), a different semantic. Pre-#95 rows in
  this tab carry that older shape; new rows use peak_in_game.
- **Rank / last-week-rank** are Telegram-only signals; they live on the
  template via `{rank}` and `{last_week_rank}` (drawn from `item.raw`) and
  are never written to the row.

## Write ordering

Always write to Sheets BEFORE sending to Telegram. See `pipeline.md`.
This prevents duplicate Telegram notifications if the process crashes
between the two operations.
