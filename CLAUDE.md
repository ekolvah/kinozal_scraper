# kinozal_scraper — контекст для Claude

## Что делает приложение
Парсит топ kinozal.tv по расписанию (GitHub Actions, 04:00 UTC), дедуплицирует через Google Sheets, отправляет новинки в Telegram. Параллельно суммаризует Telegram-каналы через Gemini.

## Файлы которые нельзя трогать без явного указания
- `scraper.py` — рантайм продакшн
- `TelegramChannelSummarizer.py` — рантайм продакшн
- `crypto.py` — рантайм продакшн
- `.github/workflows/run-script.yml` — scheduled workflow продакшн

Эти файлы исключены из ruff и mypy намеренно.

## Активная работа: declarative pipeline (issues #1–#8)

Порядок реализации (зависимости сверху вниз):
```
#1 sources.json + macro engine          ← done (PR #9)
#2 generic fetch/extract/normalize core
#3 Google Sheets batch storage
#4 Telegram notifier queue              (параллельно с #3)
#5 Port Kinozal → generic pipeline      ← точка переключения на новый рантайм
#6 GitHub repos source
#7 Steam games source
#8 Update Action + README
```

До issue #5 `scraper.py` и `run-script.yml` не трогаем — продакшн работает как прежде.

## Ветки
- Каждый issue — отдельная ветка `codex-issue-N-*`
- CI триггерится на `codex-*` и `main`
- Мержить только после зелёного CI

## Перед каждым коммитом
```bash
python scripts/ci_check.py
```
Pre-push hook активирован через `git config core.hooksPath .githooks` — запускается автоматически при пуше.

## Установка окружения
```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
```
