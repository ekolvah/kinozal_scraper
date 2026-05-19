# Runtime architecture

## Pipelines

| Entry point | Sources | Type | Schedule |
|---|---|---|---|
| `json_pipeline.py` | GitHub `new_popular` | JSON API | daily 04:00 UTC |
| `github_trending_pipeline.py` | GitHub trending | HTML scraping + Gemini | daily 04:00 UTC |
| `steam_pipeline.py` | Steam Most Played | JSON (Steam Charts + appdetails) | daily 04:00 UTC |
| `events_pipeline.py` | Soldout events | HTML scraping | daily 04:00 UTC |
| `kinozal_pipeline.py` | Kinozal movies | HTML scraping | daily 04:00 UTC |
| `telegram_summarizer.py` | Telegram channels | Gemini summarization | daily 04:00 UTC, `if: always()` |

All pipelines except `telegram_summarizer` follow the generic pipeline
pattern. `telegram_summarizer` uses `TelegramChannelSummarizer` (Telethon
reader + Gemini summarizer behind Protocols) and the shared `TelegramNotifier`
— see [Telethon-direct modules](#telethon-direct-modules) below.

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

## Telethon-direct modules

`TelegramChannelSummarizer.py`, `crypto.py`, and `telegram_summarizer.py`
use Telethon + Gemini directly rather than going through the generic
pipeline (sources.json → declarative extraction → Storage → Notifier).
The reason is the domain: they read live Telegram channels, decrypt a
Telethon session, and summarize free-form chat — none of which fits the
"fetch → extract → dedupe → notify" shape the other pipelines share.

They are nevertheless covered by the same quality gates: ruff format,
ruff lint, mypy, and dedicated tests (`test_telegram_summarizer.py`,
`test_crypto.py`). Model rotation is the same strategy as the generic
pipelines — see [gemini.md](gemini.md).
