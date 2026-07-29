# Runtime architecture

**Question this document answers:** what exists at runtime and how the pieces are wired —
which pipelines there are (entry point, sources, kind), which Protocol boundaries isolate the
external services, how data flows through a generic pipeline run, and which modules
deliberately bypass that pattern. This is the **system-level map**: breadth over depth.

**Not here.** How one pipeline is built inside — extraction layers, `extract_from_*` contracts,
`NormalizedItem`, notification templates, fetch behaviour → [`pipeline.md`](pipeline.md). The
storage Protocol's own invariants (row schema, column semantics, write ordering) →
[`storage.md`](storage.md). The Gemini side (rotation, quota, retry, prompts) →
[`gemini.md`](gemini.md). How the daily run is *operated* — cron expression, env vars and
secrets, failure isolation, alerting → [`operations.md`](operations.md); the `Schedule` column
below is a coarse label, not the canon.

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

Both live-Gemini call sites (`GeminiEnricher._generate`, `GeminiSummarizer.summarize`)
emit a structured `llm_call` breadcrumb with token usage (`usage_metadata`) and
latency — see [gemini.md § Call observability](gemini.md#call-observability--tokens--latency-145) (#145).

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
- Env vars override runtime behavior — full list in [operations.md](operations.md#environment-variables)

## Telethon-direct modules

`TelegramChannelSummarizer.py` and `telegram_summarizer.py` use Telethon +
Gemini directly rather than going through the generic pipeline (sources.json
→ declarative extraction → Storage → Notifier). The reason is the domain:
they read live Telegram channels and summarize free-form chat — neither fits
the "fetch → extract → dedupe → notify" shape the other pipelines share.

Reading channels needs a **user** session, not a bot: the tracked channels
are third-party, and the Bot API cannot fetch history where the bot is not an
admin. That session is a `StringSession` built from the `TELETHON_SESSION`
secret and from nothing else. Until #386 it was a Fernet-encrypted file
(`anon.session.encrypted` + `crypto.py`) committed to this public repo, with
the secret unset so the file branch was the live path.

They are nevertheless covered by the same quality gates: ruff format,
ruff lint, mypy, and dedicated tests (`test_telegram_summarizer.py`). Model
rotation is the same strategy as the generic pipelines — see
[gemini.md](gemini.md).
