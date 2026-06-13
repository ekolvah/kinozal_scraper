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
| A. Structure drift | `test_e2e_kinozal_titles.py::TestKinozalTitlesE2E` (kinozal HTML, real HTTP); `test_e2e_github_trending.py::TestGitHubTrendingE2E` (github trending HTML, real HTTP); `test_github_trending_pipeline.py::TestUS3Visibility` (zero-row → exit 1) | ⚠ partial — no E2E for GitHub `new_popular`/Steam JSON; new pipeline turns zero-row drift into red CI |
| B. Network failures | `test_telegram_notifier.py::TestTelegramNotifierRetry::test_connection_error_goes_to_failed`, `test_429_*`, `TestTelegramNotifierKnownBugs::test_session_post_called_with_explicit_timeout`, `test_requests_timeout_routes_to_failed` | ✅ — Telegram `session.post` now passes `timeout=30` ([#54](https://github.com/ekolvah/kinozal_scraper/issues/54)); kinozal/GitHub/Steam HTTP timeouts already set to 30s in their `_fetch_*` |
| C. Auth & quota | `test_gemini_enricher.py::TestGeminiEnricherQuota`, `TestRotatingGeminiEnricher::test_rotates_to_next_model_on_quota`, `test_all_models_exhausted_*`; `test_json_pipeline.py::TestEnricherQuotaCircuitBreaker::test_all_models_exhausted_from_start_uses_on_error_fallback`; `test_telegram_notifier.py::test_http_400_goes_to_failed`; `test_sheets_storage.py::TestSheetsStorageRetryOn429` (`test_429_then_success_retries_and_succeeds`, `test_429_repeated_eventually_raises_after_5_attempts`, `test_non_429_api_error_not_retried`); `test_telegram_summarizer.py::TestGeminiSummarizerQuota`, `TestTelethonReaderErrorSwallow::test_fetch_channel_swallows_exception_returns_error_tuple` | ✅ — Sheets 429 retried with exponential backoff (5 attempts, 1s→60s) ([#55](https://github.com/ekolvah/kinozal_scraper/issues/55)); Gemini all-exhausted covered caller-side; Telethon-session-expired surfaces as `(None, "", False)` swallow ([#45](https://github.com/ekolvah/kinozal_scraper/issues/45)); no GitHub 401 |
| D. Config errors | `test_pipeline_config.py::TestValidateSourcesConfig`, `TestExpandMacros`, `TestBuildMacroContext`, `TestLoadSourcesConfig` (incl. `test_unresolved_macro_in_url_raises_config_error`, `test_unresolved_macro_in_nested_field_raises`), `TestConfigValidationKnownGaps::test_invalid_css_row_selector_not_caught_by_validator` | ⚠ documents-current-bug — invalid CSS `row_selector` not caught ([#57](https://github.com/ekolvah/kinozal_scraper/issues/57)); unresolved `{{macro}}` now raises `ConfigError` at load time ([#58](https://github.com/ekolvah/kinozal_scraper/issues/58)) |
| E. Data integrity | `test_json_pipeline.py::TestJsonPipelineDeduplication`, `test_sheets_storage.py::TestInMemoryStorage`, `TestSchemaValidation`; `test_kinozal_pipeline.py::TestPipelineDeduplication`, `TestPipelineWriteBeforeNotify`; `test_events_pipeline.py::TestEventsPipelineDeduplication` | ✅ — all pipeline tests now invoke `run_*_pipeline` directly (rewritten in [#51](https://github.com/ekolvah/kinozal_scraper/pull/51)) |
| F. Message rendering | `test_telegram_notifier.py::TestFormatField`, `TestBuildNotification`, `TestTelegramNotifierImageFallback::test_photo_400_falls_back_to_text_send`, `TestTelegramNotifierMessageLimits::test_message_over_4096_chars_is_truncated_and_sent`, `test_caption_over_1024_chars_falls_back_to_sendmessage`; `test_kinozal_pipeline.py::TestPipelineNotificationContent`; `test_events_pipeline.py::TestEventsPipelineNotificationContent`; `test_generic_pipeline.py::TestBuildNotificationRawFallback`, `TestBuildNotificationNewlineCollapse`, `TestBuildNotificationLinks`; `test_telegram_summarizer.py::TestFormatSummaryMessage` (http-vs-non-http URL, HTML-special-char escape) | ✅ — messages >4096 chars are truncated client-side, captions >1024 chars fall back to `sendMessage` ([#53](https://github.com/ekolvah/kinozal_scraper/issues/53)) |
| G. Trailer enrichment | `test_kinozal_pipeline.py::TestEnrichWithTrailer`, `TestKinozalKnownBugs::test_youtube_quota_exhausted_pipeline_continues_with_empty_trailer` | ✅ — quota-exhausted scenario covered pipeline-level |
| H. Pipeline orchestration | `test_json_pipeline.py::TestJsonPipelineSourceIsolation`, `TestJsonPipelineFailedNotifications`; `test_kinozal_pipeline.py::TestPipelineFailureIsolation`, `TestPipelineWriteBeforeNotify`; `test_events_pipeline.py::TestEventsPipelineEdgeCases`; `test_telegram_summarizer.py::TestSummarizeChannelsOrchestration` (one-channel-error isolation, empty-text/empty-summary skip) | ✅ — all pipeline tests rewritten to call production functions directly ([#51](https://github.com/ekolvah/kinozal_scraper/pull/51)); Telegram summarizer orchestration covered via Protocol doubles ([#45](https://github.com/ekolvah/kinozal_scraper/issues/45)) |
| I. URL resolution | `test_kinozal_pipeline.py::TestKinozalUrls`, `TestBaseUrlResolution`, `TestKinozalEmptyUrlGuard::test_url_field_drift_logs_warning_but_still_notifies`; `test_generic_pipeline.py::TestBuildNotificationLinks` | ✅ — kinozal items with empty `url` after extraction emit WARNING; item still notified so the user sees the missing link and reports the drift ([#56](https://github.com/ekolvah/kinozal_scraper/issues/56)) |
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

## Test patterns

- **Protocol doubles**: `InMemoryStorage`, `InMemoryNotifier`, `NullEnricher` — inject via constructor, assert on state after pipeline run
- **No mocks of internal functions**: call `run_*_pipeline()` directly with Protocol doubles — see [testing.md](testing.md)
- **Class naming**: `Test<Feature>` groups related assertions (e.g., `TestJsonPipelineDeduplication`, `TestEventsPipelineNotificationContent`)
- **Pipeline test structure**: build config dict → inject in-memory doubles → call `run_*_pipeline()` → assert on doubles' recorded calls

<!-- AUTOGEN:INVENTORY:START -->

## Inventory (372 tests)

Auto-generated by `python scripts/gen_test_coverage.py`. Do not edit manually.

| Test file | Module under test | Tests | Classes |
|---|---|---|---|
| `test_kinozal_pipeline.py` | `kinozal_pipeline.py` | 48 | 12 |
| `test_gemini_enricher.py` | `gemini_enricher.py` | 42 | 15 |
| `test_pipeline_config.py` | `pipeline_config.py` | 32 | 6 |
| `test_telegram_summarizer.py` | `telegram_summarizer.py` | 31 | 6 |
| `test_generic_pipeline.py` | `generic_pipeline.py` | 29 | 6 |
| `test_json_pipeline.py` | `json_pipeline.py` | 25 | 12 |
| `test_telegram_notifier.py` | `telegram_notifier.py` | 24 | 8 |
| `test_steam_pipeline.py` | `steam_pipeline.py` | 23 | 9 |
| `test_events_pipeline.py` | `events_pipeline.py` | 22 | 6 |
| `test_sheets_storage.py` | `sheets_storage.py` | 22 | 4 |
| `test_github_trending_pipeline.py` | `github_trending_pipeline.py` | 20 | 7 |
| `test_issue_branch.py` | `issue_branch.py` | 11 | 3 |
| `test_check_red.py` | `check_red.py` | 10 | 2 |
| `test_validate_issue_sections.py` | `validate_issue_sections.py` | 10 | 3 |
| `test_new_branch.py` | `new_branch.py` | 7 | 4 |
| `test_crypto.py` | `crypto.py` | 5 | 1 |
| `test_ci_check.py` | `ci_check.py` | 4 | 3 |
| `test_e2e_kinozal_titles.py` | `e2e_kinozal_titles.py` | 3 | 1 |
| `test_e2e_github_trending.py` | `e2e_github_trending.py` | 2 | 1 |
| `test_settings_deny.py` | `settings_deny.py` | 2 | 1 |

<!-- AUTOGEN:INVENTORY:END -->
