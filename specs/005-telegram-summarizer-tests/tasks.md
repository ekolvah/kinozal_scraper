# Tasks: Test coverage for telegram_summarizer / TelegramChannelSummarizer / crypto (PR2 of 3)

**Input**: [spec.md](./spec.md)

**Tests are the deliverable** — Constitution Principle I. The two new
files + the small `format_summary_message` extraction together satisfy
FR-001 through FR-004.

## Phase 1: Refactor for testability

- [ ] **T1** `telegram_summarizer.py` — extract the per-summary message
      construction from the `__main__` block into a module-level
      `format_summary_message(summary: ChannelSummary) -> str`. `__main__`
      calls the helper. Output byte-identical to pre-PR. Type
      annotations.

## Phase 2: New tests

- [ ] **T2** `tests/test_crypto.py`:
  - `TestCryptoRoundTrip::test_encrypt_decrypt_round_trip` — random key,
    multiple payload sizes.
  - `TestCryptoRoundTrip::test_decrypt_with_wrong_key_raises` —
    `InvalidToken`.
  - `TestCryptoRoundTrip::test_encrypt_is_non_deterministic` — two
    encrypts produce different ciphertexts.
- [ ] **T3** `tests/test_telegram_summarizer.py`:
  - `TestSummarizeChannelsOrchestration` (H):
    - `test_one_channel_error_does_not_block_others`
    - `test_empty_text_skips_summarizer`
    - `test_empty_summary_not_added_to_results`
  - `TestGeminiSummarizerQuota` (C):
    - `test_first_model_quota_falls_back_to_next` (patches
      `genai.GenerativeModel`)
    - `test_all_models_exhausted_returns_empty`
    - `test_non_quota_exception_returns_empty_without_fallback`
  - `TestTelethonReaderErrorSwallow` (C):
    - `test_fetch_channel_swallows_exception_returns_error_tuple` —
      patches `TelethonReader._fetch_channel_async` to raise.
  - `TestFormatSummaryMessage` (F):
    - `test_http_url_wraps_channel_in_anchor`
    - `test_non_http_url_renders_plain_text`
    - `test_html_special_chars_escaped`

## Phase 3: Docs + polish

- [ ] **T4** `docs/architecture/test-coverage.md`:
  - Remove the 3 rows in "Modules without dedicated tests" for
    `TelegramChannelSummarizer.py`, `crypto.py`, `telegram_summarizer.py`.
  - Append the new test references to category rows C, F, H.
- [ ] **T5** `python scripts/ci_check.py` — green.
- [ ] **T6** Commit, push, `gh pr create` referencing #45 (PR2/3). No
      self-merge.

## Out of scope (PR3)

- Removing the legacy ruff/mypy/CI exclusions from `pyproject.toml`,
  `scripts/ci_check.py`, `.github/workflows/ci.yml`, `CLAUDE.md`.
- Live-Telethon integration test (no credentials in CI).
