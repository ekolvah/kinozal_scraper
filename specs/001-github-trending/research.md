# Phase 0 Research: GitHub Trending source

**Feature**: 001-github-trending  
**Date**: 2026-05-18  
**Status**: complete — all NEEDS CLARIFICATION resolved

## 1. Live CSS selectors for `github.com/trending?since=daily`

**Decision**:

| Field | Selector | Notes |
|---|---|---|
| Row container | `article.Box-row` | 18 rows seen on 2026-05-18 fetch |
| Repository name link | `h2 a` (text) → "owner /repo" (no space after `/`) | Unused directly; we use `h2 a@href` for both title and dedupe_key, then normalise |
| Repository href | `h2 a@href` → `/owner/repo` (relative) | Resolved to full URL via `base_url: https://github.com` |
| Description | `p` | Plain text, may be missing for some repos |
| Today's stars | `span.d-inline-block.float-sm-right` → "1,690 stars today" | Free-form text — surfaced as-is in notification |

**Rationale**: Verified with a live `requests.get(...)` + BeautifulSoup probe (see plan-time output). The issue body proposed `.octicon-star + span` for the metric; that selector matches multiple elements on the page (total-stars and today-stars both use the octicon). `span.d-inline-block.float-sm-right` is the unique element holding "X stars today".

**Alternatives considered**:

- `.octicon-star + span` (from issue body) — rejected, ambiguous.
- Parsing a JSON island in the page — none exists; trending is server-rendered HTML.
- Hitting an undocumented JSON endpoint (`api.github.com/...`) — there is no public Trending API. Confirmed by GitHub's docs.

**Drift risk**: high. GitHub changes its CSS classes irregularly. Mitigation: the saved fixture lets unit tests catch the failure mode separately from the live test, and US3 ensures the live failure is visible.

## 2. Shared dedupe key format between `github_new_popular` and `github_trending`

**Decision**: both sources emit dedupe keys in the form `"owner/repo"` (no leading slash).

- `github_new_popular` already produces this via `dedupe_key: "full_name"` (the JSON field is exactly `"owner/repo"`).
- `github_trending` extracts `h2 a@href` → `/owner/repo`, then the new pipeline strips the leading slash locally before passing to `Storage.append_rows()`.

**Rationale**: A migration of the existing source's keys would lose all currently-stored dedupe rows in Google Sheets and cause a one-time wave of duplicate notifications. The cost of the local normalisation step in the new pipeline is one line of code; preserving stored state is worth it.

**Alternatives considered**:

- **Add a `transform:` system to `generic_pipeline.extract_from_html`** (per the literal text of issue #60: `"transform": "strip_leading_slash"`). Rejected: introduces a new public surface in a shared module that other sources don't need, and the only transform used is `strip_leading_slash`. YAGNI.
- **Switch `github_new_popular` dedupe_key to `html_url`** (full URL). Rejected: same migration-pain reason — invalidates all existing dedupe rows.
- **Hash both sides into a normalised key** (e.g. SHA1 of lowercased owner/repo). Rejected: hashes destroy the human-readable property of the current sheet, which is used for debugging.

## 3. Source ordering for FR-005a (format-from-first-source-on-overlap)

**Decision**: ordering is enforced by the order of steps in `.github/workflows/run-script.yml`. The new step `python github_trending_pipeline.py` is inserted **after** `python json_pipeline.py`. No in-code priority logic is added.

**How this satisfies FR-005a**:

1. Workflow step "Run JSON sources pipeline" runs first → `json_pipeline.py` processes `github_new_popular` → writes "owner/repo" to `github_projects` tab and sends Telegram message in `github_new_popular` format.
2. Workflow step "Run GitHub trending pipeline" runs next → `github_trending_pipeline.py` reads `Storage.get_existing_keys("github_projects")` → sees the row just written → filters that repo out → no second notification.
3. Net effect: format of the surviving notification is the one from the first-run source. Matches spec.

**Rationale**: zero new abstraction. The constitution's "no organizational-only abstractions" principle (II) is honoured.

**Alternatives considered**:

- **A shared `priority` field in `sources.json`** read by both pipelines. Rejected: the two pipelines run as separate Python processes — they cannot coordinate in memory. The only shared state is `Storage`. Workflow step order is the natural coordination mechanism that already exists.
- **A single combined `github_pipeline.py` that handles both sources sequentially in one process.** Rejected: would require merging `json` and `html` source handling — large blast radius for a property that is already free.

## 4. Why no change to `generic_pipeline.py`

**Decision**: keep `generic_pipeline.py` untouched. All GitHub-trending-specific normalisation happens in the new `github_trending_pipeline.py` module after `extract_from_html` returns.

**Rationale**:

- `generic_pipeline.extract_from_html` already supports everything we need: `row_selector`, per-field selectors with `@attr`, `base_url` for URL resolution.
- The only "missing" feature (transform pipeline on selectors) has exactly one prospective user — this PR. Per the constitution's Principle V ("Root Cause Before Fix") and YAGNI, that's not enough to justify expanding a shared module.
- Local post-processing (`item.dedupe_key = item.dedupe_key.lstrip("/")`, `item.title = item.dedupe_key`) is the same pattern `kinozal_pipeline.py::_normalize_items` uses (lines 81–98) — established precedent.

**Alternatives considered**:

- **Add `transform:` support to `_html_field`**. Rejected — YAGNI.
- **Use a regex post-processor in `sources.json`**. Rejected — introduces a config schema that is harder to validate and harder to test than Python code.

## 5. Visibility on zero-row extraction (closing the gap)

**Decision**: the new `github_trending_pipeline.py` returns a non-zero exit code when `extract_from_html` produces zero items. `events_pipeline.py` currently does *not* (it just logs and returns) — this is a pre-existing visibility gap for `soldout_events`. **We do not retrofit `events_pipeline.py` in this PR** (out of scope, not a regression). The new pipeline gets the non-zero exit from the start.

**Rationale**: Constitution Principle IV ("Visibility Over Silence") explicitly requires that extraction failures turn the CI run red. Adding non-zero exit to the new module is one line. Retroactively changing `events_pipeline.py` could change soldout's CI behaviour and is a separate concern.

**Follow-up**: open a separate issue post-merge to align `events_pipeline.py` and `kinozal_pipeline.py` with the same non-zero exit on zero-row outcome. Linked back to `feedback_visibility_over_silence` memory.

**Alternatives considered**:

- **Have all HTML pipelines share a common exit-on-error wrapper.** Worthwhile cleanup, but separate scope.

## 6. Enrichment with Gemini for trending source

**Decision**: no LLM enrichment in v1. The `enrich:` block is omitted from the new source entry.

**Rationale**: issue #60 does not request enrichment. The `github_new_popular` source uses a Gemini call to produce a 100-character Russian summary; doing the same for trending would double the daily Gemini quota cost without a clear product win. Trending entries already carry a one-line description from GitHub itself.

**Alternatives considered**:

- **Add the same `enrich:` block.** Rejected — quota cost without a corresponding spec acceptance criterion. Easy to add later if the user wants it.

## 7. Live E2E test placement and gating

**Decision**: a new `tests/test_e2e_github_trending.py` performs one real HTTP GET against `github.com/trending?since=daily`, asserts ≥1 row with non-empty title and `https://github.com/...` URL. Gated identically to `tests/test_e2e_kinozal_titles.py` — skipped automatically when no network or when the run is marked offline.

**Rationale**: precedent already exists in this repo. Reuse the same gating mechanism.

**Alternatives considered**:

- **Skip live E2E entirely; rely on fixture tests.** Rejected — the constitution's Quality Gates section explicitly requires an E2E smoke test for sources that touch HTML extraction or external APIs.
