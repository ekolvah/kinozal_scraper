# Contract: `sources.json` delta for `github_trending`

**Feature**: 001-github-trending  
**Date**: 2026-05-18

## Exact JSON entry to append

The following object is appended to the `sources[]` array in `sources.json`. Position within the array does not affect dedupe (storage is shared), but for clarity place it adjacent to `github_new_popular`.

```json
{
  "id": "github_trending",
  "enabled": true,
  "type": "html",
  "url": "https://github.com/trending?since=daily",
  "base_url": "https://github.com",
  "row_selector": "article.Box-row",
  "limit": 25,
  "sheet_tab": "github_projects",
  "dedupe_key": "h2 a@href",
  "fields": {
    "title": "h2 a@href",
    "url": "h2 a@href",
    "description": "p",
    "metric": "span.d-inline-block.float-sm-right",
    "image_url": null
  },
  "message_template": "<b>{title}</b>\n{description}\n⭐ {metric}\n{url}"
}
```

### Field-by-field rationale

| Key | Value | Why |
|---|---|---|
| `id` | `"github_trending"` | New source identity; referenced by the new pipeline's filter. |
| `enabled` | `true` | Source goes live with the PR. |
| `type` | `"html"` | Trending page is server-rendered HTML; no public JSON API exists. |
| `url` | `https://github.com/trending?since=daily` | Daily-window trending page. `since=weekly`/`monthly` are different products — out of scope. |
| `base_url` | `https://github.com` | Used by `extract_from_html._resolve_url` for the `url` field only (per `generic_pipeline.py:201`). |
| `row_selector` | `article.Box-row` | Verified live 2026-05-18 — 18 rows match. |
| `limit` | `25` | Trending page renders ~25 entries; matches the issue body. Positive integer (passes validator). |
| `sheet_tab` | `"github_projects"` | **Shared with `github_new_popular`** — this is what makes dedupe cross-source (FR-005). |
| `dedupe_key` | `"h2 a@href"` | Yields `/owner/repo`; the new pipeline strips the leading `/` so the stored key matches `github_new_popular`'s `full_name` format. |
| `fields.title` | `"h2 a@href"` | Same as dedupe_key — after normalisation, both equal `owner/repo`. |
| `fields.url` | `"h2 a@href"` | Resolved against `base_url` to `https://github.com/owner/repo`. |
| `fields.description` | `"p"` | Plain text of the description `<p>` inside the row. |
| `fields.metric` | `"span.d-inline-block.float-sm-right"` | "X stars today" indicator. Free-form text passed through to the notification. |
| `fields.image_url` | `null` | No image extracted for v1. |
| `message_template` | `"<b>{title}</b>\n{description}\n⭐ {metric}\n{url}"` | Russian-style by default (the template is mostly punctuation; the `description` is whatever language GitHub returns). Mirrors the visual structure of `github_new_popular.message_template` (also `<b>{title}</b>` headed, single-line metric, trailing URL). |

## Pipeline contract

The new `github_trending_pipeline.py` module must satisfy:

```python
def run_github_trending_pipeline(
    storage: Storage,
    notifier: Notifier,
    sources_config: dict[str, Any] | None = None,
) -> None: ...
```

Behavioural contract:

1. Loads sources config via `pipeline_config.load_sources_config()` if `sources_config` is `None`.
2. Selects sources with `id == "github_trending"` and `enabled is True`. (Single-source filter — no startswith pattern like events/kinozal use, because there is only one trending source.)
3. For each selected source:
   - Fetches `url` with the same UA header pattern as `events_pipeline._fetch_html`.
   - Calls `generic_pipeline.extract_from_html(html_text, source)`.
   - **If `result.errors` non-empty and `result.items` empty**: logs ERROR; the **process exits with code 1** at the end of the source loop (Principle IV). This is the deliberate visibility behaviour deviation from `events_pipeline.py`'s current zero-exit.
   - **Otherwise** for each item: strips leading `/` from `dedupe_key`, copies the normalised value into `title`, logs WARNING if `metric == ""`.
   - Reads `storage.get_existing_keys("github_projects")`, filters items already present.
   - Writes new items to storage **before** sending notifications (`storage.append_rows` then `notifier.send_items`).

Module also exposes an `if __name__ == "__main__":` block matching the env-var pattern of `events_pipeline.py` (`CREDENTIALS`, `SPREADSHEET_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) so it can be invoked directly by the workflow step.

## Workflow contract

`.github/workflows/run-script.yml` gets a new step after "Run JSON sources pipeline" and before "Run events pipeline":

```yaml
- name: Run GitHub trending pipeline
  run: python github_trending_pipeline.py
  env:
    SPREADSHEET_URL: ${{ secrets.SPREADSHEET_URL }}
    TELEGRAM_BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
    TELEGRAM_CHAT_ID: ${{ secrets.BOT_CHATID }}
    CREDENTIALS: ${{ secrets.CREDENTIALS }}
```

Step ordering rationale: must run **after** `json_pipeline.py` so that any repo first observed by `github_new_popular` is already in storage when trending runs (satisfies FR-005a "format from first-running source"). Position before/after the events/kinozal pipelines is irrelevant — those don't touch `github_projects`.

`GITHUB_TOKEN` is intentionally absent — we use unauthenticated HTML page fetches and the trending page does not require auth.

## Validator contract delta

`pipeline_config.validate_sources_config()` change:

```python
# pipeline_config.py — inside the per-source loop
if source["type"] == "html" and not source.get("row_selector"):
    raise ConfigError(
        f"Source '{source_id}' has type='html' but no 'row_selector' field"
    )
```

This is the only change to the validator. Existing HTML sources (`kinozal_movies`, `soldout_events`) already declare `row_selector`, so the tightening is backward-compatible.
