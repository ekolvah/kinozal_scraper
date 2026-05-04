# kinozal_scraper

Multi-source notification pipeline that monitors Kinozal, GitHub, and Steam for new content, deduplicates via Google Sheets, and sends alerts to Telegram.

Runs daily at 04:00 UTC via GitHub Actions.

## Sources

| Source | Type | API | Sheets Tab | Dedupe Key |
|--------|------|-----|------------|------------|
| Kinozal top movies | HTML scraping | kinozal.tv | `movies` | Film title |
| GitHub new popular repos | JSON | GitHub Search API | `github_projects` | `full_name` |
| Steam top games | JSON | SteamSpy | `steam_games` | `appid` |

> **Note on GitHub source**: This uses the official [Search API](https://docs.github.com/en/rest/search/search) with `created:>=<7 days ago>` sorted by stars. It finds *new popular repositories*, not the curated [github.com/trending](https://github.com/trending) list which uses undocumented ranking.

## Architecture

```
sources.json          Declarative config (URLs, fields, limits, macros)
pipeline_config.py    Load config, expand macros, validate schema
json_pipeline.py      Generic runner for all JSON sources
generic_pipeline.py   Pure extract/normalize functions (NormalizedItem)
sheets_storage.py     Storage protocol (Google Sheets + InMemory)
telegram_notifier.py  Notifier protocol (Telegram API + InMemory)
kinozal_pipeline.py   Kinozal-specific runner (HTML, YouTube trailers)
scraper.py            Legacy entry point (Kinozal + Telegram summarizer)
```

Adding a new JSON source requires only a config entry in `sources.json` — no Python code.

## Configuration

### GitHub Actions Variables (Settings > Variables > Actions)

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOP_LIMIT` | `10` | Number of GitHub repos to track |
| `STEAM_TOP_LIMIT` | `10` | Number of Steam games to track |
| `URLS` | — | Kinozal URLs (`label\|url;label\|url`) |
| `LLM_MODEL` | — | Gemini model for Telegram summarizer |

### Secrets (Settings > Secrets > Actions)

| Secret | Used by |
|--------|---------|
| `GITHUB_TOKEN` | GitHub Search API auth (auto-provided) |
| `BOT_TOKEN` | Telegram bot token |
| `BOT_CHATID` | Telegram chat ID |
| `CREDENTIALS` | Google service account JSON |
| `SPREADSHEET_URL` | Google Sheets URL for deduplication |
| `API_KEY` | YouTube Data API key |
| `GOOGLE_API_KEY` | Gemini API key |

Changing `GITHUB_TOP_LIMIT` or `STEAM_TOP_LIMIT` adjusts item count without code edits.

## Failure behavior

- **Test gate**: pure logic tests run before any production code. If tests fail, the workflow stops — no partial execution.
- **JSON sources (at-least-once)**: notifications are sent before writing to Sheets. If Telegram fails, the item is NOT marked as seen and will retry on the next run. Possible duplicate notification on crash between send and store — acceptable tradeoff over silent data loss.
- **Source isolation**: one source failing (network error, API down) does not block other sources.
- **Kinozal (at-most-once)**: writes to Sheets before Telegram. Failed notifications are not retried.

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
```

## Quality tooling

Run the local quality gates:

```bash
python scripts/ci_check.py
```

Or individually:

```bash
python -m ruff format --check .
python -m ruff check .
python -m pytest
python -m mypy <modules>
```

Dependency audit:

```bash
python -m pip_audit -r requirements.txt
```

Regenerate pinned dependencies:

```bash
python -m piptools compile requirements.in --output-file requirements.txt --strip-extras --upgrade
python -m piptools compile requirements-dev.in --output-file requirements-dev.txt --strip-extras --upgrade
```
