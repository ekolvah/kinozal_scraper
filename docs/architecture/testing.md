# Testing philosophy

> **Question this document answers:** How do we plan to guarantee product quality?
>
> For the actual coverage map (which tests catch which category of bugs and where the gaps are), see [test-coverage.md](test-coverage.md).

## Rule: no mocks of internal functions

Mock of external I/O is acceptable. Mock of internal business logic is not.

**Rule:**
- External boundaries (Sheets, Telegram, YouTube, HTTP) → Fake implementation
  (`InMemoryStorage`, `InMemoryNotifier`) or a saved HTML/JSON fixture.
- Internal functions (`_extract_kinozal_items`, `run_kinozal_pipeline`, etc.)
  → NEVER mock. Tests must call the production function directly.

**Anti-pattern (to remove in step 2):**
`_FetchingPipeline` in `test_kinozal_pipeline.py` duplicates `run_kinozal_pipeline`
inside the test. If production deduplication logic changes, the test stays green —
it tests its own copy.

**Correct pattern (as in `test_json_pipeline.py`):**
Call `run_*_pipeline()` directly. Pass `InMemoryStorage` and `InMemoryNotifier`.
Assert on doubles' state after the call.

## Test levels

**Integration-first (primary level):**
- Call production pipeline with saved HTML/JSON fixtures and Protocol doubles.
- Fixtures: saved HTML dumps from kinozal.tv, JSON responses from GitHub/Steam.
  Update dumps manually when site structure changes.
- Covers full business logic without flakiness (no network, no rate limits).
- When: on every PR, in CI.

**Unit (pure functions):**
- Isolated test of a single pure function.
- Fixes the function contract (not "catches bug X", but guarantees given
  this input always this output).
- When: for transformation logic (parsing, formatting, normalization).

**E2E smoke (real HTTP / real Telegram):**
- Minimal run against the real site and real API.
- Verifies the external resource hasn't changed structure or blocked us.
- When: before PR merge (PRs in this project are infrequent); the production
  script already runs daily on schedule and acts as an E2E smoke test itself.
- Failure blocks merge (site structure broken → update fixture/parser).

## Bug taxonomy

| ID | Category | Examples |
|---|---|---|
| A | Structure drift | kinozal changes CSS selector; GitHub changes response key |
| B | Network failures | timeout; 5xx; unavailable; gzipped body |
| C | Auth & quota | Sheets 401/429; YouTube quota; Gemini quota; Telegram 401 |
| D | Config errors | bad CSS selector; macro not expanded; limit ≤ 0 |
| E | Data integrity | dedupe_key drift → duplicates; write-vs-notify race |
| F | Message rendering | size >4096; HTML escape; broken image → fallback |
| G | Trailer enrichment | YouTube no-result; year mismatch; quota exhausted |
| H | Pipeline orchestration | partial failure isolation; write-before-notify order |
| I | URL resolution | relative→absolute; base_url drift; broken url field |
| J | Concurrent state | rerun after crash; partially written rows |

## Bug → Test type mapping

Choose the cheapest reliable test for each category.

| Category | Integration + fixtures | Unit | E2E smoke |
|---|---|---|---|
| A. Structure drift | ✅ update fixture | ❌ | ✅ before PR merge |
| B. Network failures | ✅ raise in fake HTTP | ❌ | ⚠ |
| C. Auth & quota | ✅ fake raises exception | ❌ | ❌ no credentials in CI |
| D. Config errors | ❌ | ✅ pure validation | ❌ |
| E. Data integrity | ✅ InMemoryStorage state | ❌ | ❌ |
| F. Message rendering | ✅ InMemoryNotifier | ✅ pure format | ⚠ test-channel |
| G. Trailer | ✅ _FakeYoutube | ❌ | ❌ |
| H. Orchestration | ✅ Protocol doubles | ❌ | ❌ |
| I. URL resolution | ✅ | ✅ pure | ❌ |
| J. Concurrent state | ✅ InMemory with state | ❌ | ❌ |

## What gets tested

- All pure transformation logic: macro expansion, field mapping, normalization,
  row construction, deduplication key lookups, schema validation.
- Protocol contract: `InMemoryStorage` tests verify the `Storage` interface.

## What does NOT get tested in this repo

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
see [test-coverage.md](test-coverage.md). The inventory table is auto-generated;
run `python scripts/gen_test_coverage.py` to refresh after adding or removing tests.
