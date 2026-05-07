# kinozal_scraper — контекст для Claude

## Что делает приложение
Парсит топ kinozal.tv по расписанию (GitHub Actions, 04:00 UTC), дедуплицирует через Google Sheets, отправляет новинки в Telegram. Параллельно суммаризует Telegram-каналы через Gemini.

## Файлы исключённые из ruff/mypy

`telegram_summarizer.py`, `TelegramChannelSummarizer.py`, `crypto.py` исключены из
ruff и mypy в `scripts/ci_check.py` и `pyproject.toml` (legacy код).

## Активная работа: declarative pipeline (issues #1–#8)

Порядок реализации (зависимости сверху вниз):
```
#1 sources.json + macro engine          ← done (PR #9)
#2 generic fetch/extract/normalize core ← done
#3 Google Sheets batch storage          ← done
#4 Telegram notifier queue              ← done
#5 Port Kinozal → generic pipeline      ← done (PR #36)
#6 GitHub repos source                  ← done
#7 Steam games source                   ← done
#8 Update Action + README
```

## Ветки
- Каждый issue — отдельная ветка `codex-issue-N-*`
- CI триггерится на `codex-*` и `main`
- Мержить только после зелёного CI

## Перед каждым коммитом
```bash
python scripts/ci_check.py
```
Pre-push hook активирован через `git config core.hooksPath .githooks` — запускается автоматически при пуше.

## Architecture decisions

Key decisions recorded here; details in separate files to keep this file short.

- [Testing](docs/architecture/testing.md) — no mocks on external APIs, use Protocol + InMemoryStorage
- [Pipeline](docs/architecture/pipeline.md) — layers, NormalizedItem, extract_from_* contracts
- [Storage](docs/architecture/storage.md) — Storage Protocol, DI, EAFP, row schema

## Установка окружения
```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
```
