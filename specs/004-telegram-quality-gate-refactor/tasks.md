# Tasks: Refactor telegram_summarizer / TelegramChannelSummarizer / crypto for testability (PR1 of 3)

**Input**: [spec.md](./spec.md)

**No tests in this PR** — PR2 adds them. The refactor is verified locally
via `ruff` + `mypy --strict` against the three files (with exclusions
temporarily lifted in the local env only, NOT committed).

## Phase 1: Refactor

- [ ] **T1** `crypto.py` — Add pure
      `encrypt_bytes(data: bytes, key: bytes) -> bytes` and
      `decrypt_bytes(data: bytes, key: bytes) -> bytes`. Rewrite
      `save_encrypter_session()` / `load_encrypter_session()` to delegate
      to those helpers. Type annotations on every function. Keep the
      `class crypto` namespace so existing call-sites
      (`crypto.load_encrypter_session()`) keep working.
- [ ] **T2** `TelegramChannelSummarizer.py` — Top-down rewrite preserving
      behaviour:
  - Add module-level dataclass `ChannelSummary(channel: str, url: str,
    summary: str)`.
  - Define `TelegramReader` Protocol with `async fetch_channel(...)`.
  - Define `Summarizer` Protocol with `summarize(text, is_broadcast)`.
  - Move current Telethon flow into `class TelethonReader` constructor +
    `fetch_channel`. Pass api_id/hash/session/phone via constructor; no
    `os.getenv` inside the class.
  - Move current Gemini flow into `class GeminiSummarizer` constructor +
    `summarize`. Pass `models` + `broadcast_prompt` + `chat_prompt` via
    constructor.
  - Add top-level pure `summarize_channels(reader, summarizer,
    channel_urls) -> list[ChannelSummary]`.
  - Remove module-level `logger.basicConfig(...)` and the env-var class
    attrs — entry point owns env-var reads.
- [ ] **T3** `telegram_summarizer.py` — Build the concrete reader +
      summarizer from env-vars, call `summarize_channels(...)`, render the
      Telegram messages with the existing 1:1 format. Keep `basicConfig`
      under `__main__`.

## Phase 2: Local quality-gate check (not yet part of CI)

- [ ] **T4** Locally temp-lift exclusions, run
      `python -m ruff check telegram_summarizer.py TelegramChannelSummarizer.py crypto.py`
      and `python -m mypy --strict telegram_summarizer.py TelegramChannelSummarizer.py crypto.py`.
      Fix anything reported. Revert the exclusions-config edits before
      `git add`. PR3 will lift them for real with `types-pytz` too.

## Phase 3: Polish

- [ ] **T5** `python scripts/ci_check.py` — green (uses current
      exclusions; verifies nothing else regressed).
- [ ] **T6** Commit, push, `gh pr create` — references #45 but does NOT
      close it (two more PRs follow). Body lists the deferred work.

## Out of scope (this PR)

- Tests (PR2).
- Removing the legacy-exclusions from `pyproject.toml`,
  `scripts/ci_check.py`, `.github/workflows/ci.yml`, `CLAUDE.md`,
  `docs/architecture/test-coverage.md` (PR3).
- Adding `types-pytz` to dev requirements (PR3).
- Migrating from `google.generativeai` to `google.genai`.
