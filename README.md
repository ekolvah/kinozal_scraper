# kinozal_scraper

Multi-source notification pipeline that monitors Kinozal, GitHub, Steam, and Soldout for new content, deduplicates via Google Sheets, and sends alerts to Telegram. Also summarizes Telegram channels via Gemini.

Runs daily via GitHub Actions (schedule in `.github/workflows/run-script.yml`).

## Sources

| Source | Type | Sheets Tab | Dedupe Key |
|--------|------|------------|------------|
| Kinozal top movies | HTML scraping | `movies` | Film title |
| GitHub new popular repos | JSON (Search API) | `github_projects` | `full_name` |
| Steam Most Played | JSON (Steam Charts) | `steam_games` | `appid` |
| Soldout events | HTML scraping | `soldout_events` | Event title |
| Telegram channels | Gemini summarization | — | — |

Adding a new JSON source requires only a config entry in `sources.json` — no Python code.

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
pip install -e . --no-deps   # installs the kinozal_scraper package (entry points run as `python -m kinozal_scraper.<pipeline>`)
git config core.hooksPath .githooks
python scripts/ci_check.py   # local CI: format + lint + tests + mypy
```

The runnable pipelines live under `src/kinozal_scraper/` and are launched as
`python -m kinozal_scraper.<module>` (e.g. `python -m kinozal_scraper.github_popular_pipeline`);
the production schedule wires them in `.github/workflows/run-script.yml`.

Configuration (env vars, secrets, CI workflows) is documented in [docs/architecture/ci.md](docs/architecture/ci.md).

## Architecture

See [docs/architecture/](docs/architecture/) for detailed design docs.
Full file-by-file index (what each file answers) → [project-map.md](docs/architecture/project-map.md).

## Regenerate pinned dependencies

```bash
python -m piptools compile requirements.in --output-file requirements.txt --strip-extras --upgrade
python -m piptools compile requirements-dev.in --output-file requirements-dev.txt --strip-extras --upgrade
```
