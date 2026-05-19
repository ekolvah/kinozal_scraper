# Feature Specification: PipelineResult.ok вместо module-level _FAILED

**Feature Branch**: `codex-issue-97-pipeline-result-ok`

**Created**: 2026-05-19

**Status**: Draft

**Input**: GitHub issue #97 — «Refactor: replace module-level _FAILED globals with PipelineResult.ok across pipelines»

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Cron-фейл одного источника поднимает exit code пайплайна (Priority: P1)

Оператор запускает любой pipeline-файл (`github_trending_pipeline.py`, `steam_pipeline.py`, `events_pipeline.py`, `kinozal_pipeline.py`, `json_pipeline.py`) в GitHub Actions по расписанию. Если хотя бы один настроенный источник внутри пайплайна не смог загрузить/распарсить/нормализовать данные — шаг workflow завершается с ненулевым exit code, GitHub Actions помечает запуск красным, оператор получает уведомление о деградации.

**Why this priority**: единственная наблюдаемая пользователем гарантия, что «тихие» сбои не накапливаются. Сейчас два из пяти файлов (`github_trending_pipeline`, `steam_pipeline`) уже так делают через `_FAILED`, остальные три (`events`, `json`, `kinozal`) — silent skip при ошибке источника, что нарушает [[feedback_visibility_over_silence]].

**Independent Test**: запустить любой `*_pipeline.py` с заведомо битым `sources.json` (URL 404 / пустой ответ) и убедиться, что процесс возвращает exit code 1 и логирует ошибку с `source_id`. Запуск со всеми работающими источниками возвращает exit code 0.

**Acceptance Scenarios**:

1. **Given** один источник в `sources.json` падает с network error, **When** запускается `python <pipeline>.py`, **Then** процесс завершается с exit code 1 и в stderr/логах есть запись `ERROR ... [<source_id>] ...`.
2. **Given** все источники успешно отрабатывают, **When** запускается `python <pipeline>.py`, **Then** процесс завершается с exit code 0.
3. **Given** в pipeline 3 источника и падает только средний, **When** запускается процесс, **Then** оставшиеся 2 источника обрабатываются полностью, items отправляются в Telegram, в конце процесс выходит с code 1 — фейл одного источника не блокирует остальные, но overall статус красный.

---

### User Story 2 — Тесты pipeline'ов независимы и не текут друг в друга (Priority: P1)

Разработчик пишет/добавляет тест на pipeline. Тест должен видеть результат пайплайна как возвращаемое значение функции, а не как побочный эффект (изменение модульной переменной). Порядок запуска тестов не влияет на результат — каждый тест видит чистое состояние.

**Why this priority**: текущая схема с `_FAILED` глобалом + `_reset_failure()` хелпером — anti-pattern. `test_successful_run_does_not_mark_failure` сейчас зелёный потому что `run_X_pipeline` вызывает `_reset_failure()` в начале; если в будущем reset уберут или поменяют порядок — тест начнёт давать ложные pass'ы. Параллельный pytest (`-n auto`) на этом сломается.

**Independent Test**: запустить тестовый файл повторно (`pytest tests/test_steam_pipeline.py tests/test_steam_pipeline.py`) — все тесты должны проходить оба раза; запустить через `pytest --random-order` — должны проходить в любом порядке; параллельный запуск (`pytest -n 4`) не должен давать flaky.

**Acceptance Scenarios**:

1. **Given** тест пишет «runner вернул ok=True для всех источников», **When** этот тест выполняется после теста с фейлом, **Then** утверждение видит `PipelineResult` от своего вызова, а не остаточное состояние.
2. **Given** в `tests/` нет вызовов `_reset_failure()` ни в setup/teardown, ни в самих тестах, **When** запускается `pytest tests/`, **Then** все тесты зелёные.

---

### User Story 3 — Добавление нового pipeline-файла не размножает anti-pattern (Priority: P2)

Разработчик создаёт новый `*_pipeline.py` (например, для нового источника контента). Шаблон, на который он смотрит, — это уже отрефакторенный `events_pipeline.py` / `json_pipeline.py`. Он копирует `PipelineResult`-стиль, не копирует `_FAILED`.

**Why this priority**: основная причина рефакторинга в issue #97 («не размножать паттерн в третий раз»). Должно быть закреплено в коде, не в инструкции.

**Independent Test**: `grep -rn "_FAILED\|_did_fail\|_reset_failure" *.py` после рефакторинга — не должно ничего найти за пределами комментариев истории (если такие останутся в `docs/`).

**Acceptance Scenarios**:

1. **Given** разработчик читает любой `*_pipeline.py` чтобы понять, как сообщать фейл, **When** он смотрит на `run_*_pipeline` сигнатуру, **Then** возвращаемый тип явно говорит `PipelineResult` (или `list[PipelineResult]`), без чтения комментариев или подсказок.

---

### Edge Cases

- **Multi-source pipeline частично упал** (см. сценарий 3 выше): exit code = 1, но успешные items уже разосланы в Telegram. Это сохраняет текущее поведение `github_trending_pipeline` / `steam_pipeline` — не регресс.
- **Pipeline вызывается из in-process кода (не `__main__`)**: возвращаемое значение позволяет caller'у решить, что делать с ошибкой, вместо чтения module-level глобала. Сейчас такого caller'а нет, но рефакторинг убирает барьер для появления.
- **`run_kinozal_pipeline` имеет нестандартную сигнатуру** (требует `youtube: Youtube` для трейлеров). Возвращаемый контракт должен быть тем же `PipelineResult` / `list[PipelineResult]`, что и у остальных runner'ов.
- **`run_json_pipeline` обрабатывает один источник за вызов** (вызывается извне с `sources_config`). Возвращает `PipelineResult` (single), не list.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Каждая функция `run_*_pipeline` MUST возвращать `PipelineResult` (один источник) или `list[PipelineResult]` (несколько источников). Тип возвращаемого значения зафиксирован в сигнатуре.
- **FR-002**: `PipelineResult.errors` непустой ⇔ источник упал; `PipelineResult.ok` есть `not errors` (контракт уже определён в `generic_pipeline.py`).
- **FR-003**: Блок `if __name__ == "__main__"` каждого pipeline-файла MUST вызывать `sys.exit(1)`, если хотя бы один из возвращённых `PipelineResult` имеет `.ok is False`. Если все ok — `sys.exit(0)` (по умолчанию).
- **FR-004**: Модульные переменные `_FAILED`, функции `_did_fail()`, `_reset_failure()` MUST быть удалены из `github_trending_pipeline.py` и `steam_pipeline.py`. Никакого нового модульного mutable state не вводится.
- **FR-005**: Существующие тесты `_reset_failure`-зависимости MUST быть перенесены на assertion по возвращённому `PipelineResult` (не по module-level state).
- **FR-006**: Каждый pipeline-файл MUST логировать ошибки источников через `logger.error("[%s] ...", source_id, ...)` и накапливать сообщение в `PipelineResult.errors` — у оператора остаётся та же видимость в cron-логах.
- **FR-007**: Изменение MUST быть scope-чистым: не трогает `Storage`, `Notifier`, `Enricher` Protocol; не унифицирует `__main__` boilerplate между файлами (это отдельный backlog).
- **FR-008**: Существующее observable поведение `github_trending` и `steam` (частичный фейл → exit 1, успешные источники отправляются полностью) MUST сохраниться. Файлы `events`, `json`, `kinozal` MUST приобрести то же поведение — это устранение silent-skip баги, не регресс.

### Key Entities

- **`PipelineResult`** (существует в `generic_pipeline.py`): `source_id`, `items`, `errors`, `warnings`, derived `ok`. Это единственная сущность, через которую runner сообщает о результате — после рефакторинга других каналов сигнала из runner'а наружу нет.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `grep -rn "^_FAILED\b\|def _did_fail\|def _reset_failure" *.py` возвращает 0 строк в production-коде.
- **SC-002**: Все 5 pipeline-файлов (`github_trending`, `steam`, `events`, `json`, `kinozal`) имеют функцию `run_*_pipeline` с явным возвращаемым типом `PipelineResult` или `list[PipelineResult]` (type-checked mypy).
- **SC-003**: При запуске `pytest tests/` все тесты зелёные. Тесты, ранее зависевшие от `_reset_failure()`, после рефакторинга проверяют возвращаемый `PipelineResult`, и их повторный запуск в обратном порядке (`pytest --random-order` или ручная перестановка) не меняет результат.
- **SC-004**: Для каждого из 3 ранее silent-skip pipeline'ов (`events`, `json`, `kinozal`) добавлен хотя бы один тест, который доказывает: при падении источника `PipelineResult.ok is False` и `__main__` блок поднимет exit(1). Тест не вводит mock на внешние API (см. `docs/architecture/testing.md`) — использует Protocol doubles или фикстуру `sources_config` с заведомо невалидным URL/payload.
- **SC-005**: `ci_check.py` проходит локально без правок самого `ci_check.py` (т.е. рефакторинг не нарушает lint/format/mypy/coverage-doc).

## Assumptions

- **Spec Kit Windows-bypass**: spec.md и каталог `specs/007-*` созданы вручную, без `setup-plan.sh` / `before_specify` хука — см. [[speckit-windows-python3-stub]]. Это не влияет на содержание спеки.
- **Ветка создана через `python scripts/new_branch.py codex-issue-97-pipeline-result-ok`**, а не через `speckit.git.feature` — соответствие [[CLAUDE.md]] convention `codex-issue-N-*` важнее, чем spec-kit numbering, потому что CI триггерится именно на `codex-*`.
- **`run_kinozal_pipeline` обрабатывает 1+ kinozal-источников из `sources.json`** — возвращаемое значение `list[PipelineResult]` (даже если в продакшене источник один — для единообразия с `events`/`github_trending`/`steam`, у которых тоже возможен мульти-source).
- **`run_json_pipeline` обрабатывает 1 источник за вызов** (вызывается извне с конкретным `source` dict) — возвращает `PipelineResult` (single).
- **PR scope** — все 5 файлов + соответствующие тесты в одном PR. Это рефакторинг, искусственное дробление по файлам создаст невалидные промежуточные состояния (один runner возвращает `PipelineResult`, другой ещё через глобал — `__main__` boilerplate в каждом файле инфер'ит exit code из разных источников). См. [[CLAUDE.md]] правило «один PR — одна логическая единица»: эта единица == «вычистить _FAILED anti-pattern».
- **Documentation**: после рефакторинга обновляется `docs/architecture/test-coverage.md` через `scripts/gen_test_coverage.py` (этот шаг автоматически делает `ci_check.py`); раздел `docs/architecture/pipeline.md` может потребовать абзаца о возвращаемом контракте — добавляется в этом же PR, если есть упоминание `_FAILED`.
