# Data Model: PipelineResult.ok вместо _FAILED

## Entity: `PipelineResult`

**Location**: `generic_pipeline.py` (already exists, **no schema change**)

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `source_id` | `str` | runner | Идентификатор источника из `sources.json["sources"][i]["id"]` |
| `items` | `list[NormalizedItem]` | `extract_from_*` | Извлечённые из источника items (после нормализации, до дедупа) |
| `errors` | `list[str]` | `extract_from_*` + runner | Накопленные ошибки. Заполняется как `extract_from_*`, так и runner'ом (fetch fail, empty payload, post-enrichment пусто) |
| `warnings` | `list[str]` | `extract_from_*` | Non-fatal предупреждения (не используется напрямую в этом рефакторинге) |
| `ok` | `bool` (derived) | property | `not self.errors` |

### Что новое заполняет runner

До рефакторинга runner писал ошибки в `logger.error()` + `_FAILED = True`. После — то же самое, плюс `result.errors.append(<message>)` в локальный `PipelineResult` для этого источника. Список сообщений:

| Триггер | Message | Файл |
|---------|---------|------|
| `requests.get(...)` raise | `f"fetch failed: {exc}"` | github_trending, steam, events, json, kinozal |
| `extract_from_X` returned empty items | передаётся `result` as-is (его `errors` уже есть) | все 5 |
| Steam: `response.ranks` пуст/не list | `"empty 'response.ranks' in charts payload"` | steam |
| Steam: после enrichment пусто | `"no usable records after enrichment"` | steam |
| Kinozal: storage.get_existing_keys raise (если применимо) | `f"storage read failed: {exc}"` | kinozal — **только если** текущий код это ловит, иначе бросаем дальше |

### Lifecycle: один `PipelineResult` на один source

```
for source in enabled_sources:
    result = PipelineResult(source_id=source["id"])     # инициализация
    try: html_or_json = fetch(...)                       # сеть
    except Exception as exc:
        result.errors.append(f"fetch failed: {exc}")
        results.append(result); continue
    
    extracted = extract_from_X(html_or_json, source)     # PipelineResult с items/errors
    result.items = extracted.items
    result.errors.extend(extracted.errors)
    if not result.items and result.errors:
        results.append(result); continue
    
    # дедуп / enrich / store / notify
    ...
    results.append(result)
return results
```

Runner накапливает `results: list[PipelineResult]` и возвращает его. Каждый result уже содержит `source_id` своего источника — что упрощает диагностику.

## State Transitions

`PipelineResult.ok` — derived, не хранится. Состояние неявно:

- `errors == []` — успех источника
- `errors != []` — фейл; `items` может быть пустым (fetch fail) или непустым (часть извлечена, но было предупреждение)

Никаких enum-состояний, никакого state machine — это чистый result object.

## Out-of-scope (not modeled here)

- Объединение результатов между runner'ами (нет cross-pipeline отчёта).
- Persistence (errors в Sheets не пишутся — только в логи / stdout).
- Структурированный лог (errors остаются `list[str]`, не dict; structlog adoption — отдельный backlog).
