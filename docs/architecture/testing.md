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
  implementation for tests (`InMemoryStorage`, future `InMemoryNotifier`).
- Integration tests against real external services belong in a separate test
  suite, run manually against a dedicated test document/channel.

## What gets unit-tested

- All pure transformation logic: macro expansion, field mapping, normalization,
  row construction, deduplication key lookups.
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
