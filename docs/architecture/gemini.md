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
1. Try current model
2. On `ResourceExhausted` (429) → advance to next model, retry immediately
3. After all models exhausted → 60s cooldown → one more full rotation
4. Still exhausted → raise `QuotaExhausted` to caller

`TelegramChannelSummarizer` uses the same model list via `_build_model_list()` →
`get_generation_models()`, with simpler rotation (no cooldown, just skip on 429).

## Model discovery

`get_generation_models()` in `gemini_enricher.py`:
1. `genai.list_models()` — all available models
2. Filter: must support `generateContent`
3. Filter: `_is_text_gemini()` — starts with `models/gemini-`, no suffix like `-tts`, `-image`, `-customtools`, `-computer-use`, `-robotics`
4. Filter: not in `GEMINI_EXCLUDED_MODELS`
5. Sort: newest version first (`_model_version_key`)

## `GEMINI_EXCLUDED_MODELS`

Env var, comma-separated full model names. Allows disabling broken or
problematic models without code changes.

Example: `models/gemini-3.1-pro-preview,models/gemini-3-flash-preview`

Set as GitHub Actions variable (not secret) — see [ci.md](ci.md).

## Retry logic

`GeminiEnricher._generate` uses tenacity:
- 3 attempts
- Exponential backoff: 1s multiplier, max 10s
- Retries only on `google.api_core.exceptions.ResourceExhausted`
- After 3 failures: wrapped as `QuotaExhausted` for `RotatingGeminiEnricher` to catch

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

`json_pipeline.run_json_pipeline`, `github_trending_pipeline.run_github_trending_pipeline`
and `steam_pipeline.run_steam_pipeline` accept an optional `enricher: Enricher | None`
parameter and apply the same loop semantics:

- `enricher is None` or no `enrich` block → field stays unset, `{summary_ru}`
  placeholder resolves to empty, notification still sends.
- `QuotaExhausted` raised mid-loop → remaining items get the `on_error`
  fallback value, but every notification still goes out (Principle IV).

### Steam-specific fallback (issue #124)

`run_steam_pipeline` deviates from GitHub sources on one point: the original
English `short_description` is itself informative, so a failed translation
(empty result, `FALLBACK_MARKER` from `TruncatedResponse`, `QuotaExhausted`,
or `enricher is None`) falls back to `item.description`, not to the marker.
The notification ships in English, with a WARNING in cron logs marking the
degradation. Implemented in `steam_pipeline._apply_translation`.
