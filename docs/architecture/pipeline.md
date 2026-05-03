# Pipeline architecture

## Layers

```
sources.json          declarative config (urls, selectors, limits, macros)
pipeline_config.py    loads config, expands macros, validates schema
generic_pipeline.py   extract + normalize (no network, no I/O)
sheets_storage.py     read existing keys, append confirmed rows
telegram_notifier.py  send items, return confirmed list  [issue #4]
scraper.py            legacy runtime — untouched until issue #5
```

## Key principle: new source = config, not code

Adding GitHub, Steam, or any future source requires only a new entry in
`sources.json`. No new Python class, no new extractor.

## Data flow (issues #2–#5)

```
load_sources_config()
  → for each enabled source:
      fetch payload (HTTP, done by caller)
      extract_from_json / extract_from_html  → PipelineResult
      storage.get_existing_keys(sheet_tab)   → set[str]
      filter new items (dedupe_key not in existing keys)
      notifier.send(new_items)               → confirmed_items
      storage.append_rows(sheet_tab, [item.to_row() for item in confirmed_items])
```

## NormalizedItem

Defined in `generic_pipeline.py`. All pipeline stages pass this type.

```python
@dataclass
class NormalizedItem:
    dedupe_key: str      # unique key for deduplication (required)
    title: str           # display title (required)
    source_id: str       # matches sources.json id
    url: str
    description: str
    metric: str
    image_url: str
    raw: dict            # original record for debugging
```

Row serialization: `item.to_row()` → `[dedupe_key, title, url, metric, source_id, notified_at]`
Headers constant: `ROW_HEADERS` in `generic_pipeline.py`

## extract_from_* contracts

- Take in-memory payload (list of dicts for JSON, HTML string for HTML)
- Return `PipelineResult(items, errors, warnings)`
- Zero items extracted → `errors` entry (quality failure)
- Missing `dedupe_key` or `title` on a record → `errors` entry, item skipped
- Never raise for data quality issues — caller decides what to do

## HTML source config

HTML sources require `row_selector` in source config (not in `fields`).
Field selectors use `css@attr` syntax to extract attributes.

## Macro expansion

Handled by `pipeline_config.py` before the pipeline runs.
Supported macros: `{{TODAY}}`, `{{DATE_MINUS_7_DAYS}}`, `{{GITHUB_TOP_LIMIT}}`, `{{STEAM_TOP_LIMIT}}`.
