# kinozal_scraper — контекст для Claude

## Что делает приложение
Парсит топ kinozal.tv по расписанию (GitHub Actions, 04:00 UTC), дедуплицирует через Google Sheets, отправляет новинки в Telegram. Параллельно суммаризует Telegram-каналы через Gemini.

## Среда

Windows + git-bash. Все грабли ниже повторялись ≥2 раз — не переоткрывать.

- **Python**: `python`, НЕ `python3` (последнее — Microsoft Store stub, который открывает магазин).
- **Утилиты**: нет `jq`/`sed`/`awk`. Парсить JSON/текст — pure-Python скриптами в `scripts/`.
- **Пути**: `~/` не резолвится надёжно в shell-hook'ах и settings.json. Используй абсолютные (`C:/Users/<username>/...` или `$HOME/...` в bash).
- **PowerShell ≠ bash**: `$null` (не `/dev/null`), `$env:VAR` (не `$VAR`), backtick для line continuation. Для POSIX-скриптов вызывай Bash tool явно.
- **`subprocess.run(capture_output=True)`** на Windows + git-bash может вернуть `stdout=None` несмотря на `text=True`. Нормализуй в caller'е, не верь типу (см. #109).
- **Спорадические file-lock / AV-сканер** на длинных `git`/`pytest`: перед root-cause hunt — 1 retry. Если воспроизводится — тогда копай.
- **`pip-compile`**: при изменении `requirements*.in` обязательно перекомпилировать `.txt` в **том же коммите**. `ci_check.py` ловит drift, но push без пересборки = CI red.

## Debugging
- Сначала root cause, потом fix. Никаких workarounds/shims, пока корень не понятен.
- Перед патчем — инструментируй: логи, входы, точка отказа. Только потом предложение.

## Активная работа

Текущие задачи: [GitHub Issues](https://github.com/ekolvah/kinozal_scraper/issues)

Issue создаём с label обязательно: `gh issue create --label bug|enhancement|documentation|testing|...`.

## PR Workflow
- **Новый bug/feature → `/plan #N` → `/implement #N`** (commands в `.claude/commands/` опираются на `scripts/validate_issue_sections.py`, `scripts/issue_branch.py`, `scripts/check_red.py`, `scripts/ci_check.py`). Ручной workflow ниже — fallback для опечаток/однострочников или когда `/implement` упал на escape hatch. См. #114.
- **`## Architect review` — обязательная 7-я секция issue** (энфорсится `validate_issue_sections.py`). `/plan` заполняет её прогоном субагента `architect-reviewer` (`.claude/agents/architect-reviewer.md`); тривиальные правки — `skipped: <причина>`. Подробнее — `docs/architecture/principles.md` §Development Workflow.
- Каждый issue — отдельная ветка `codex-issue-N-*`. CI триггерится на `codex-*` и `main`.
- **Создавать ветку только через `python scripts/new_branch.py codex-issue-N-<slug>`** — скрипт чекаутит main, делает `pull --ff-only` и только потом `checkout -b`. Это гарантирует, что новая ветка растёт от свежего `origin/main`, а не от соседней feature-ветки (иначе после squash-merge будет divergence — см. #66).
- **Никогда не пушить напрямую в `main`** — только через PR.
- **Никогда не мержить PR самостоятельно** (`gh pr merge` запрещено без явного подтверждения пользователя для конкретного PR).
- PR мержит только пользователь вручную после своего апрува.
- Один PR — одна логическая единица: docs-only PR отдельно от refactor/feature (PR #39 → #40 переделывали из-за смешения).

## Зависимости
- При изменении `requirements*.in` запусти `pip-compile` для соответствующего `.txt` в том же коммите.
- `scripts/ci_check.py` ловит drift версий и пакеты в `.in` без pin в `.txt`.

## Перед каждым коммитом

`python scripts/ci_check.py` — подробнее в [CI doc](docs/architecture/ci.md).
`.githooks/pre-push` запускает ci_check автоматически перед push — не дублировать вручную.

## Architecture decisions

Key decisions recorded here; details in separate files to keep this file short.

- **[Principles](docs/architecture/principles.md)** — source of truth: 6 core principles + dev workflow + quality gates. When this file conflicts with `principles.md`, `principles.md` wins.
- [Files map](docs/architecture/files-map.md) — на какой вопрос отвечает каждый файл процесса + карта известных дублей (backlog де-дупликации)
- [Runtime overview](docs/architecture/runtime.md) — 4 pipelines, protocols, data flow
- [Pipeline](docs/architecture/pipeline.md) — layers, NormalizedItem, extract_from_* contracts
- [Storage](docs/architecture/storage.md) — Storage Protocol, DI, EAFP, row schema
- [Testing](docs/architecture/testing.md) — no mocks on external APIs, Protocol doubles
- [Test coverage map](docs/architecture/test-coverage.md) — what is tested, gaps, patterns
- [CI & deployment](docs/architecture/ci.md) — GitHub Actions, env vars, setup
- [Gemini enrichment](docs/architecture/gemini.md) — model rotation, quota strategy
