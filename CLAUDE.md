# kinozal_scraper — контекст для Claude

## Что делает приложение
Парсит топ kinozal.tv по расписанию (GitHub Actions, 04:00 UTC), дедуплицирует через Google Sheets, отправляет новинки в Telegram. Параллельно суммаризует Telegram-каналы через Gemini.

## Среда
- Windows. Используй `python` (НЕ `python3` — это Microsoft Store stub).
- Не полагайся на `jq`/`sed`/`awk` — пиши pure-Python скрипты в `scripts/`.

## Debugging
- Сначала root cause, потом fix. Никаких workarounds/shims, пока корень не понятен.
- Перед патчем — инструментируй: логи, входы, точка отказа. Только потом предложение.

## Активная работа

Текущие задачи: [GitHub Issues](https://github.com/ekolvah/kinozal_scraper/issues)

Issue создаём с label обязательно: `gh issue create --label bug|enhancement|documentation|testing|...`.

## PR Workflow
- Каждый issue — отдельная ветка `codex-issue-N-*`. CI триггерится на `codex-*` и `main`.
- **Создавать ветку только через `python scripts/new_branch.py codex-issue-N-<slug>`** — скрипт чекаутит main, делает `pull --ff-only` и только потом `checkout -b`. Это гарантирует, что новая ветка растёт от свежего `origin/main`, а не от соседней feature-ветки (иначе после squash-merge будет divergence — см. #66).
- **Никогда не пушить напрямую в `main`** — только через PR.
- **Никогда не мержить PR самостоятельно** (`gh pr merge` запрещено без явного подтверждения пользователя для конкретного PR).
- PR мержит только пользователь вручную после своего апрува.
- Один PR — одна логическая единица: docs-only PR отдельно от refactor/feature (PR #39 → #40 переделывали из-за смешения).
- Предпочтительно `/commit-push-pr` из плагина `commit-commands` — авто-ветка если на main.

## Зависимости
- При изменении `requirements*.in` запусти `pip-compile` для соответствующего `.txt` в том же коммите.
- `scripts/ci_check.py` ловит drift версий и пакеты в `.in` без pin в `.txt`.

## Перед каждым коммитом

`python scripts/ci_check.py` — подробнее в [CI doc](docs/architecture/ci.md).
`.githooks/pre-push` запускает ci_check автоматически перед push — не дублировать вручную.

## Architecture decisions

Key decisions recorded here; details in separate files to keep this file short.

- [Runtime overview](docs/architecture/runtime.md) — 4 pipelines, protocols, data flow
- [Pipeline](docs/architecture/pipeline.md) — layers, NormalizedItem, extract_from_* contracts
- [Storage](docs/architecture/storage.md) — Storage Protocol, DI, EAFP, row schema
- [Testing](docs/architecture/testing.md) — no mocks on external APIs, Protocol doubles
- [Test coverage map](docs/architecture/test-coverage.md) — what is tested, gaps, patterns
- [CI & deployment](docs/architecture/ci.md) — GitHub Actions, env vars, setup
- [Gemini enrichment](docs/architecture/gemini.md) — model rotation, quota strategy

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
[specs/007-pipeline-result-ok/plan.md](specs/007-pipeline-result-ok/plan.md)
<!-- SPECKIT END -->
