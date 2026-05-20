# Quickstart: проверить рефакторинг руками

## Локальный success path

```
# Любой pipeline-файл (dry-run где есть)
$env:STEAM_DRY_RUN = "1"
python steam_pipeline.py
# expected: exit code 0, в логах "[steam_charts_mostplayed] sent N notification(s)"

$env:GITHUB_TRENDING_DRY_RUN = "1"
python github_trending_pipeline.py
# expected: exit code 0
```

## Локальный failure path (новое поведение)

Создать тестовый `sources_invalid.json` с заведомо невалидным URL для одного источника:

```
# 1. Скопировать sources.json -> sources_invalid.json
# 2. Поменять url одного источника на "http://127.0.0.1:1/"  (unreachable)
# 3. Запустить
$env:STEAM_SOURCES_PATH = "sources_invalid.json"
$env:STEAM_DRY_RUN = "1"
python steam_pipeline.py; echo "exit: $LASTEXITCODE"
# expected (after refactor): exit code 1, в логах "[<source_id>] fetch failed: ..."
# Before refactor for github_trending/steam: тоже exit 1 (regression-safe)
# Before refactor for events/json/kinozal: exit 0 (silent skip — это баг, чинит этот PR)
```

## Test suite

```
# Полный
python -m pytest tests/

# Конкретно изменённые
python -m pytest tests/test_github_trending_pipeline.py tests/test_steam_pipeline.py tests/test_events_pipeline.py tests/test_json_pipeline.py tests/test_kinozal_pipeline.py -v

# Параллельно (если pytest-xdist установлен) — проверяет независимость тестов
python -m pytest tests/ -n 4
```

## Pre-push gate

```
python scripts/ci_check.py
# expected: PASS на всех шагах (format, ruff, pytest, mypy, requirements drift, coverage doc)
```

## Smoke check на отсутствие anti-pattern

```
grep -rn "^_FAILED\b" --include="*.py" .
grep -rn "def _did_fail\b\|def _reset_failure\b" --include="*.py" .
# expected: пусто

grep -rn "_did_fail\|_reset_failure" --include="*.py" .
# expected: пусто (включая тесты)
```

## End-to-end production smoke

После merge — следующий GitHub Actions cron run (04:00 UTC) выполнит все pipeline'ы. Зелёный статус по всем шагам = success. Если хотя бы один источник упал — соответствующий step красный.
