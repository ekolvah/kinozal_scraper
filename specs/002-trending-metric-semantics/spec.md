# Feature Specification: Align `metric` column semantics across GitHub sources

**Feature Branch**: `codex-issue-86-trending-metric-semantics`

**Created**: 2026-05-18

**Status**: Draft

**Parent**: #60 (post-merge correction)

**Input**: User feedback after PR #85 merged. Real-world example:
```
NirDiamant/agents-towards-production  ...  172 stars today  github_trending
```
Pieces with thousands-of-stars in the same column from `github_new_popular`
collide with this daily-delta value — operator could not tell what `metric`
actually means without checking `source_id`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — `metric` column reads consistently across sources (P1)

As the operator scanning the `github_projects` Sheets tab, when I sort or
compare rows by the `metric` column, I want every value to mean the same thing
regardless of which source wrote the row, so I can use the column as data
rather than as a per-source code that needs decoding.

**Why this priority**: Without this, the column is unusable for any analysis
across both sources. This is a correctness bug discovered post-merge of #60 —
not a new feature, but a contract violation between two sources writing the
same column.

**Independent Test**: Run both `json_pipeline.py` (writes
`github_new_popular` rows) and `github_trending_pipeline.py` (writes
`github_trending` rows) into an in-memory `github_projects` tab. For every
written row, `row[metric_col_index]` matches `^\d+$` — no thousands separators,
no trailing words.

**Acceptance Scenarios**:

1. **Given** the trending fixture HTML, **When** the pipeline writes rows,
   **Then** every row's `metric` is the **total** stargazers count of the
   repository as a digit-only string (e.g. `"14113"`), parsed from
   `a[href$="/stargazers"]`.
2. **Given** the same fixture, **When** the pipeline sends notifications,
   **Then** every Telegram message contains both the total (`⭐ 14113`) and the
   daily delta (`+1690 today`) — so the trending velocity signal is preserved
   in the human-readable channel.
3. **Given** a row whose `<a href="…/stargazers">` is missing (extraction
   drift), **When** the pipeline processes it, **Then** the item still emits
   (Principle IV — visibility over silence), `metric` is empty string, and a
   WARNING is logged with the dedupe_key.

## Requirements *(mandatory)*

- **FR-001**: For the `github_trending` source, the `metric` field stored to
  `github_projects` MUST be the total stargazers count as a digit-only
  string (no commas, no words). Matches the shape `github_new_popular`
  already writes.
- **FR-002**: The Telegram notification for `github_trending` MUST surface
  the daily-delta ("X stars today" → numeric `X`) alongside the total. This
  signal is **Telegram-only** — never persisted to the row.
- **FR-003**: The invariant "for `github_projects.metric`, all sources write
  total stargazers count as a digit-only string" MUST be recorded in
  `docs/architecture/storage.md` so future GitHub-source additions inherit it.
- **FR-004**: Missing daily-delta on the trending page (the
  `span.d-inline-block.float-sm-right` element absent or unparseable) MUST
  NOT block the notification — `stars_today` placeholder MUST resolve to
  empty string, the item still goes through.

### Success Criteria

- **SC-001**: A fresh pipeline run over the saved fixture
  `tests/fixtures/github_trending/trending_daily.html` writes ≥1 row whose
  `metric` is a digit-only string ≥ 100 (sanity floor — first fixture row's
  stargazer count is 14,113, well above any plausible daily delta).

## Assumptions

- The `a[href$="/stargazers"]` selector is stable enough — verified live on
  2026-05-18 against the same fixture. If GitHub renames the URL pattern, the
  visibility safety net from US3/#60 (zero-row → exit 1) catches it.
- `build_notification` already resolves unknown placeholders via `item.raw`
  fallback (`generic_pipeline.py:255-261`) — no change needed there.
- The existing `extract_from_html` contract is **not** extended (no new
  `extra_fields` DSL). Daily-delta extraction is done by a small local
  helper in `github_trending_pipeline.py`, reusing the already-parsed
  BeautifulSoup tree pattern.
