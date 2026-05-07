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
- `on_error` — fallback value if enrichment fails (default: empty string)
