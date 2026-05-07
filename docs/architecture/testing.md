# Testing philosophy

## Rule: no mocks on external APIs

Do not mock `gspread`, `telethon`, `google.generativeai`, or any other external
library. Fake objects for deep API hierarchies are fragile — they pass tests
while real integration silently breaks (different return types, quota errors,
missing fields).

**Instead:**
- Extract pure functions (row construction, field mapping, normalization) and
  unit-test those directly — no mocks needed.
- Define a `Protocol` for each external boundary and provide an in-memory
  implementation for tests (`InMemoryStorage`, `InMemoryNotifier`).
- Integration tests against real external services belong in a separate test
  suite, run manually against a dedicated test document/channel.

## Known gap: no automated E2E tests

CI only runs unit tests. If a Google Sheets API contract changes or a token
expires, unit tests with `InMemoryStorage` will pass while production fails.
Mitigation: a separate manual integration test suite against a dedicated
test spreadsheet and Telegram channel. Automating this in CI is a future issue.

## What gets unit-tested

- All pure transformation logic: macro expansion, field mapping, normalization,
  row construction, deduplication key lookups, schema validation.
- Protocol contract: `InMemoryStorage` tests verify the `Storage` interface.

## What does NOT get unit-tested in this repo

- `SheetsStorage` internals (gspread call order, worksheet creation).
- `TelegramChannelSummarizer` / Telethon calls.
- Any code path that requires live credentials.

## Test runner

```bash
python -m pytest          # via pyproject.toml config
python scripts/ci_check.py  # full CI mirror: format + lint + tests + mypy
```

## Test coverage map

For a structured inventory of what is tested and where gaps exist,
see [test-coverage.md](test-coverage.md).
