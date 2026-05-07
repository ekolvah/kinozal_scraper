# CI and deployment

## Local pre-commit

```bash
python scripts/ci_check.py
```

Runs: ruff format check → ruff lint → pytest → mypy.
Legacy files (`telegram_summarizer.py`, `TelegramChannelSummarizer.py`, `crypto.py`)
are excluded from ruff and mypy.

Pre-push hook: `.githooks/pre-push` runs `ci_check.py` automatically.
Activate: `git config core.hooksPath .githooks`

## CI workflow (`ci.yml`)

Triggers: PR and push to `main` / `codex-*` branches.

Steps: checkout → Python 3.12 → install deps → ruff format → ruff lint → pytest → mypy → pip-audit.

mypy excludes the same legacy files as `ci_check.py`, plus `.claude` directory.

## Production workflow (`run-script.yml`)

Schedule: `0 4 * * *` UTC + manual `workflow_dispatch`.

Steps run sequentially:
1. **pytest** — smoke gate, fails fast
2. **json_pipeline.py** — GitHub + Steam sources
3. **events_pipeline.py** — Soldout events
4. **kinozal_pipeline.py** — Kinozal movies
5. **telegram_summarizer.py** — `if: always()` (runs even if earlier steps fail)

## Environment variables

### Shared across pipelines

| Variable | Type | Used by |
|---|---|---|
| `CREDENTIALS` | secret | json_pipeline, events_pipeline, kinozal_pipeline (Google Sheets service account JSON) |
| `SPREADSHEET_URL` | secret | json_pipeline, events_pipeline, kinozal_pipeline |
| `TELEGRAM_BOT_TOKEN` | secret | all 4 steps |
| `TELEGRAM_CHAT_ID` | secret | all 4 steps |

### json_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | secret | GitHub API auth |
| `GH_TOP_LIMIT` | var | max GitHub repos to fetch |
| `STEAM_TOP_LIMIT` | var | max Steam games to fetch |
| `GOOGLE_API_KEY` | secret | Gemini API for enrichment |
| `LLM_MODEL` | var | preferred Gemini model |
| `GEMINI_EXCLUDED_MODELS` | var | comma-separated models to skip |

### events_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `SOLDOUT_URL` | var | Soldout events page URL |

### kinozal_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `API_KEY` | secret | Kinozal API key |
| `URLS` | var | Kinozal page URLs to scrape |

### telegram_summarizer

| Variable | Type | Purpose |
|---|---|---|
| `CHANNEL_URL` | var | semicolon-separated Telegram channel URLs/IDs |
| `GOOGLE_API_KEY` | secret | Gemini API for summarization |
| `API_HASH` | secret | Telethon app hash |
| `TELEGRAM_API_ID` | secret | Telethon app ID |
| `PHONE_NUMBER` | secret | Telethon auth phone |
| `TELETHON_SESSION` | secret | Telethon session string |
| `SECRET_KEY` | secret | crypto module key |
| `LLM_MODEL` | var | preferred Gemini model |
| `GEMINI_EXCLUDED_MODELS` | var | comma-separated models to skip |

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
```
