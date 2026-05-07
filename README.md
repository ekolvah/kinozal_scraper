# kinozal_scraper

Multi-source notification pipeline that monitors Kinozal, GitHub, Steam, and Soldout for new content, deduplicates via Google Sheets, and sends alerts to Telegram. Also summarizes Telegram channels via Gemini.

Runs daily at 04:00 UTC via GitHub Actions.

## Sources

| Source | Type | Sheets Tab | Dedupe Key |
|--------|------|------------|------------|
| Kinozal top movies | HTML scraping | `movies` | Film title |
| GitHub new popular repos | JSON (Search API) | `github_projects` | `full_name` |
| Steam top games | JSON (SteamSpy) | `steam_games` | `appid` |
| Soldout events | HTML scraping | `soldout_events` | Event title |
| Telegram channels | Gemini summarization | — | — |

Adding a new JSON source requires only a config entry in `sources.json` — no Python code.

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
python scripts/ci_check.py   # local CI: format + lint + tests + mypy
```

Configuration (env vars, secrets, CI workflows) is documented in [docs/architecture/ci.md](docs/architecture/ci.md).

## Architecture

See [docs/architecture/](docs/architecture/) for detailed design docs:

- [Runtime overview](docs/architecture/runtime.md) — pipelines, protocols, data flow
- [Pipeline](docs/architecture/pipeline.md) — layers, NormalizedItem, notification templates
- [Storage](docs/architecture/storage.md) — Storage Protocol, DI, row schema
- [Testing](docs/architecture/testing.md) — no mocks on external APIs, Protocol doubles
- [Test coverage](docs/architecture/test-coverage.md) — what is tested, gaps
- [CI & deployment](docs/architecture/ci.md) — GitHub Actions, env vars, setup
- [Gemini enrichment](docs/architecture/gemini.md) — model rotation, quota strategy

## Regenerate pinned dependencies

```bash
python -m piptools compile requirements.in --output-file requirements.txt --strip-extras --upgrade
python -m piptools compile requirements-dev.in --output-file requirements-dev.txt --strip-extras --upgrade
```
