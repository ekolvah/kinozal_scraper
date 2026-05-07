# Test coverage map

## Inventory (184 tests)

| Test file | Module under test | Tests | Classes |
|---|---|---|---|
| `test_kinozal_pipeline.py` | `kinozal_pipeline.py` | 34 | 8 |
| `test_generic_pipeline.py` | `generic_pipeline.py` | 29 | 6 |
| `test_json_pipeline.py` | `json_pipeline.py` | 26 | 12 |
| `test_pipeline_config.py` | `pipeline_config.py` | 24 | 4 |
| `test_telegram_notifier.py` | `telegram_notifier.py` | 19 | 5 |
| `test_sheets_storage.py` | `sheets_storage.py` | 18 | 3 |
| `test_gemini_enricher.py` | `gemini_enricher.py` | 18 | 6 |
| `test_events_pipeline.py` | `events_pipeline.py` | 15 | 4 |
| `test_tooling_smoke.py` | (toolchain) | 1 | 0 |

## Modules without dedicated tests

| Module | Reason | Mitigation |
|---|---|---|
| `youtube.py` | No Protocol boundary, requires live YouTube API | Indirect coverage via `test_kinozal_pipeline.py` trailer tests |
| `text_utils.py` | Small utility | Indirect coverage via `test_kinozal_pipeline.py::TestTitleYearMatches` |
| `TelegramChannelSummarizer.py` | Legacy, excluded from linting | None |
| `crypto.py` | Legacy, excluded from linting | None |
| `telegram_summarizer.py` | Legacy entry point (wiring only) | None |
| `scripts/ci_check.py` | Meta-tooling | None |

## Test patterns

- **Protocol doubles**: `InMemoryStorage`, `InMemoryNotifier`, `NullEnricher` — inject via constructor, assert on state after pipeline run
- **No external API mocks**: never mock `gspread`, `genai`, `telethon` — see [testing.md](testing.md)
- **Class naming**: `Test<Feature>` groups related assertions (e.g., `TestDeduplication`, `TestWriteBeforeNotify`)
- **Pipeline test structure**: build config dict → inject in-memory doubles → call `run_*_pipeline()` → assert on doubles' recorded calls

## Recommended next tests (prioritized)

1. **Extract `Youtube` behind a Protocol** — add `InMemoryYoutube` double, test `kinozal_pipeline` trailer enrichment path without live API
2. **Add `test_text_utils.py`** — direct tests for `title_year_matches()` (currently only indirect coverage)
3. **`RotatingGeminiEnricher` cooldown edge cases** — verify timing behavior when all models exhaust simultaneously
