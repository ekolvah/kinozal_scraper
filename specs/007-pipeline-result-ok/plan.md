# Implementation Plan: PipelineResult.ok вместо _FAILED

**Branch**: `codex-issue-97-pipeline-result-ok` | **Date**: 2026-05-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-pipeline-result-ok/spec.md`

## Summary

Заменить module-level `_FAILED` глобал + `_did_fail()` / `_reset_failure()` хелперы в `github_trending_pipeline.py` и `steam_pipeline.py` на возвращаемое значение `PipelineResult` (single) или `list[PipelineResult]` (multi-source) из runner-функций. Привести три «тихих» pipeline-файла (`events_pipeline.py`, `json_pipeline.py`, `kinozal_pipeline.py`) к тому же контракту, чтобы при сбое источника они тоже выходили с exit code 1 (устранение silent-skip регрессии, прямой effect [[constitution.md#IV-Visibility-Over-Silence]]).

Контракт `PipelineResult` уже существует в `generic_pipeline.py` — добавляется ровно столько, чтобы агрегировать fetch/extract фейлы поверх per-source extract результата. `__main__` блоки читают `.ok` всех результатов и решают `sys.exit(0|1)`.

## Technical Context

**Language/Version**: Python 3.12 (см. `.python-version` / pre-push hook fallback `py -3.12`).

**Primary Dependencies**: `dataclasses` (stdlib), `requests`, `bs4`, `gspread`, существующие модули `generic_pipeline`, `pipeline_config`, `sheets_storage`, `telegram_notifier`. Никаких новых runtime-зависимостей.

**Storage**: Google Sheets через `Storage` Protocol; в тестах `InMemoryStorage`. Не меняется.

**Testing**: pytest + `tests/conftest.py`. Тесты используют Protocol doubles (`InMemoryStorage`, `InMemoryNotifier`); правило «no mocks of external APIs» из `docs/architecture/testing.md` сохраняется.

**Target Platform**: GitHub Actions cron (Linux runner) для прода; локально Windows + venv (`.venv/Scripts/python`).

**Project Type**: CLI / batch — каждый pipeline-файл это самостоятельный точечный entrypoint, запускаемый GitHub Actions step'ом.

**Performance Goals**: N/A для рефакторинга (никаких новых сетевых вызовов, никакой логики; `PipelineResult` объекты лёгкие dataclass).

**Constraints**: Backward-compatibility test-side — после рефакторинга все существующие тесты остаются зелёными (с обновлёнными assertion'ами там, где они опирались на `_reset_failure`).

**Scale/Scope**: 5 pipeline-файлов + соответствующие 5 test-файлов (`tests/test_*_pipeline.py`). ~250 строк изменений.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution: `.specify/memory/constitution.md` v1.0.0.

| Principle | Compliance |
|-----------|------------|
| **I. Test-First (NON-NEGOTIABLE)** | ✅ `/speckit-tasks` поставит test-таски выше impl-тасков. Тесты на новое поведение (events/json/kinozal → exit 1 при фейле источника) пишутся first и должны падать против текущего main, потом зеленеть после impl. |
| **II. Protocol Boundaries with DI** | ✅ `Storage`/`Notifier`/`Enricher` Protocol-ы не трогаются. Runner сигнатуры расширяются только возвращаемым типом. |
| **III. Write-Before-Notify** | ✅ Порядок `storage.append_rows` → `notifier.send_items` сохраняется во всех файлах. Рефакторинг чисто на «как сообщить наружу о фейле», не меняет внутренний control-flow. |
| **IV. Visibility Over Silence** | ✅ Рефакторинг **усиливает** visibility: ранее silent skip в `events`/`json`/`kinozal` становится exit-code-1 фейлом. Это прямое исполнение Principle IV. |
| **V. Root Cause Before Fix** | ✅ Root cause известен из PR #96 review: copy-paste anti-pattern. Фикс — устранить причину копирования (отсутствие явного return type на runner'е), а не симптом. Никаких shim'ов, retries, или try/except оборачиваний. |
| **VI. Fail-Fast Configuration** | ✅ `pipeline_config.validate_sources_config()` не трогается. Никаких новых config-форм. |

**Result**: PASS — переходим в Phase 0 без записи в Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/007-pipeline-result-ok/
├── plan.md              # This file
├── research.md          # Phase 0 — короткий, no unknowns
├── data-model.md        # Phase 1 — runner-contract diff на уровне сигнатур
├── quickstart.md        # Phase 1 — как воспроизвести success/failure scenario вручную
├── contracts/
│   └── runner-signatures.md  # Before/after сигнатуры всех 5 runner'ов
├── checklists/
│   └── requirements.md  # уже создан /speckit-specify
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

Все изменения — в существующих файлах. Структура проекта flat (CLI/batch), без `src/`:

```text
# Изменяемые файлы (production)
github_trending_pipeline.py     # удалить _FAILED/_did_fail/_reset_failure; run_*_pipeline -> list[PipelineResult]; __main__ читает .ok
steam_pipeline.py               # ↑ то же
events_pipeline.py              # run_events_pipeline -> list[PipelineResult]; __main__ exit(1) при фейле; новый код, не было раньше
json_pipeline.py                # run_json_pipeline -> PipelineResult (single); __main__ exit(1) при фейле
kinozal_pipeline.py             # run_kinozal_pipeline -> list[PipelineResult]; __main__ exit(1) при фейле

# Изменяемые файлы (тесты)
tests/test_github_trending_pipeline.py  # снять зависимость на _reset_failure
tests/test_steam_pipeline.py            # ↑
tests/test_events_pipeline.py           # +тест на возврат list[PipelineResult], фейл -> ok=False
tests/test_json_pipeline.py             # +тест на возврат PipelineResult, фейл -> ok=False
tests/test_kinozal_pipeline.py          # +тест на возврат list[PipelineResult], фейл -> ok=False

# Документация
docs/architecture/test-coverage.md      # автоматически обновляется ci_check.py через gen_test_coverage.py
docs/architecture/pipeline.md           # если упоминает _FAILED — заменить на PipelineResult.ok
```

**Structure Decision**: flat repository (Option 1 в template-е, без `src/`). Никаких новых директорий. PR содержит ровно 5 production-файлов, 5 test-файлов и (по необходимости) 1-2 doc-файла.

## Phase 0: Research

Нет открытых NEEDS CLARIFICATION в спеке. Все технические решения уже зафиксированы:

- **Возвращаемый тип** = существующий `PipelineResult` из `generic_pipeline.py` (lines 36-45). У него есть `errors: list[str]` и derived `@property ok`.
- **Multi vs single result**: `events`, `github_trending`, `kinozal`, `steam` обрабатывают N источников из `sources.json` → `list[PipelineResult]`. `json_pipeline.run_json_pipeline` обрабатывает один `source` dict → `PipelineResult` (single).
- **Агрегация ошибок**: каждый блок `try: _fetch_X ... except: logger.error(...); _FAILED = True; continue` в текущем коде превращается в `errors.append(str(exc))` локального `PipelineResult` для этого источника и `continue`. Уже существующие `extract_from_html` / `extract_from_json` возвращают `PipelineResult` — runner либо переиспользует его, либо строит новый при fetch-фейле.
- **`__main__` логика**: `results = run_*_pipeline(...)` → `if not all(r.ok for r in results): sys.exit(1)`. Для `json_pipeline` single result: `if not result.ok: sys.exit(1)`.

Все детали записаны в `research.md`.

## Phase 1: Design & Contracts

1. **`data-model.md`** — single entity, `PipelineResult` (уже есть). Документируем что добавляется в `errors` runner'ом (fetch errors, нулевой ranks list, пустой нормализованный набор и т.д.).
2. **`contracts/runner-signatures.md`** — before/after сигнатуры каждой `run_*_pipeline` функции и каждого `__main__` блока. Без фактических импортов / dataclass-синтаксиса — на уровне contract (input → return type → effect).
3. **`quickstart.md`** — как воспроизвести success path и failure path для каждого pipeline-файла вручную (dry-run + invalid sources.json).
4. **Agent context update**: в `CLAUDE.md` блок между `<!-- SPECKIT START -->` и `<!-- SPECKIT END -->` маркерами обновить ссылкой на `specs/007-pipeline-result-ok/plan.md`. Если маркеров нет — пропустить (см. Assumptions).

## Constitution Check (post-design re-eval)

После Phase 1 дизайна никаких новых принципов не задето. PASS, переход к `/speckit-tasks`.

## Complexity Tracking

Пусто — нет violations.

## Assumptions

- **Spec Kit bypass** для `setup-plan.sh` на Windows: plan.md, research.md, data-model.md, contracts/ создаются вручную ([[speckit-windows-python3-stub]]).
- **Agent context маркеры**: если в `CLAUDE.md` нет блока `<!-- SPECKIT START --> / <!-- SPECKIT END -->`, шаг агент-context update тихо пропускается. Это не блокирует `/speckit-tasks`.
- **`events_pipeline.run_events_pipeline`** сейчас не имеет return type. Меняется на `list[PipelineResult]` — это не break для существующих caller'ов (текущий `__main__` игнорирует возвращаемое значение).
- **`json_pipeline.run_json_pipeline`** обрабатывает 1 source за вызов. Возвращает `PipelineResult` (single), не `list`. Это асимметрия по дизайну — отражает фактический контракт функции.
- **Удаление `_did_fail` / `_reset_failure`** — публичные функции (без подчёркивания нет, но обе с `_`), не импортируются из других модулей (`grep -rn "_did_fail\|_reset_failure" .` подтвердит до удаления). Удаление безопасно.
