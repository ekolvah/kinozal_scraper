# Phase 1 Data Model: GitHub Trending source

**Feature**: 001-github-trending  
**Date**: 2026-05-18

## Entities

### `NormalizedItem` (existing — reused as-is)

Defined in `generic_pipeline.py:15-29`. No changes for this feature. Field usage for this source:

| Field | Source value | Post-normalisation value |
|---|---|---|
| `dedupe_key` | `h2 a@href` → `"/tinyhumansai/openhuman"` | `"tinyhumansai/openhuman"` (leading `/` stripped in pipeline) |
| `title` | `h2 a@href` → `"/tinyhumansai/openhuman"` | `"tinyhumansai/openhuman"` (same normalisation) |
| `source_id` | `"github_trending"` (constant from `sources.json`) | unchanged |
| `url` | `h2 a@href` + `base_url: "https://github.com"` → `"https://github.com/tinyhumansai/openhuman"` | unchanged |
| `description` | text of `<p>` inside row | unchanged; empty string if `<p>` absent |
| `metric` | text of `span.d-inline-block.float-sm-right` → e.g. `"1,690 stars today"` | unchanged; empty string if absent → triggers WARNING log |
| `image_url` | not extracted (field set to `null` in sources.json) | always empty |
| `trailer_url` | unused | always empty |
| `raw` | empty dict — `extract_from_html` does not populate `raw` | unchanged |

**Row-level invariants** (enforced by the new pipeline, not by `generic_pipeline.py`):

1. After normalisation, `dedupe_key` and `title` are equal and contain no leading `/`.
2. `url` always begins with `https://github.com/` (because `base_url` is set and `h2 a@href` always starts with `/`).
3. If `description` or `metric` is empty, the item is **still emitted** to storage and notifier (Principle IV). A WARNING is logged with the dedupe_key so the operator can spot drift in the daily logs.
4. If `dedupe_key` or `title` is empty after extraction, the item is **dropped** by `extract_from_html` via the existing `result.errors` path — this is unchanged behaviour.

### Source configuration entry (new — declarative)

Lives in `sources.json` under `sources[]`. Shape conforms to `pipeline_config._REQUIRED_SOURCE_FIELDS`. Concrete delta in [contracts/sources_json.md](./contracts/sources_json.md).

**Validation rules** (enforced by `pipeline_config.validate_sources_config()`):

- All existing required keys: `id`, `type`, `url`, `limit`, `sheet_tab`, `dedupe_key`, `fields`, `message_template`.
- **NEW in this PR**: when `type == "html"`, `row_selector` is also required (currently optional in the validator — see `pipeline_config.py:14-23`). Existing HTML sources (`kinozal_movies`, `soldout_events`) already declare it, so this is a tightening with no cost.
- `limit` is a positive integer (existing rule).
- All macros (`{{...}}`) resolve at load time (existing rule). The trending entry uses no macros.

### Storage row (shared sheet — existing schema)

Tab: `github_projects` (shared between `github_new_popular` and `github_trending`).

Column layout: `ROW_HEADERS = ["dedupe_key", "title", "url", "metric", "source_id", "notified_at"]`.

Both sources contribute rows with the same column order. The `source_id` column will hold either `"github_new_popular"` or `"github_trending"` depending on which source first observed the repo. The `dedupe_key` is the shared identity key.

**Migration**: none. The existing tab and its rows are unchanged. The new source simply starts writing into the same tab; its `dedupe_key` shape (`owner/repo`) is by construction identical to the existing `github_new_popular` rows.

## Entity Relationships

```text
sources.json
├── sources[0] github_new_popular  (type=json)  ──┐
│                                                 │
└── sources[N] github_trending     (type=html)  ──┤
                                                  │
                                                  ▼
                                          github_projects (Sheets tab)
                                          ├── row: dedupe_key="owner/repo", source_id="github_new_popular"
                                          ├── row: dedupe_key="other/repo", source_id="github_trending"
                                          └── row: ...

                                          (dedupe_key is the shared identity; source_id is informational)
```

## State Transitions

None — items are stateless within a run. The only persisted state is the dedupe row; once written, it is never modified. Across runs:

- Run N: trending source sees `repoX` → not in storage → notify + write row.
- Run N+1: trending source sees `repoX` again → in storage → no notification.
- Run N+k: existing source sees `repoX` (older repo now also matching its query) → in storage → no notification. (Cross-source dedupe.)

## Out-of-Scope (declared)

- Repository age / total-star metadata in the row — the trending source does not have this information and the spec does not require it.
- LLM-enriched summary (`enrich:` block) — see research.md item 6.
- Image / OG-preview extraction — `image_url` is `null` for this source.
