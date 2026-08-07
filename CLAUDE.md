# kinozal_scraper — контекст для Claude

## Что делает приложение
Парсит топ kinozal.tv по расписанию (GitHub Actions, cron в `.github/workflows/run-script.yml`), дедуплицирует через Google Sheets, отправляет новинки в Telegram. Параллельно суммаризует Telegram-каналы через Gemini.

## Среда

Windows + git-bash. Все грабли ниже повторялись ≥2 раз — не переоткрывать.

- **Python**: `python`, НЕ `python3` (последнее — Microsoft Store stub, который открывает магазин).
- **Утилиты**: нет `jq`/`sed`/`awk`. Парсить JSON/текст — pure-Python скриптами в `scripts/`.
- **Пути**: `~/` не резолвится надёжно в shell-hook'ах и settings.json. Используй абсолютные (`C:/Users/<username>/...` или `$HOME/...` в bash).
- **PowerShell ≠ bash**: `$null` (не `/dev/null`), `$env:VAR` (не `$VAR`), backtick для line continuation. Для POSIX-скриптов вызывай Bash tool явно.
- **`subprocess.run`, захватывающий вывод**: всегда `encoding="utf-8"`, и **никаких `or ""` на `stdout`/`stderr`** — `None` означает сломанный захват (поток-читатель умер на декодировании), и дефолт подменяет отказ пустотой. Оба правила энфорсит `tests/test_subprocess_encoding.py` (#364, #410). Если ребёнок — Python, ему нужен ещё `PYTHONUTF8=1`/`-X utf8`; это гард не ловит.
- **Спорадические file-lock / AV-сканер** на длинных `git`/`pytest`: перед root-cause hunt — 1 retry. Если воспроизводится — тогда копай.
- **`ci_check.py` / `git push` с pre-push хуком идут минуты** (тайминг — канон в [CI doc](docs/architecture/ci.md#local-pre-commit)): вывод замирает после `pytest` на шаге `pip-audit` — это **сетевой шаг, а не hang**. Не убивать процесс, не поллить — один foreground-вызов с `timeout: 600000` ([mindset](.claude/rules/mindset.md)).
- **`tasklist` в agent-песочнице (Bash-тул на Windows-машине мейнтейнера) возвращает пустой вывод** (0 строк даже без фильтра); в обычном терминале работает. Делать по нему вывод «процесс умер» нельзя — прецедент: так был ошибочно запущен второй экземпляр `ci_check`.

## Debugging
- Сначала root cause, потом fix. Никаких workarounds/shims, пока корень не понятен.
- Перед патчем — инструментируй: логи, входы, точка отказа. Только потом предложение.

## Активная работа

Текущие задачи: [GitHub Issues](https://github.com/ekolvah/kinozal_scraper/issues)

## PR Workflow

Процедурные правила workflow (роли, ветка, PR-дисциплина, labels, гейты) — канон в **[`docs/architecture/agent-process.md`](docs/architecture/agent-process.md)**. Claude выполняет planner/reviewer через `/plan #N`, implementer/fixer — через `/implement #N`; дефолт каталога для них — Codex `$implement-issue #N`, маршрут выбирает user. Здесь не дублируем.

## Зависимости

Канон — [`agent-process.md`](docs/architecture/agent-process.md) (pip-compile в том же коммите при изменении
`requirements*.in`). Механика: `scripts/ci_check.py` ловит version-drift и пакеты в `.in` без pin
в `.txt`.

## Перед каждым коммитом

`python scripts/ci_check.py` — подробнее в [CI doc](docs/architecture/ci.md).
`.githooks/pre-push` запускает ci_check автоматически перед push — не дублировать вручную.

## Architecture decisions

- **[Principles](docs/architecture/principles.md)** — source of truth: принципы §I–VII + quality gates + governance. При конфликте с этим файлом выигрывает `principles.md`.
- [Project map](docs/architecture/project-map.md) — **полное оглавление навигации** (какой файл на какой вопрос отвечает) + IA-policy (tier-модель, canonical-home). Отдельные доки сюда поштучно **не дублируем** — спускаемся через этот индекс.
- [Mindset](.claude/rules/mindset.md) — токен-тактики Claude-харнесса + указатели на цель-функцию/принципы/процесс, always-load
