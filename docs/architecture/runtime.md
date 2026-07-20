# Runtime architecture

## Pipelines

| Entry point | Sources | Type | Schedule |
|---|---|---|---|
| `github_popular_pipeline.py` | GitHub `new_popular` | JSON API | daily |
| `github_trending_pipeline.py` | GitHub trending | HTML scraping + Gemini | daily |
| `steam_pipeline.py` | Steam Most Played | JSON (Steam Charts + appdetails) | daily |
| `soldout_pipeline.py` | Soldout events | HTML scraping | daily |
| `kinozal_pipeline.py` | Kinozal movies | HTML scraping | daily |
| `telegram_summarizer.py` | Telegram channels | Gemini summarization | daily, `if: always()` |

All pipelines except `telegram_summarizer` follow the generic pipeline
pattern. `telegram_summarizer` uses `TelegramChannelSummarizer` (Telethon
reader + Gemini summarizer behind Protocols) and the shared `TelegramNotifier`
— see [Telethon-direct modules](#telethon-direct-modules) below.

`kinozal_pipeline` additionally enriches each movie with a YouTube trailer
(`enrich_with_trailer`, #144). Отбор детерминированный, и **Gemini в этом cron-04:00
hot path нет** — eval-only LLM/embedding/TMDB-пикеры (#142/#143/#329) сознательно вне
прода, так что трейлер стоит ноль Gemini-квоты. Композиция retrieval → selection и
обоснование выбора — канон в
[pipeline.md § Trailer retrieval and selection](pipeline.md#trailer-retrieval-and-selection-140-141-144).

## Protocols

Three boundaries isolate external services from business logic:

| Protocol | Prod implementation | Test double | Defined in |
|---|---|---|---|
| `Storage` | `SheetsStorage` | `InMemoryStorage` | `sheets_storage.py` |
| `Notifier` (implicit) | `TelegramNotifier` | `InMemoryNotifier` | `telegram_notifier.py` |
| `Enricher` | `RotatingGeminiEnricher` | `NullEnricher` | `gemini_enricher.py` |

These boundaries — the three adapters plus the auth-isolation rule "adapters take
ready clients, not credentials" — are now machine-enforced by `import-linter`
(the `imports` gate in `ci_check.py`, contracts in `.importlinter`). See
[ci.md](ci.md) for the two contracts (`adapter-no-auth`, `pipeline-layers`) (#234).

## Data flow (generic pipelines)

```
sources.json
  → pipeline_config.py (macro expansion, schema validation)
    → fetch (HTTP — per-pipeline, not declarative)
      → generic_pipeline.py (extract_from_json / extract_from_html → NormalizedItem)
        → sheets_storage.get_existing_keys()  → dedupe
          → telegram_notifier.send_items()    [DELIVER]
            → sheets_storage.append_rows()    [STORE SENT ITEMS]
```

Sheets rows represent confirmed delivery. Delivery failures are surfaced as
run failures instead of being collapsed into "no news." Details in
[pipeline.md](pipeline.md).

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
