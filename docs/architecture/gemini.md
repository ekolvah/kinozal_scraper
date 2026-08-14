# Gemini enrichment and quota strategy

**Question this document answers:** how this project spends a **free-tier** LLM quota without
going dark — the `Enricher` Protocol, model rotation and discovery, retry/backoff behaviour,
where enrichment prompts live, what the `llm_call` observability record carries (tokens and
latency), the `summary_ru` invariant, and how enrichment is plumbed into the pipelines. Quota is
the axis: most decisions here exist because the budget is finite and a spent quota must degrade
visibly, not silently (§IV).

**Not here.** Which pipelines enrich and where enrichment sits in a run →
[`runtime.md`](runtime.md) / [`pipeline.md`](pipeline.md). Prompt-injection and other
LLM-specific threats → [`llm-security.md`](llm-security.md). API keys and their rotation →
[`operations.md`](operations.md). Trailer picking — the eval-only LLM/embedding pickers are
**not** in the prod hot path and live in
[`pipeline.md`](pipeline.md#trailer-retrieval-and-selection).

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
   for the rest of the run, advance to next live model
4. On `TryNextModel` (truncation, network timeout, any unexpected exception
   on this specific item) → advance to next live model, do **not**
   mark the model dead (same model may handle a different item fine)
5. After all live models exhausted → 60s cooldown → clear the dead set →
   one more full rotation (a quota window may have rolled over; a 404'd
   model may have come back)
6. Still exhausted → raise `QuotaExhausted` to caller

`TelegramChannelSummarizer` uses the same model list via `_build_model_list()` →
`get_generation_models()`, with simpler rotation and no cooldown. A
`503/UNAVAILABLE` response is retried on the same model up to three attempts
with exponential backoff; after exhaustion it becomes `TryNextModel`. The
summarizer advances immediately on 404, 429, or any other `TryNextModel`
failure, without marking that model dead for later channels.

## Model discovery

`get_generation_models()` in `gemini_enricher.py`:
1. `client.models.list()` — all available models (new `google.genai` SDK)
2. Filter: `generateContent` in `Model.supported_actions`
3. Filter: `_is_text_gemini()` — starts with `models/gemini-`, no suffix like `-tts`, `-image`, `-customtools`, `-computer-use`, `-robotics`
4. Sort: newest version first (`_model_version_key`)

## Retry logic

`GeminiEnricher._generate` uses tenacity:
- 3 attempts
- Exponential backoff: 1s multiplier, max 10s
- Retries only on `google.api_core.exceptions.ResourceExhausted`
- After 3 failures: wrapped as `QuotaExhausted` for `RotatingGeminiEnricher` to catch

`GeminiSummarizer._generate_content` also uses three attempts and the same
1s–10s exponential backoff, but retries only `503/UNAVAILABLE`. This absorbs a
short Gemini service interruption for the current channel before model
rotation; exhausted retry is classified through the shared taxonomy and the
next configured model is tried. The summarizer does not retry 404 or 429 on
the same model.

`GeminiEnricher.enrich` translates per-model failures into rotator signals so
the rotator can give each item another chance on a different model before
falling back to `FALLBACK_MARKER`:

| Condition | Exception surfaced | Rotator action |
|---|---|---|
| `ResourceExhausted` (429, quota) | `QuotaExhausted` | switch to next live model |
| `NotFound` (404, model deprecated mid-rotation) | `ModelUnavailable` | switch + mark dead for this run |
| `400 INVALID_ARGUMENT` (malformed request) | `ModelConfigRejected` | switch model, ERROR-log, record `config_rejected_models`, fire a Telegram alert, and red the job (§IV) |
| `TruncatedResponse` (MAX_TOKENS / SAFETY) | `TryNextModel` | switch to next live model |
| Any other exception (network, non-API errors) | `TryNextModel` | switch to next live model |
| `response_pattern` mismatch | (returns `FALLBACK_MARKER` directly) | no rotation — bad prompt is not a model problem |

**Why `ModelConfigRejected` is loud, not silent.** A `400 INVALID_ARGUMENT`
is *our request* being malformed — deterministic (every item 400s identically on
that model), a code bug, unlike a transient per-item `TryNextModel`. Absorbed as
a routine `TryNextModel` such a bug is invisible: the rotator drops to a working
model, notifications ship, the cron stays green, and no §IV alert fires.
`ModelConfigRejected` restores visibility: rotation still delivers data, but the
config bug reaches the operator (Telegram alert + red job). It is deliberately
**not** dead-marked (unlike 404): the alert forces a quick fix so the per-item
re-hit is transient, and dead-marking would risk false-killing a healthy model on
a rare *item-specific* 400 that `.status` alone can't distinguish from a
config-wide one. Behavioral note: an item-specific `INVALID_ARGUMENT` that every
model rejects now goes red + alert (previously silent green → fallback marker) —
that is a genuinely-rejected item reaching the operator, correct per §IV, not a
false-alarm regression.

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
correlates with echo and format leakage in model output.

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

### Thinking suppression (`_thinking_policy`)

Gemini 2.5+/3.x run an internal reasoning phase that, left unbounded, spends the
whole `max_output_tokens` on thoughts and returns `MAX_TOKENS` on a valid short
prompt. The request knob is version-specific, so one pure policy selects the
current API shape ([ADR-0008](../adr/0008-use-one-explicit-gemini-thinking-policy.md)):

- `v ≥ 3.0` uses `ThinkingConfig(thinking_level="low")` on its first and only
  request. When the caller configures `max_output_tokens`, that limit is raised
  up front to at least 1024; lower observed limits truncated after spending
  their allowance on thinking.
- `2.5 ≤ v < 3.0` keeps `ThinkingConfig(thinking_budget=0)`.
- `v < 2.5` keeps no `thinking_config`; a 2.0 model rejects any such config.

There is no per-model allow-list, probe, retry, or runtime capability cache. If
a future model changes the request contract again, the existing
`ModelConfigRejected` path makes that incompatibility visible instead of guessing
another argument or value.

## Call observability — tokens & latency

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
`requirements*.txt`, and no activation code in the repo — unrunnable-in-CI code
rots. The in-cron structured `llm_call` log stays the only production surface.

Recipe (throwaway venv, real `GOOGLE_API_KEY`):

```bash
# from the repo root — throwaway venv
python -m venv .venv-phoenix && .venv-phoenix/Scripts/activate   # *nix: source .venv-phoenix/bin/activate
pip install -e . -r requirements.txt                            # project + its runtime pins: bs4, gspread, google-genai…
pip install arize-phoenix openinference-instrumentation-google-genai
export PYTHONUTF8=1                                             # Windows: Phoenix prints an emoji → cp1252 stdout 400s without it
```

`-e .` alone is **not** enough — it installs only the deps declared in `pyproject.toml`, while
the enricher's transitive imports (`bs4`, `gspread`, …) are pinned in `requirements.txt`; skip it
and the harness dies on `ModuleNotFoundError: bs4`.

```python
import phoenix.otel
from openinference.instrumentation.google_genai import (
    GoogleGenAIInstrumentor,
)

tracer_provider = phoenix.otel.register()  # local collector + UI at http://127.0.0.1:6006
GoogleGenAIInstrumentor().instrument(tracer_provider=tracer_provider)  # patches client.models.generate_content

# then run the enricher / summarizer locally; spans stream into the Phoenix UI
```

**Inspecting the traces.** Open the printed Phoenix UI (`http://localhost:6006`) for the visual
waterfall, or read the spans from the terminal via `arize-phoenix-client`:
`phoenix.client.Client(base_url=…).spans.get_spans(project_identifier="default")`. Each span carries
`llm.model_name`, `llm.token_count.{prompt,completion,total}`, latency, and the full request
`input.value` / `llm.invocation_parameters` (e.g. the on-the-wire `thinking_level`) — payload detail
the in-cron `llm_call` breadcrumb does not capture. Phoenix also serves an **MCP** endpoint at
`http://localhost:6006/mcp`; `claude mcp add --transport http phoenix http://localhost:6006/mcp` lets
Claude Code query the traces via MCP (the server is consumer-side, loaded on the next session start).

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
`Зачем` and that the template references `{summary_ru}`.

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
  notification still goes out (Principle IV).

### Steam-specific fallback

`run_steam_pipeline` deviates from GitHub sources on one point: the original
English `short_description` is itself informative, so a failed translation
(empty result, `FALLBACK_MARKER` from `TruncatedResponse`, `QuotaExhausted`,
or `enricher is None`) falls back to `item.description`, not to the marker.
The notification ships in English, with a WARNING in cron logs marking the
degradation. Implemented in `steam_pipeline._apply_translation`.
