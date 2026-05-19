# Phase 0 Research: PipelineResult.ok вместо _FAILED

Никаких NEEDS CLARIFICATION в спеке нет. Этот документ фиксирует уже принятые решения и отвергнутые альтернативы — для аудита code-review'ом.

## Decision 1: Переиспользовать существующий `PipelineResult`

**Decision**: возвращаемый тип runner'ов = `PipelineResult` (single) или `list[PipelineResult]` (multi), классы уже определены в `generic_pipeline.py:36-45`.

**Rationale**: `PipelineResult` уже имеет `source_id`, `items`, `errors`, `warnings` и derived `@property ok`. `extract_from_html` / `extract_from_json` уже его возвращают; runner'ы пользуются им локально, но не пробрасывают наружу. Достаточно стандартизировать пробрасывание.

**Alternatives considered**:
- *Создать новый dataclass `RunStatus`*: отвергнуто — дублирование. `PipelineResult` уже несёт всю нужную информацию.
- *Использовать `bool` или `tuple[bool, list[str]]`*: отвергнуто — мы только что вышли из anti-pattern с глобалом, регресс в untyped tuple — шаг назад.
- *Поднимать exception из runner'а*: отвергнуто — фейл одного источника не должен прерывать обработку остальных. Текущее поведение (продолжать после фейла) корректно и должно сохраниться (FR-008 спеки).

## Decision 2: Multi vs single result

**Decision**:
- `run_github_trending_pipeline`, `run_steam_pipeline`, `run_events_pipeline`, `run_kinozal_pipeline` → `list[PipelineResult]`.
- `run_json_pipeline` → `PipelineResult` (single).

**Rationale**: первые четыре runner'а итерируют `[s for s in config["sources"] if ...]` — N источников за вызов. `run_json_pipeline(storage, notifier, source, ...)` принимает одиночный `source` dict (на это указывают существующая сигнатура и тесты).

**Alternatives considered**:
- *Унификация всех на `list[PipelineResult]`*: отвергнуто. `run_json_pipeline` имеет другой layer (вызывается извне на конкретный source); искусственное превращение в `[result]` создаст confusion при чтении кода.

## Decision 3: Что записывать в `PipelineResult.errors` runner'ом

**Decision**: runner добавляет в `errors` локального `PipelineResult` человекочитаемые сообщения для случаев, когда соответствующий блок текущего кода ставил `_FAILED = True`:

1. `fetch failed: <exc>` — сетевая ошибка.
2. `extraction errors: <result.errors>` — `extract_from_X` вернул 0 items и есть errors.
3. `no usable records after enrichment` — Steam: после `_enrich_with_appdetails` пусто.
4. `empty 'response.ranks' in charts payload` — Steam: невалидный payload.

Если `extract_from_*` уже вернул `result` с `result.errors` непустым — runner НЕ дублирует записи, использует существующий `result`.

**Rationale**: 1:1 mapping с текущими `logger.error` + `_FAILED = True` callsite'ами. Гарантирует, что наблюдаемое поведение (что именно красным горит) не меняется, кроме факта exit code'а.

**Alternatives considered**:
- *Включать stack trace*: отвергнуто. Логгер уже пишет полный exc; `errors` это user-facing summary.

## Decision 4: Поведение `__main__` блоков

**Decision**: универсальный pattern для всех 5 файлов:

```
results = run_X_pipeline(...)  # list или single
if isinstance(results, list):
    failed = [r for r in results if not r.ok]
else:
    failed = [] if results.ok else [results]
if failed:
    sys.exit(1)
```

Для каждого файла этот блок — 4-5 строк, без хелпера в `generic_pipeline.py` (нет смысла абстрагировать ради 5 копий по 5 строк, это в out-of-scope из issue #97; см. также Decision 5).

**Rationale**: явно, читабельно, не требует import'а нового хелпера. Соответствует существующему стилю проекта.

**Alternatives considered**:
- *Хелпер `exit_on_failure(results)` в `generic_pipeline.py`*: отвергнуто. issue #97 явно выделяет «unify `__main__` boilerplate» в out-of-scope как отдельный candidate. Не смешиваем scope.

## Decision 5: `_did_fail` / `_reset_failure` удаляем полностью

**Decision**: `grep -rn "_did_fail\|_reset_failure" --include="*.py" .` показывает callsite'ы ровно в 4 файлах: 2 production (`github_trending_pipeline.py`, `steam_pipeline.py`) + 2 test (`tests/test_github_trending_pipeline.py`, `tests/test_steam_pipeline.py`). Все 4 будут обновлены.

`_FAILED` glob — то же самое: только production-файлы.

**Rationale**: нет external caller'ов. Чистый surgical removal.

## Decision 6: Тесты на новое exit-1 поведение для events/json/kinozal

**Decision**: для каждого из 3 ранее silent-skip pipeline'ов добавляется хотя бы один тест:

- `tests/test_events_pipeline.py` — `test_returns_failed_result_when_source_unreachable`
- `tests/test_json_pipeline.py` — `test_returns_not_ok_when_extraction_fails`
- `tests/test_kinozal_pipeline.py` — `test_returns_failed_result_when_fetch_fails`

Каждый тест:
1. Подкладывает `sources_config` с невалидным URL / битым payload.
2. Вызывает `run_*_pipeline(...)` напрямую.
3. Проверяет, что возвращённый `PipelineResult.ok is False` (или `any(not r.ok for r in results)`).
4. Не вводит mock на `requests`/HTTP — использует Protocol-double стратегию из существующих тестов (например, фикстура с raise'ом из storage/notifier, или невалидный sources_config, который сам триггерит extract-failure).

**Rationale**: Principle I + покрытие новой функциональности (raising exit-1 в файлах, где раньше его не было).

**Alternatives considered**:
- *Только обновить старые тесты*: отвергнуто. Новое поведение (exit 1 в events/json/kinozal) — это новая функциональность, требует **новый** тест в каждом из 3 файлов.

## Decision 7: Type hints

**Decision**: явный return type в сигнатуре каждого runner'а:

- `def run_github_trending_pipeline(...) -> list[PipelineResult]:`
- `def run_steam_pipeline(...) -> list[PipelineResult]:`
- `def run_events_pipeline(...) -> list[PipelineResult]:`
- `def run_kinozal_pipeline(...) -> list[PipelineResult]:`
- `def run_json_pipeline(...) -> PipelineResult:`

**Rationale**: mypy в ci_check ловит регресс типа. SC-002 спеки явно требует type-checked сигнатуру.
