# Feature Specification: Refactor telegram_summarizer / TelegramChannelSummarizer / crypto for testability

**Feature Branch**: `codex-issue-45-refactor-telegram-summarizer`

**Created**: 2026-05-18

**Status**: Draft

**Parent**: #45 (PR1 of 3)

**Input**: Three production files run daily under cron but are excluded from
ruff/mypy and have zero tests. The issue identifies the structural barrier
to testing: classmethod-only orchestration, global env-var reads, hardcoded
file paths, no dependency injection. This spec covers ONLY the structural
refactor — adding type annotations and turning the classmethod orchestrator
into a pure function over injected Protocols. Tests are deferred to PR2;
removing the ruff/mypy exclusions is deferred to PR3.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tests can inject fake Telegram + fake Gemini (P1)

As the engineer writing tests for the daily channel summary cron, when I
want to verify "if Gemini quota is exhausted the script still posts an
error message to Telegram" or "if one channel errors the others still go
through", I need to plug in a fake Telegram client and a fake Gemini
without subclassing Telethon or monkey-patching `genai`.

**Why this priority**: Without this refactor PR2 cannot exist — every
proposed test in #45 requires injecting a double. The current code reads
env-vars at class-definition time and calls `TelegramClient(...)` inside
the same function that orchestrates channels.

**Independent Test**: Import `TelegramChannelSummarizer.summarize_channels`,
pass a stub `TelegramReader` whose `fetch_channel(...)` returns
`("Канал X", "msg1\nmsg2", True)` and a stub `Summarizer` whose
`summarize(...)` returns `"<summary>"`. Assert the returned list has the
expected structure.

**Acceptance Scenarios**:

1. **Given** the refactored module, **When** test code constructs
   `summarize_channels(reader=Fake(), summarizer=Fake(), channel_urls=[...])`,
   **Then** the call succeeds without touching the network, env-vars, or
   the filesystem.
2. **Given** the entry point `telegram_summarizer.py`, **When** the cron
   step runs it under the existing env-var contract (`CHANNEL_URL`,
   `TELEGRAM_API_ID`, etc.), **Then** behaviour MUST be byte-identical to
   pre-refactor — same Telegram messages, same `📢 Канал: ...` format,
   same fallback text on empty results.
3. **Given** `crypto.py`, **When** test code calls a future round-trip
   helper `decrypt_bytes(encrypt_bytes(data, key), key) == data`, **Then**
   the assertion holds without any file IO. The current file-IO wrappers
   (`save_encrypter_session`, `load_encrypter_session`) MUST still work
   identically — they are thin shells around the pure helpers.

## Requirements *(mandatory)*

- **FR-001**: `crypto.py` MUST expose pure functions
  `encrypt_bytes(data: bytes, key: bytes) -> bytes` and
  `decrypt_bytes(data: bytes, key: bytes) -> bytes`. The existing
  class-static methods stay callable with identical signatures and same
  side effects (file paths, env-var read) — they delegate to the pure
  helpers.
- **FR-002**: `TelegramChannelSummarizer.py` MUST define two Protocols:
  - `TelegramReader`: `def fetch_channel(self, channel_url: str) -> ChannelMessages`
    where `ChannelMessages` is `tuple[str | None, str, bool]` (title,
    joined text, is_broadcast). The Protocol is **sync** so test doubles
    can be written without an event loop; the concrete `TelethonReader`
    bridges to async internally via `asyncio.run`. On error the reader
    returns `(None, "", False)` (matches current behaviour).
  - `Summarizer`: `def summarize(self, text: str, is_broadcast: bool) -> str`.
- **FR-003**: The module MUST expose a top-level pure function
  `summarize_channels(reader: TelegramReader, summarizer: Summarizer,
   channel_urls: list[str]) -> list[ChannelSummary]` — no `asyncio.run`
  inside, no env-var reads, no `genai` calls, no global state.
- **FR-004**: Two concrete classes MUST be provided in the same module:
  - `TelethonReader` — wraps the existing Telethon flow (Fernet-decrypted
    session, `TelegramClient`, `GetHistoryRequest`, day-cutoff filter,
    sender-name rendering, broadcast detection). One small public method.
  - `GeminiSummarizer` — wraps the existing model-rotation loop with
    `ResourceExhausted` fallback. Takes the model list at construction
    time (same `get_generation_models()` source as today).
- **FR-005**: `telegram_summarizer.py` (entry point) MUST construct the
  concrete reader + summarizer using env-vars exactly as today, then call
  `summarize_channels(...)` and render Telegram messages with the same
  format strings as pre-refactor. No new env-vars, no removed env-vars.
- **FR-006**: All three files MUST pass `ruff check` + `ruff format` and
  `mypy --strict` locally even though `pyproject.toml` / `ci_check.py`
  exclusions are NOT removed in this PR (PR3 removes them). Manual
  verification only; CI gate stays unchanged.
- **FR-007**: NO new tests are added in this PR. No tests are removed
  either. PR2 will add the taxonomy-coverage tests against the new
  Protocol surface.

### Success Criteria

- **SC-001**: `git diff main -- pyproject.toml scripts/ci_check.py .github/workflows/ci.yml CLAUDE.md docs/architecture/test-coverage.md` is empty after this PR — config changes are scoped to PR3.
- **SC-002**: The cron job (`run-script.yml` step "Run Telegram channel summarizer") continues to succeed after merge. Operator-verified via manual `workflow_dispatch`.
- **SC-003**: `python -m ruff check telegram_summarizer.py TelegramChannelSummarizer.py crypto.py` and `python -m mypy --strict ...` report zero issues. Verified locally before commit.

## Assumptions

- The Telethon API surface (`TelegramClient`, `GetHistoryRequest`,
  `StringSession`) does NOT change as part of this refactor.
- The Gemini model-rotation behaviour intentionally differs from
  `RotatingGeminiEnricher` (no cooldown, no `QuotaExhausted` re-raise,
  just sequential model fallback). That difference is preserved verbatim —
  unifying the two is out of scope for #45.
- `BROADCAST_PROMPT` / `CHAT_PROMPT` env-var fallback text is preserved
  byte-identical so existing operator tuning is not invalidated.

## Out of scope (deferred to PR2 / PR3)

- Adding tests (PR2).
- Removing ruff/mypy exclusions in `pyproject.toml`, `ci_check.py`,
  `.github/workflows/ci.yml`, `CLAUDE.md`, `docs/architecture/test-coverage.md` (PR3).
- Adding `types-pytz` to `requirements-dev.in` (PR3 — mypy will only see
  this file once exclusions lift).
- Unifying summarizer model-rotation with `RotatingGeminiEnricher`.
- Migrating from `google.generativeai` to `google.genai` (separate issue).
