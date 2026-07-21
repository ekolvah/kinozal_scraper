# Gemini enrichment and quota strategy

## Enricher Protocol

Defined in `gemini_enricher.py`:

| Implementation | Use case |
|---|---|
| `NullEnricher` | tests, missing `GOOGLE_API_KEY` — returns `on_error` value |
| `GeminiEnricher` | single model, 3× retry with exponential backoff (1s–10s) |
| `RotatingGeminiEnricher` | production — cycles through all available models |

## Model rotation (free tier strategy)

Free tier limit: ~20 requests/day/model. With ~14 text models available,
rotation gives ~280 requests/day without upgrading.

`RotatingGeminiEnricher` behavior:
1. Try current model (skipping any marked dead this run)
2. On `ResourceExhausted` (429) → advance to next live model, retry immediately
3. On `NotFound` (404, model deprecated server-side) → mark this model dead
   for the rest of the run, advance to next live model (see #128)
4. On `TryNextModel` (truncation, network timeout, any unexpected exception
   on this specific item — see #130) → advance to next live model, do **not**
   mark the model dead (same model may handle a different item fine)
5. After all live models exhausted → 60s cooldown → clear the dead set →
   one more full rotation (a quota window may have rolled over; a 404'd
   model may have come back)
6. Still exhausted → raise `QuotaExhausted` to caller

`TelegramChannelSummarizer` uses the same model list via `_build_model_list()` →
`get_generation_models()`, with simpler rotation (no cooldown, just skip on 429).

## Model discovery

`get_generation_models()` in `gemini_enricher.py`:
1. `client.models.list()` — all available models (new `google.genai` SDK)
2. Filter: `generateContent` in `Model.supported_actions` (was `supported_generation_methods` on the deprecated SDK, #107)
3. Filter: `_is_text_gemini()` — starts with `models/gemini-`, no suffix like `-tts`, `-image`, `-customtools`, `-computer-use`, `-robotics`
4. Sort: newest version first (`_model_version_key`)

## Retry logic

`GeminiEnricher._generate` uses tenacity:
- 3 attempts
- Exponential backoff: 1s multiplier, max 10s
- Retries only on `google.api_core.exceptions.ResourceExhausted`
- After 3 failures: wrapped as `QuotaExhausted` for `RotatingGeminiEnricher` to catch

`GeminiEnricher.enrich` translates per-model failures into rotator signals so
the rotator can give each item another chance on a different model before
falling back to `FALLBACK_MARKER` (#128, #130):

| Condition | Exception surfaced | Rotator action |
|---|---|---|
| `ResourceExhausted` (429, quota) | `QuotaExhausted` | switch to next live model |
| `NotFound` (404, model deprecated mid-rotation) | `ModelUnavailable` | switch + mark dead for this run |
| `TruncatedResponse` (MAX_TOKENS / SAFETY) | `TryNextModel` | switch to next live model |
| Any other exception (network, `InvalidArgument`, …) | `TryNextModel` | switch to next live model |
| `response_pattern` mismatch | (returns `FALLBACK_MARKER` directly) | no rotation — bad prompt is not a model problem |

## Prompt configuration

Prompts live in `sources.json` under each source's `enrich` section:
- `prompt` — `string.Template` with `$title`, `$description`, `$metric`, `$url`, `$source_id` + any `raw` fields
- `parameters.temperature` (default 0.2), `parameters.max_tokens` (default 150)
- `response_pattern` (optional) — regex; if set, the model's answer is validated against it after markdown-strip. On mismatch the answer is replaced with the visible-anomaly marker (see below).
- `on_error` — fallback value if enrichment fails (default: empty string)

### Input sanitization

Before substitution, `$description` is sanitized: fenced code blocks are
removed, leading `*`/`#`/`>` markers are stripped, whitespace is collapsed,
and the text is truncated to 400 characters. This is a defensive shaping of
the *prompt input* — `item.description` itself is left untouched. Rationale:
raw `<p>` from GitHub trending HTML often contains README markdown that
correlated with echo / format-leak in model output (issue #106 — closed
not-planned, fix lives in PR #102).

### Output validation and fallback

`_generate` reads `response.candidates[0].finish_reason`:

- `STOP` → answer is markdown-stripped (outer ``` fences, `**bold**`
  wrappers, leading `*`/`-` bullets) and returned.
- `MAX_TOKENS` or `SAFETY` → `TruncatedResponse` is raised. `enrich` catches
  it and returns the **fallback** value: `on_error` if non-empty, otherwise
  `FALLBACK_MARKER = "⚠️ summary unavailable"`.

After successful generation, if `response_pattern` is set on the source's
`enrich` block, the cleaned text is matched against it. A mismatch (model
echoed the instruction, produced markdown listing, etc.) also routes to the
fallback marker.

Every call logs `model_name`, `prompt_len`, `resp_len`, `finish_reason`,
and the first line of the answer at INFO level — the diagnostic surface
needed to triage drift without instrumenting each call ad hoc.

## Call observability — tokens & latency (#145)

Both live Gemini call sites — `GeminiEnricher._generate` (item enrichment) and
`GeminiSummarizer.summarize` (Telegram channel summaries) — emit one structured
`llm_call` breadcrumb per completed call via `llm_observability.log_llm_call`:

```
llm_call model=… prompt_tokens=… candidates_tokens=… total_tokens=… latency_ms=… finish=… outcome=…
```

Token counts come from `response.usage_metadata` (read by the pure, tolerant
`llm_observability.extract_usage`); latency is `time.perf_counter()` around the
live call. A missing / partial `usage_metadata` degrades to `None` fields plus an
`outcome=…,degraded` marker rather than crashing or logging a misleading zero
(§IV — visibility over silence). This is the cheap, dependency-free observability
layer that runs **in cron**; it complements, not replaces, the older
`prompt_len/resp_len/first_line` INFO line above.

### Phoenix / OpenInference — local dev only (not cron)

For a visual trace (spans, per-call token spend, latency waterfall) during manual
debugging, run [Arize Phoenix](https://github.com/Arize-ai/phoenix) locally with the
OpenInference instrumentor for `google.genai` (the old-SDK instrumentor
`openinference-instrumentation-google-generativeai` does not install on Python 3.12).
It is **opt-in, local, and
deliberately not committed**: no `arize-phoenix` / `openinference-*` in
`requirements*.txt`, and no activation code in the repo — unrunnable-in-CI code rots
(#145). The in-cron structured `llm_call` log stays the only production surface.

Recipe (throwaway venv, real `GOOGLE_API_KEY`):

```bash
pip install arize-phoenix openinference-instrumentation-google-genai
```

```python
import phoenix.otel
from openinference.instrumentation.google_genai import (
    GoogleGenAIInstrumentor,
)

tracer_provider = phoenix.otel.register()  # local collector + UI at http://127.0.0.1:6006
GoogleGenAIInstrumentor().instrument(tracer_provider=tracer_provider)  # patches client.models.generate_content

# then run the enricher / summarizer locally; spans stream into the Phoenix UI
```

## `summary_ru` invariant (GitHub sources)

Both `github_new_popular` and `github_trending` write the enrich result to
`item.raw["summary_ru"]` and render it via `{summary_ru}` in the
`message_template`. The prompt MUST ask Gemini for exactly two Russian lines:

```
Для кого: <короткая роль / аудитория>
Зачем: <какую конкретную боль или задачу решает>
```

Pin-tests in `tests/test_pipeline_config.py::TestRussianEnrichPrompts`
enforce that both sources' prompts contain the substrings `Для кого` and
`Зачем` and that the template references `{summary_ru}`. Closed by #88.

`summary_ru` is **never** written to the Sheets row — it lives only in
`item.raw` so the notification template can read it. No `ROW_HEADERS`
migration required.

## Enrichment plumbing across pipelines

`github_popular_pipeline.run_github_popular_pipeline`, `github_trending_pipeline.run_github_trending_pipeline`
and `steam_pipeline.run_steam_pipeline` accept an optional `enricher: Enricher | None`
parameter and apply the same loop semantics:

- `enricher is None` or no `enrich` block → field stays unset, `{summary_ru}`
  placeholder resolves to empty, notification still sends.
- `QuotaExhausted` raised mid-loop → remaining items get the fallback value
  (`enrich.on_error` if non-empty, otherwise `FALLBACK_MARKER`), but every
  notification still goes out (Principle IV; #128).

### Steam-specific fallback (issue #124)

`run_steam_pipeline` deviates from GitHub sources on one point: the original
English `short_description` is itself informative, so a failed translation
(empty result, `FALLBACK_MARKER` from `TruncatedResponse`, `QuotaExhausted`,
or `enricher is None`) falls back to `item.description`, not to the marker.
The notification ships in English, with a WARNING in cron logs marking the
degradation. Implemented in `steam_pipeline._apply_translation`.
