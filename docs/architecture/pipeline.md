# Pipeline architecture

## Layers

```
sources.json          declarative config (urls, selectors, limits, macros)
pipeline_config.py    loads config, expands macros, validates schema
generic_pipeline.py   extract + normalize (no network, no I/O)
sheets_storage.py     read existing keys, append confirmed rows
telegram_notifier.py  send items, return confirmed list
```

## Key principle: new source = config, not code (with known limitation)

Adding GitHub, Steam, or any future source requires only a new entry in
`sources.json` for extraction and normalization. No new Python class needed.

**Known limitation:** HTTP fetching (pagination, auth headers, rate limits) is
not declarative. Each new source requires a small fetch function in the caller.

## Data flow

For the full list of pipelines and how they connect, see [runtime.md](runtime.md).

**Delivery state is intentional: Sheets rows represent confirmed delivery.**
Pipelines that can partition notifier results write only successfully sent
items to Sheets. If delivery fails for any item, the step must surface a
non-ok result and exit non-zero instead of silently looking like "no news."

```
load_sources_config()
  → for each enabled source:
      fetch payload (HTTP, done by caller)
      extract_from_json / extract_from_html  → PipelineResult
      storage.get_existing_keys(sheet_tab)   → set[str]  ← raises SchemaError on mismatch
      new_items = [i for i if i.dedupe_key not in existing_keys]
      sent, failed = notifier.send(new_items)
      storage.append_rows(sheet_tab, [i.to_row() for i in sent])
      failed notifications -> PipelineResult.errors
```

## Error policy

`PipelineResult` carries `errors` and `warnings`; production callers exit
non-zero when any result is not ok. Notification delivery failures are errors,
not warnings, because users must receive either data or a failure signal — never silence.
Future: `on_error: skip_item | fail_source` field in `sources.json` — deferred
to issues #6/#7 when sources become real.

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
    trailer_url: str     # enriched by caller; not stored in Sheets
    raw: dict            # original record for debugging
```

Row serialization: `item.to_row()` → `[dedupe_key, title, url, metric, source_id, notified_at]`
Headers constant: `ROW_HEADERS` in `generic_pipeline.py`

## Notification templates

`build_notification(item, template)` in `generic_pipeline.py` renders the
Telegram HTML message. Available template variables:

| Variable | Content |
|---|---|
| `{title}` | plain escaped title |
| `{title_link}` | `<a href="{url}">{title}</a>` — clickable title linking to the source page |
| `{url}` | raw URL of the item page |
| `{trailer_url}` | raw YouTube trailer URL |
| `{trailer_link}` | `<a href="{trailer_url}">Trailer</a>` — clickable "Trailer" word; empty if no trailer |
| `{description}` | plain escaped description |
| `{metric}` | numeric metric (stars, players, etc.) |
| `{image_url}` | raw image URL |
| any key from `item.raw` | e.g. `{summary_ru}` for GitHub sources, `{description_ru}` for Steam (see [gemini.md](gemini.md)) |

**Kinozal template** (`sources.json`):
```
{title_link}\n{trailer_link}
```
Renders as: clickable film title → kinozal page, then "Trailer" → YouTube.

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
