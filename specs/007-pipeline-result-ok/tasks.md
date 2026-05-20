---
description: "Task list for issue #97 — replace _FAILED globals with PipelineResult.ok"
---

# Tasks: PipelineResult.ok вместо _FAILED

**Input**: Design documents from `specs/007-pipeline-result-ok/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/runner-signatures.md ✅

**Tests**: MANDATORY (Constitution Principle I) — каждое поведенческое изменение получает falling test до implementation.

**Organization**: По user story из spec.md. US1 (P1) — exit-code surface для всех pipeline'ов. US2 (P1) — тесты независимы. US3 (P2) — anti-pattern удалён.

## Format: `[ID] [P?] [Story] Description`

- **[P]** — можно запускать параллельно (разные файлы, нет блокирующих зависимостей)
- **[Story]** — US1 / US2 / US3
- Каждая задача содержит точный путь к файлу

## Path Conventions

Flat repo, без `src/`. Production-файлы в repo root: `<name>_pipeline.py`. Тесты в `tests/test_<name>_pipeline.py`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Подготовка ветки и spec-артефактов. Большая часть уже выполнена `/speckit-specify` + `/speckit-plan`.

- [x] T001 Создана ветка `codex-issue-97-pipeline-result-ok` через `python scripts/new_branch.py`
- [x] T002 Создана структура `specs/007-pipeline-result-ok/` с spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md
- [x] T003 Обновлён `.specify/feature.json` и CLAUDE.md SPECKIT block

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Никаких блокирующих foundational tasks нет. `PipelineResult` уже существует в `generic_pipeline.py:36-45`. `extract_from_html` / `extract_from_json` уже его возвращают. Phase 2 пустая.

**Checkpoint**: Foundation ready (immediately) — user story работа стартует.

---

## Phase 3: User Story 1 — Cron-фейл одного источника поднимает exit code (Priority: P1) 🎯 MVP

**Goal**: При сбое любого источника в любом из 5 pipeline-файлов процесс возвращает exit code 1 + ERROR в логах. При полном успехе — exit code 0.

**Independent Test**: ручной запуск с битым `sources.json` (см. quickstart.md "Локальный failure path"). Автоматически — pytest на новые тесты.

### Tests for User Story 1 (write FIRST, MUST FAIL against current main)

- [ ] T010 [P] [US1] В `tests/test_events_pipeline.py` добавить тест `test_run_events_pipeline_returns_list_with_failed_result_on_fetch_error`. Тест монтирует `sources_config` с одним enabled events-source, через `monkeypatch.setattr(events_pipeline.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError("boom")))` (или эквивалент через requests-mock if уже в проекте — не вводить новых зависимостей). Утверждает: `results = run_events_pipeline(storage, notifier, sources_config=cfg); assert len(results) == 1; assert not results[0].ok; assert "fetch failed" in results[0].errors[0]`.
- [ ] T011 [P] [US1] В `tests/test_json_pipeline.py` добавить `test_run_json_pipeline_returns_not_ok_when_extraction_yields_no_items`. Подкладывает `source` с `json_path` ведущим в пустой массив (или невалидным URL через monkeypatch). Утверждает `result = run_json_pipeline(...); assert not result.ok`.
- [ ] T012 [P] [US1] В `tests/test_kinozal_pipeline.py` добавить `test_run_kinozal_pipeline_returns_failed_result_when_fetch_fails`. Аналогично — невалидный URL + monkeypatch. Утверждает `results = run_kinozal_pipeline(storage, notifier, youtube, sources_config=cfg); assert any(not r.ok for r in results)`.
- [ ] T013 [US1] Запустить `python -m pytest tests/test_events_pipeline.py tests/test_json_pipeline.py tests/test_kinozal_pipeline.py -v` и убедиться, что T010/T011/T012 **падают** (`AttributeError: 'NoneType' object has no attribute 'ok'` или эквивалент). Фиксирует "red" фазу TDD.

### Implementation for User Story 1

- [ ] T014 [P] [US1] Рефакторинг `events_pipeline.py`: изменить сигнатуру `def run_events_pipeline(...) -> list[PipelineResult]:`. Инициализировать `results: list[PipelineResult] = []` в начале. Для каждого источника создать `result = PipelineResult(source_id=source["id"])` перед fetch'ем. На fetch failure: `result.errors.append(f"fetch failed: {exc}")`, `results.append(result)`, `continue`. На extract failure: `result.items = extracted.items; result.errors.extend(extracted.errors)`. В конце цикла источника `results.append(result)`. `return results`. Обновить `__main__`: `results = run_events_pipeline(...); if any(not r.ok for r in results): sys.exit(1)`.
- [ ] T015 [P] [US1] Рефакторинг `json_pipeline.py`: `def run_json_pipeline(...) -> PipelineResult:`. Возвращать single `PipelineResult`. Обновить `__main__` (из `kinozal_pipeline.py` / однострочный wrapper если runner ожидает один source dict): `result = run_json_pipeline(...); if not result.ok: sys.exit(1)`. Если `__main__` сейчас не передаёт `source` explicitly — оставить логику источника как есть, добавить только exit-code propagation.
- [ ] T016 [P] [US1] Рефакторинг `kinozal_pipeline.py`: `def run_kinozal_pipeline(...) -> list[PipelineResult]:`. Та же схема, что T014. Учесть, что kinozal runner имеет дополнительный параметр `youtube: Youtube` — сигнатура остальных параметров не меняется. Обновить `__main__`.
- [ ] T017 [US1] Запустить `python -m pytest tests/test_events_pipeline.py tests/test_json_pipeline.py tests/test_kinozal_pipeline.py -v` и убедиться, что **все тесты зелёные**, включая T010/T011/T012 (green фаза TDD).

**Checkpoint US1**: ✅ Все 3 ранее silent-skip pipeline'а теперь surface'ят фейл через exit code 1. spec FR-001, FR-003, FR-008 выполнены для events/json/kinozal.

---

## Phase 4: User Story 2 — Тесты pipeline'ов независимы (Priority: P1)

**Goal**: Удалить `_FAILED`/`_did_fail`/`_reset_failure` из `github_trending_pipeline.py` и `steam_pipeline.py`, обновить соответствующие тесты так, чтобы они проверяли возвращаемый `PipelineResult`, а не модульный глобал.

**Independent Test**: `pytest tests/test_github_trending_pipeline.py tests/test_steam_pipeline.py -v` зелёный, `grep -rn "_did_fail\|_reset_failure" --include="*.py" .` пусто.

### Implementation for User Story 2

US2 — это атомарное изменение «удалить anti-pattern» в каждом из двух файлов. Тест и production-код одного файла идут одним коммитом, потому что тесты импортируют `_did_fail` который удаляется (тест не может failed, потом passed — он сначала не компилируется без обновления).

- [ ] T018 [P] [US2] В `github_trending_pipeline.py` удалить `_FAILED = False`, `def _did_fail()`, `def _reset_failure()`. Сигнатура `def run_github_trending_pipeline(...) -> list[PipelineResult]:`. Заменить `global _FAILED; _reset_failure()` → инициализация локального `results: list[PipelineResult] = []`. Каждое `_FAILED = True` → `result.errors.append(...)` локального `PipelineResult` + `results.append(result)` + `continue`. `__main__`: `if any(not r.ok for r in results): sys.exit(1)`. В `tests/test_github_trending_pipeline.py` удалить `_did_fail` из импортов (lines 12), `self.assertTrue(_did_fail())` (line 162) заменить на `self.assertTrue(any(not r.ok for r in self.<results>))` или эквивалент через возвращаемое значение runner'а.
- [ ] T019 [P] [US2] В `steam_pipeline.py` те же изменения, что T018. В `tests/test_steam_pipeline.py` удалить `_did_fail` из импортов (lines 9), все `self.assertTrue(_did_fail())` / `self.assertFalse(_did_fail())` (lines 281, 288, 292) заменить на assertion'ы по возвращённому значению.
- [ ] T020 [US2] Запустить `python -m pytest tests/test_github_trending_pipeline.py tests/test_steam_pipeline.py -v` — все тесты зелёные.
- [ ] T021 [US2] Запустить `python -m pytest tests/test_github_trending_pipeline.py tests/test_steam_pipeline.py --random-order` (если плагин установлен) или повторно запустить тесты в обратном порядке (`pytest tests/test_steam_pipeline.py tests/test_github_trending_pipeline.py`) — все зелёные. Подтверждает независимость от порядка.

**Checkpoint US2**: ✅ Module-level `_FAILED` anti-pattern удалён. Тесты не зависят от serial reset.

---

## Phase 5: User Story 3 — Новый pipeline-файл не размножает anti-pattern (Priority: P2)

**Goal**: Гарантировать, что после рефакторинга в репозитории нет следов anti-pattern, который кто-то скопирует в новый файл.

**Independent Test**: `grep -rn "_FAILED\|_did_fail\|_reset_failure" --include="*.py" .` возвращает 0 строк.

### Implementation for User Story 3

- [ ] T022 [US3] Выполнить `grep -rn "_FAILED\|_did_fail\|_reset_failure" --include="*.py" .`. Ожидается пусто. Если что-то осталось (комментарий с историческим референсом и т.п.) — удалить.
- [ ] T023 [US3] Выполнить `python -m mypy events_pipeline.py json_pipeline.py kinozal_pipeline.py github_trending_pipeline.py steam_pipeline.py` (или просто `python -m mypy .` если так настроено в проекте). Все pipeline-файлы type-check без ошибок. Никаких `# type: ignore` добавлять не нужно.
- [ ] T024 [US3] Проверить `docs/architecture/pipeline.md` (если есть упоминания `_FAILED`) — заменить на абзац про `PipelineResult` return contract или ссылку на `specs/007-pipeline-result-ok/contracts/runner-signatures.md`. Если упоминаний нет — пропустить.

**Checkpoint US3**: ✅ Anti-pattern удалён, нет следов в production-коде, mypy зелёный, документация (если требовалось) обновлена.

---

## Phase 6: Polish & Cross-Cutting (release readiness)

- [ ] T025 Запустить `python scripts/ci_check.py`. Должен пройти полностью (ruff format, ruff lint, pytest, mypy, requirements drift, coverage doc). При фейле `gen_test_coverage.py` step'а — `git add docs/architecture/test-coverage.md` и перезапуск.
- [ ] T026 Запустить `git status` и убедиться, что нет нежелательных файлов (например, `__pycache__/` уже в `.gitignore`). Сделать `git add` точечно: production pipeline files, test files, `specs/007-pipeline-result-ok/`, `CLAUDE.md`, `.specify/feature.json`, потенциально `docs/architecture/*.md`.
- [ ] T027 Сделать коммит: `git commit -m "refactor: PipelineResult.ok вместо _FAILED глобала (closes #97)"` с подробным телом, описывающим scope (5 файлов) и observable changes (events/json/kinozal теперь surface exit-code).
- [ ] T028 `git push -u origin codex-issue-97-pipeline-result-ok`. Pre-push hook прогонит `ci_check.py` ещё раз.
- [ ] T029 Открыть PR: `gh pr create --title "refactor: PipelineResult.ok вместо _FAILED глобала" --body "Closes #97. ..."`. PR описание ссылается на `specs/007-pipeline-result-ok/spec.md` и перечисляет затронутые принципы констатуции (IV. Visibility Over Silence — усиление; V. Root Cause — anti-pattern удалён). **Не мержить** PR (см. [[pr-merge]]).

---

## Dependencies

```text
T001-T003 (Phase 1 setup) ─ already done
   ↓
Phase 2 (Foundational) — empty
   ↓
Phase 3 [US1]:
  T010 [P], T011 [P], T012 [P] (failing tests)
    ↓
  T013 (verify red)
    ↓
  T014 [P], T015 [P], T016 [P] (impl)
    ↓
  T017 (verify green)
   ↓
Phase 4 [US2]:
  T018 [P], T019 [P]
    ↓
  T020, T021 (verify green + order-independent)
   ↓
Phase 5 [US3]:
  T022, T023, T024
   ↓
Phase 6:
  T025 → T026 → T027 → T028 → T029
```

## Parallel Execution Examples

**Phase 3 tests (T010-T012)**: writing tests for 3 different test files — fully parallel.

**Phase 3 impl (T014-T016)**: refactoring 3 different production files — fully parallel.

**Phase 4 (T018, T019)**: refactoring 2 different file pairs (github_trending + its test, steam + its test) — parallel.

US2 cannot start until US1 is green (T017), because removing `_FAILED` requires the new return-value pattern to be in place across all files for consistency.

US3 cannot start until US2 is green (T021), because T022 grep depends on T018/T019 completion.

## Implementation Strategy

**MVP scope** = US1 alone (Phase 3). Это устранение silent-skip баги в трёх «тихих» pipeline'ах. Без US2/US3 PR ещё ценен, но содержит расхождение (`_FAILED` в двух файлах + новый pattern в трёх). Поэтому **в этом PR делаем все три US**, не выпускаем US1 separately — иначе нарушим «один PR — одна логическая единица» как минимум стилистически.

**Incremental delivery внутри PR**: коммиты могут быть по US (3 коммита: feat US1, refactor US2, polish US3) или одним коммитом — на усмотрение. Pre-push hook гонит ci_check на финальное состояние; ветка пушится одним push'ем.

---

## Format validation

Все T010-T029 соответствуют формату: `- [ ] T<NNN> [P?] [Story?] <description with file path>`. Setup-задачи (T001-T003) без story label (отмечены `[x]` потому что уже сделаны). Polish-задачи (T025-T029) без story label.
