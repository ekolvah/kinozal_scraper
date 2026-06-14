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

## Debugging
- Сначала root cause, потом fix. Никаких workarounds/shims, пока корень не понятен.
- Перед патчем — инструментируй: логи, входы, точка отказа. Только потом предложение.

## Активная работа

Текущие задачи: [GitHub Issues](https://github.com/ekolvah/kinozal_scraper/issues)

## PR Workflow

Процедурные правила workflow (создание ветки `issue-N-*` через `scripts/new_branch.py`, PR-дисциплина, no-main-push / no-self-merge, one-PR-one-unit, labels, `/plan #N` → `/implement #N`, architect-review гейт) живут в **[`.claude/rules/workflow.md`](.claude/rules/workflow.md)** — это их канон (always-load). Здесь не дублируем.

## Зависимости

Канон правила — [`.claude/rules/workflow.md`](.claude/rules/workflow.md) #7 (pip-compile в том же
коммите при изменении `requirements*.in`). **Правило сюда не дублировать.** Механика:
`scripts/ci_check.py` ловит version-drift и пакеты в `.in` без pin в `.txt`; push без
пересборки `.txt` = CI red.

## Перед каждым коммитом

`python scripts/ci_check.py` — подробнее в [CI doc](docs/architecture/ci.md).
`.githooks/pre-push` запускает ci_check автоматически перед push — не дублировать вручную.

## Architecture decisions

Key decisions recorded here; details in separate files to keep this file short.

- **[Principles](docs/architecture/principles.md)** — source of truth: 6 core principles + quality gates + governance (operational workflow delegated to [`.claude/rules/workflow.md`](.claude/rules/workflow.md)). When this file conflicts with `principles.md`, `principles.md` wins.
- [Project map](docs/architecture/project-map.md) — **полное оглавление навигации**: какой файл на какой вопрос отвечает (процесс + исходники + все deep-dive arch-доки `docs/architecture/*`) + где живёт какое знание (tier-модель + canonical-home). Отдельные доки сюда поштучно **не дублируем** — спускаемся через этот индекс.
- [Mindset](.claude/rules/mindset.md) — операционный режим агента (токен-тактики + указатели на цель-функцию/принципы/workflow), always-load
