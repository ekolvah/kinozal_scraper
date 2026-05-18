# Feature Specification: Test coverage for telegram_summarizer / TelegramChannelSummarizer / crypto

**Feature Branch**: `codex-issue-45-tests-telegram-summarizer`

**Created**: 2026-05-18

**Status**: Draft

**Parent**: #45 (PR2 of 3)

**Input**: PR1 (#90, merged) refactored `TelegramChannelSummarizer` to
expose Protocol-injected surfaces (`TelegramReader`, `Summarizer`,
`summarize_channels`) and turned `crypto.py` into pure helpers with
file-IO wrappers. The three modules still have no tests and are listed
in `test-coverage.md` under "Modules without dedicated tests". This PR
adds taxonomy-coverage tests against the new Protocol surface using the
existing project conventions (Protocol doubles for the boundary,
`unittest.mock.patch` only against the external `genai` client — the same
pattern `test_gemini_enricher.py` uses).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — taxonomy bugs in the cron path get caught before merge (P1)

As the engineer making changes to the daily Telegram-channel-summary
cron, when I touch `summarize_channels`, `GeminiSummarizer`, or the
notification-text format, I want the test suite to fail if I break:

- orchestration isolation (one channel error masking the others — H),
- Gemini quota fallback (model rotation on `ResourceExhausted` — C),
- Telethon error swallow (one channel crashing the whole run — C),
- crypto round-trip and key mismatch detection (security regression),
- message rendering (`📢 Канал: …` template HTML-escape and link logic — F).

**Why this priority**: Today these scenarios are uncovered. The script
runs daily under cron with no integration tests; structural drift in
Gemini's API or in our refactor would only surface after a failed cron
on production.

**Independent Test**: Run `pytest tests/test_telegram_summarizer.py
tests/test_crypto.py -v`. All new tests pass without network or live
secrets.

**Acceptance Scenarios**:

1. **Given** the new `summarize_channels` test with a stub reader that
   returns `(None, "", False)` for one channel and valid data for two
   others, **When** the function runs, **Then** the result contains
   exactly two `ChannelSummary`s and the stub summarizer was called
   exactly twice (skip path verified).
2. **Given** the new `GeminiSummarizer` test with `genai.GenerativeModel`
   patched so the first model raises `ResourceExhausted`, **When**
   `summarize(...)` is called, **Then** the second model is tried and
   its response is returned; the warning log mentions the first model
   name.
3. **Given** `encrypt_bytes(data, key)`, **When** I feed the ciphertext
   to `decrypt_bytes(ciphertext, key)`, **Then** I get back the original
   bytes. Decrypting with a different generated key raises
   `cryptography.fernet.InvalidToken`.
4. **Given** `format_summary_message(ChannelSummary)`, **When** the URL
   starts with `http`, **Then** the rendered text wraps the channel name
   in an `<a href=...>` tag; when it doesn't, the channel name is plain
   HTML-escaped text.

## Requirements *(mandatory)*

- **FR-001**: `tests/test_crypto.py` MUST cover:
  - Round-trip: `decrypt_bytes(encrypt_bytes(data, key), key) == data` for
    several payload sizes.
  - Wrong-key path: decrypting with a different Fernet key raises
    `cryptography.fernet.InvalidToken`.
  - Non-determinism: two `encrypt_bytes(data, key)` calls produce
    different ciphertexts (Fernet's random IV).
- **FR-002**: `tests/test_telegram_summarizer.py` MUST cover:
  - **H** orchestration — one-channel-error isolation: stub
    `TelegramReader` returns `(None, "", False)` for one URL and valid
    data for others; `summarize_channels` skips only the failing channel.
  - **H** orchestration — empty-text skip: stub returns
    `(title, "", False)`; summarizer is NOT called for that URL.
  - **H** orchestration — empty-summary skip: stub summarizer returns
    `""`; no `ChannelSummary` is added for that URL.
  - **C** Gemini quota — sequential fallback on `ResourceExhausted` until
    a model succeeds.
  - **C** Gemini quota — all models exhausted → returns empty string,
    error logged.
  - **C** Non-quota exception → returns empty string immediately, error
    logged, next models NOT tried (preserves current behaviour from
    `TelegramChannelSummarizer.py:86-87`).
  - **C** Telethon error swallow — `_fetch_channel_async` raising any
    `Exception` returns `(None, "", False)` from `TelethonReader`.
  - **F** Message rendering — `format_summary_message` with http URL →
    `<a href=...>` label; without http URL → plain escaped.
  - **F** Message rendering — HTML special chars in `summary` and
    `channel` are escaped.
- **FR-003**: A small extraction refactor is in scope for this PR: the
  per-summary message construction currently inlined in
  `telegram_summarizer.py` `__main__` (lines 26-30) MUST move into a
  module-level pure function `format_summary_message(summary:
  ChannelSummary) -> str` so it is unit-testable. `__main__` calls the
  helper. No behaviour change.
- **FR-004**: `test-coverage.md` MUST be updated:
  - Remove `TelegramChannelSummarizer.py`, `crypto.py`,
    `telegram_summarizer.py` from "Modules without dedicated tests".
  - Append references to the new tests in the C, F, H category rows.
- **FR-005**: NO removal of ruff/mypy/CI exclusions in this PR (PR3's
  job). The new test files MUST pass ruff + mypy under the existing
  config without temporary `# type: ignore` comments.

### Success Criteria

- **SC-001**: After this PR, the three previously-untested production
  files have at least one test each that calls the production function
  directly with a Protocol double (no in-test reimplementation of
  business logic — `testing.md` anti-pattern rule).
- **SC-002**: `python scripts/ci_check.py` green; new test count +N
  visible in the auto-generated inventory.

## Assumptions

- `unittest.mock.patch` against `TelegramChannelSummarizer.genai.GenerativeModel`
  is acceptable — it's the same external-boundary pattern
  `test_gemini_enricher.py` uses for `GeminiEnricher._generate`.
- The existing `summarize_channels` signature does not change. The
  `format_summary_message` extraction is the only structural change in
  this PR.
- `TelethonReader` does NOT get tested against a real Telegram client —
  only the swallow-on-exception path is verified by patching
  `_fetch_channel_async` to raise.

## Out of scope (deferred to PR3)

- Removing the legacy-exclusions in `pyproject.toml`,
  `scripts/ci_check.py`, `.github/workflows/ci.yml`, `CLAUDE.md`.
- Promoting `telegram_summarizer.py` from "Legacy entry point (wiring
  only)" to a fully-gated module — that follows from PR3.
- Migrating away from `google.generativeai`.
