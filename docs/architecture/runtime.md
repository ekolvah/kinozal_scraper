# Runtime architecture

## Pipelines

| Entry point | Sources | Type | Schedule |
|---|---|---|---|
| `json_pipeline.py` | GitHub trending, Steam top | JSON API | daily 04:00 UTC |
| `events_pipeline.py` | Soldout events | HTML scraping | daily 04:00 UTC |
| `kinozal_pipeline.py` | Kinozal movies | HTML scraping | daily 04:00 UTC |
| `telegram_summarizer.py` | Telegram channels | Gemini summarization | daily 04:00 UTC, `if: always()` |

All pipelines except `telegram_summarizer` follow the generic pipeline pattern.
`telegram_summarizer` is legacy code (excluded from linting) that uses
`TelegramChannelSummarizer` + Gemini directly.

## Protocols

Three boundaries isolate external services from business logic:

| Protocol | Prod implementation | Test double | Defined in |
|---|---|---|---|
| `Storage` | `SheetsStorage` | `InMemoryStorage` | `sheets_storage.py` |
| `Notifier` (implicit) | `TelegramNotifier` | `InMemoryNotifier` | `telegram_notifier.py` |
| `Enricher` | `RotatingGeminiEnricher` | `NullEnricher` | `gemini_enricher.py` |

## Data flow (generic pipelines)

```
sources.json
  → pipeline_config.py (macro expansion, schema validation)
    → fetch (HTTP — per-pipeline, not declarative)
      → generic_pipeline.py (extract_from_json / extract_from_html → NormalizedItem)
        → sheets_storage.get_existing_keys()  → dedupe
          → sheets_storage.append_rows()      [WRITE FIRST]
            → telegram_notifier.send_items()  [NOTIFY SECOND]
```

Write-before-notify ordering prevents duplicate Telegram notifications.
Details in [pipeline.md](pipeline.md).

## Configuration

- `sources.json` — declarative: URLs, CSS selectors, limits, templates, enrich prompts
- `pipeline_config.py` — loads config, expands macros (`{{TODAY}}`, `{{GITHUB_TOP_LIMIT}}`), validates
- Env vars override runtime behavior — full list in [ci.md](ci.md)

## Legacy modules

`TelegramChannelSummarizer.py`, `crypto.py`, `telegram_summarizer.py` are excluded
from ruff and mypy (see CLAUDE.md). They use Telethon + Gemini directly, not the
generic pipeline. Model rotation strategy shared with generic pipeline — see [gemini.md](gemini.md).
