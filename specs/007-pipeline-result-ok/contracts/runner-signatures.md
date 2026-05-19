# Contracts: Runner signatures (before/after)

Каждый pipeline-файл имеет одну public runner-функцию и один `__main__` блок. Документ фиксирует сигнатурный diff — для review/mypy gate.

## `github_trending_pipeline.py`

### Before

```
_FAILED: bool = False
def _did_fail() -> bool: ...
def _reset_failure() -> None: ...

def run_github_trending_pipeline(
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None = None,
    sources_config: dict[str, Any] | None = None,
) -> None:
    ...
    if <fetch fail>:
        _FAILED = True
    ...

# __main__:
run_github_trending_pipeline(...)
if _did_fail():
    sys.exit(1)
```

### After

```
def run_github_trending_pipeline(
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None = None,
    sources_config: dict[str, Any] | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []
    for source in trending_sources:
        result = PipelineResult(source_id=source["id"])
        ...
        if <fetch fail>:
            result.errors.append(f"fetch failed: {exc}")
            results.append(result)
            continue
        ...
        results.append(result)
    return results

# __main__:
results = run_github_trending_pipeline(...)
if any(not r.ok for r in results):
    sys.exit(1)
```

**Removed**: `_FAILED`, `_did_fail()`, `_reset_failure()`, `global _FAILED` использования.

## `steam_pipeline.py`

Аналогично `github_trending_pipeline.py`. Возвращаемый тип `list[PipelineResult]`. Удаляются `_FAILED`, `_did_fail`, `_reset_failure`.

## `events_pipeline.py`

### Before

```
def run_events_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> None:
    ...  # silent skip on fetch error

# __main__:
run_events_pipeline(...)  # no exit code propagation
```

### After

```
def run_events_pipeline(...) -> list[PipelineResult]:
    ...
    return results

# __main__:
results = run_events_pipeline(...)
if any(not r.ok for r in results):
    sys.exit(1)
```

## `json_pipeline.py`

### Before

```
def run_json_pipeline(
    storage: Storage,
    notifier: Notifier,
    source: dict[str, Any] | None = None,  # single source
    enricher: Enricher | None = None,
) -> None: ...

# __main__:
run_json_pipeline(...)  # no exit propagation
```

### After

```
def run_json_pipeline(...) -> PipelineResult:
    result = PipelineResult(source_id=source["id"])
    try: ...
    except: result.errors.append(...)
    return result

# __main__:
result = run_json_pipeline(...)
if not result.ok:
    sys.exit(1)
```

**Note**: одиночный `PipelineResult`, не list. См. research.md Decision 2.

## `kinozal_pipeline.py`

### Before

```
def run_kinozal_pipeline(
    storage: Storage,
    notifier: Notifier,
    youtube: Youtube,
    sources_config: dict[str, Any] | None = None,
) -> None: ...

# __main__:
run_kinozal_pipeline(...)  # no exit propagation
```

### After

```
def run_kinozal_pipeline(...) -> list[PipelineResult]: ...

# __main__:
results = run_kinozal_pipeline(...)
if any(not r.ok for r in results):
    sys.exit(1)
```

## Test contract changes

### `tests/test_github_trending_pipeline.py`, `tests/test_steam_pipeline.py`

Удалить импорт `_did_fail` / `_reset_failure`. Все `self.assertTrue(_did_fail())` → `self.assertTrue(any(not r.ok for r in results))` (или эквивалент по контексту).

### `tests/test_events_pipeline.py`, `tests/test_json_pipeline.py`, `tests/test_kinozal_pipeline.py`

Добавить хотя бы один тест:
- runner вызывается с заведомо невалидным `sources_config` (URL = `"http://invalid.localhost/"` + monkeypatch на `requests.get` чтобы кинул `ConnectionError`, **либо** sources_config без URL).
- Тест assertit `not result.ok` (single) или `any(not r.ok for r in results)` (multi).

## mypy contract

После рефакторинга `mypy .` (что бежит из `ci_check.py`) MUST type-check pipeline-файлы без ошибок. Новых `# type: ignore` не добавлять.
