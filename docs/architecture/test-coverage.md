# Test coverage map

> **Question this document answers:** How is product quality actually guaranteed today — which tests catch which bug categories, and where are the gaps?
>
> For the strategy (rules, levels, taxonomy), see [testing.md](testing.md).

## Coverage by bug category

Each row maps a category from [Bug taxonomy](testing.md#bug-taxonomy) to the
concrete tests that catch it. Status: ✅ covered / ⚠ partial or unreliable /
⚠ documents-current-bug (test pins a known production bug — see linked issue) /
❌ gap.

When you add or rewrite a test, update this table. When you add a new bug
category, add it here AND in `testing.md` taxonomy.

| Category | Tests catching it | Status |
|---|---|---|
| A. Structure drift | `test_e2e_kinozal_titles.py::TestKinozalTitlesE2E` (kinozal HTML, real HTTP) | ⚠ partial — no E2E for GitHub/Steam JSON |
| B. Network failures | `test_telegram_notifier.py::TestTelegramNotifierRetry::test_connection_error_goes_to_failed`, `test_429_*`, `TestTelegramNotifierKnownBugs::test_session_post_called_without_explicit_timeout`, `test_requests_timeout_routes_to_failed` | ⚠ documents-current-bug — Telegram `session.post` has no `timeout=` ([#54](https://github.com/ekolvah/kinozal_scraper/issues/54)); kinozal/GitHub/Steam HTTP timeouts already set to 30s in their `_fetch_*` |
| C. Auth & quota | `test_gemini_enricher.py::TestGeminiEnricherQuota`, `TestRotatingGeminiEnricher::test_rotates_to_next_model_on_quota`, `test_all_models_exhausted_*`; `test_json_pipeline.py::TestEnricherQuotaCircuitBreaker::test_all_models_exhausted_from_start_uses_on_error_fallback`; `test_telegram_notifier.py::test_http_400_goes_to_failed`; `test_sheets_storage.py::TestSheetsStorageKnownBugs::test_append_rows_429_propagates_no_retry` | ⚠ documents-current-bug — Sheets 429 has no retry ([#55](https://github.com/ekolvah/kinozal_scraper/issues/55)); Gemini all-exhausted now covered caller-side; no GitHub 401 |
| D. Config errors | `test_pipeline_config.py::TestValidateSourcesConfig`, `TestExpandMacros`, `TestBuildMacroContext`, `TestLoadSourcesConfig`, `TestConfigValidationKnownGaps::test_invalid_css_row_selector_not_caught_by_validator`, `test_unresolved_macro_in_url_passes_validation` | ⚠ documents-current-bug — invalid CSS `row_selector` not caught ([#57](https://github.com/ekolvah/kinozal_scraper/issues/57)); unresolved `{{macro}}` leaks to URL ([#58](https://github.com/ekolvah/kinozal_scraper/issues/58)) |
| E. Data integrity | `test_json_pipeline.py::TestJsonPipelineDeduplication`, `test_sheets_storage.py::TestInMemoryStorage`, `TestSchemaValidation`; `test_kinozal_pipeline.py::TestPipelineDeduplication`, `TestPipelineWriteBeforeNotify`; `test_events_pipeline.py::TestEventsPipelineDeduplication` | ✅ — all pipeline tests now invoke `run_*_pipeline` directly (rewritten in [#51](https://github.com/ekolvah/kinozal_scraper/pull/51)) |
| F. Message rendering | `test_telegram_notifier.py::TestFormatField`, `TestBuildNotification`, `TestTelegramNotifierImageFallback::test_photo_400_falls_back_to_text_send`, `TestTelegramNotifierKnownBugs::test_message_over_4096_chars_lost_as_failed`; `test_kinozal_pipeline.py::TestPipelineNotificationContent`; `test_events_pipeline.py::TestEventsPipelineNotificationContent`; `test_generic_pipeline.py::TestBuildNotificationRawFallback`, `TestBuildNotificationNewlineCollapse`, `TestBuildNotificationLinks` | ⚠ documents-current-bug — messages >4096 chars are dropped instead of split ([#53](https://github.com/ekolvah/kinozal_scraper/issues/53)) |
| G. Trailer enrichment | `test_kinozal_pipeline.py::TestEnrichWithTrailer`, `TestKinozalKnownBugs::test_youtube_quota_exhausted_pipeline_continues_with_empty_trailer` | ✅ — quota-exhausted scenario covered pipeline-level |
| H. Pipeline orchestration | `test_json_pipeline.py::TestJsonPipelineSourceIsolation`, `TestJsonPipelineFailedNotifications`; `test_kinozal_pipeline.py::TestPipelineFailureIsolation`, `TestPipelineWriteBeforeNotify`; `test_events_pipeline.py::TestEventsPipelineEdgeCases` | ✅ — all pipeline tests rewritten to call production functions directly ([#51](https://github.com/ekolvah/kinozal_scraper/pull/51)) |
| I. URL resolution | `test_kinozal_pipeline.py::TestKinozalUrls`, `TestBaseUrlResolution`, `TestKinozalKnownBugs::test_url_field_drift_yields_silent_empty_link`; `test_generic_pipeline.py::TestBuildNotificationLinks` | ⚠ documents-current-bug — kinozal HTML attribute drift silently yields empty `url` in notifications ([#56](https://github.com/ekolvah/kinozal_scraper/issues/56)) |
| J. Concurrent state | none | ❌ gap — no tests for rerun-after-crash or partially-written rows |

**⚠ documents-current-bug** = the test pins production behaviour that is
known to be buggy. The linked issue tracks the fix; when the bug is fixed,
the test must be inverted (assert the *correct* behaviour) and the table
row promoted to ✅.

## Modules without dedicated tests

| Module | Reason | Mitigation |
|---|---|---|
| `youtube.py` | No Protocol boundary, requires live YouTube API | Indirect coverage via `test_kinozal_pipeline.py::TestEnrichWithTrailer` |
| `text_utils.py` | Small utility | Indirect coverage via `test_kinozal_pipeline.py::TestTitleYearMatches` |
| `TelegramChannelSummarizer.py` | Legacy, excluded from linting | None |
| `crypto.py` | Legacy, excluded from linting | None |
| `telegram_summarizer.py` | Legacy entry point (wiring only) | None |
| `scripts/ci_check.py` | Meta-tooling | None |

## Test patterns

- **Protocol doubles**: `InMemoryStorage`, `InMemoryNotifier`, `NullEnricher` — inject via constructor, assert on state after pipeline run
- **No mocks of internal functions**: call `run_*_pipeline()` directly with Protocol doubles — see [testing.md](testing.md)
- **Class naming**: `Test<Feature>` groups related assertions (e.g., `TestJsonPipelineDeduplication`, `TestEventsPipelineNotificationContent`)
- **Pipeline test structure**: build config dict → inject in-memory doubles → call `run_*_pipeline()` → assert on doubles' recorded calls

<!-- AUTOGEN:INVENTORY:START -->

## Inventory (204 tests)

Auto-generated by `python scripts/gen_test_coverage.py`. Do not edit manually.

| Test file | Module under test | Tests | Classes |
|---|---|---|---|
| `test_kinozal_pipeline.py` | `kinozal_pipeline.py` | 42 | 10 |
| `test_generic_pipeline.py` | `generic_pipeline.py` | 29 | 6 |
| `test_json_pipeline.py` | `json_pipeline.py` | 27 | 12 |
| `test_pipeline_config.py` | `pipeline_config.py` | 26 | 5 |
| `test_telegram_notifier.py` | `telegram_notifier.py` | 23 | 7 |
| `test_sheets_storage.py` | `sheets_storage.py` | 20 | 4 |
| `test_gemini_enricher.py` | `gemini_enricher.py` | 18 | 6 |
| `test_events_pipeline.py` | `events_pipeline.py` | 16 | 4 |
| `test_e2e_kinozal_titles.py` | `e2e_kinozal_titles.py` | 3 | 1 |

<!-- AUTOGEN:INVENTORY:END -->
