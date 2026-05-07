# kinozal_scraper — контекст для Claude

## Что делает приложение
Парсит топ kinozal.tv по расписанию (GitHub Actions, 04:00 UTC), дедуплицирует через Google Sheets, отправляет новинки в Telegram. Параллельно суммаризует Telegram-каналы через Gemini.

## Файлы исключённые из ruff/mypy

`telegram_summarizer.py`, `TelegramChannelSummarizer.py`, `crypto.py` исключены из
ruff и mypy в `scripts/ci_check.py` и `pyproject.toml` (legacy код).

## Активная работа

Текущие задачи: [GitHub Issues](https://github.com/ekolvah/kinozal_scraper/issues)

## Ветки
- Каждый issue — отдельная ветка `codex-issue-N-*`
- CI триггерится на `codex-*` и `main`
- Мержить только после зелёного CI

## Перед каждым коммитом

`python scripts/ci_check.py` — подробнее в [CI doc](docs/architecture/ci.md).

## Architecture decisions

Key decisions recorded here; details in separate files to keep this file short.

- [Runtime overview](docs/architecture/runtime.md) — 4 pipelines, protocols, data flow
- [Pipeline](docs/architecture/pipeline.md) — layers, NormalizedItem, extract_from_* contracts
- [Storage](docs/architecture/storage.md) — Storage Protocol, DI, EAFP, row schema
- [Testing](docs/architecture/testing.md) — no mocks on external APIs, Protocol doubles
- [Test coverage map](docs/architecture/test-coverage.md) — what is tested, gaps, patterns
- [CI & deployment](docs/architecture/ci.md) — GitHub Actions, env vars, setup
- [Gemini enrichment](docs/architecture/gemini.md) — model rotation, quota strategy
