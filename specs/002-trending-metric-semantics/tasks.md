# Tasks: Align `metric` column semantics across GitHub sources

**Input**: [spec.md](./spec.md)

**Tests MANDATORY** (Constitution Principle I) — every ⚠️ task must be RED
before its corresponding impl task is started.

## Phase 1: RED tests

- [ ] **T1** ⚠️ In `tests/test_github_trending_pipeline.py`: add
      `test_metric_is_total_stars_not_daily` — runs the pipeline against the
      saved fixture, asserts every row's `metric` matches `r"^\d+$"` AND the
      first item's metric `>= 100` (sanity floor: fixture's first repo is
      `tinyhumansai/openhuman` with 14,113 stars).
- [ ] **T2** ⚠️ In the same file: add
      `test_stars_today_available_in_raw` — asserts the first item's
      `item.raw["stars_today"]` matches `r"^\d+$"` (the daily-delta digits
      from `span.d-inline-block.float-sm-right`).
- [ ] **T3** ⚠️ In the same file: add
      `test_notification_shows_total_and_today` — asserts the first sent
      Notification's `text` contains the item's `metric` value AND the
      `stars_today` value (substring match, not exact format).
- [ ] **T3a** ⚠️ Update existing tests that asserted "stars today" wording
      in metric (the partial-row test on missing-metric path stays — empty
      metric still legal). Re-read failures, observe RED for T1/T2/T3.

## Phase 2: Implementation

- [ ] **T4** Make T1–T3 GREEN:
  - `sources.json` — `github_trending.fields.metric` →
    `"a[href$=\"/stargazers\"]"`; `message_template` →
    `"<b>{title}</b>\n{description}\n⭐ {metric} (+{stars_today} today)\n{url}"`.
  - `github_trending_pipeline.py` — in `_normalize_items` (or a new helper
    called from `run_github_trending_pipeline` before the storage filter):
    - Strip non-digit chars from `item.metric` (`"14,113"` → `"14113"`).
    - Add `_enrich_with_stars_today(html, items)` — re-parse the HTML once,
      walk `article.Box-row` and match by `h2 a@href`; extract numeric digits
      from `span.d-inline-block.float-sm-right`; store as
      `item.raw["stars_today"]`. Missing element → empty string.
  - Adjust empty-metric WARNING log line wording if needed so T1 still passes.
- [ ] **T5** Update `docs/architecture/storage.md`: add "Column semantics —
      invariants" subsection after the `ROW_HEADERS` description; record the
      `github_projects.metric = total stargazers, digit-only string`
      invariant; reference this PR and #60 for the regression context.

## Phase 3: Polish

- [ ] **T6** `python scripts/ci_check.py` — green.
- [ ] **T7** Commit, push, `gh pr create` closing #86. No self-merge.

## Out of scope

- Backfill of legacy `github_projects.metric` rows with the old
  `"X stars today"` shape — historical, doesn't affect new behaviour.
- Schema validator that reads `github_projects.metric` and enforces
  digit-only — pin-tests are sufficient.
- The same invariant applied to other tabs (`steam_games`, `events`,
  `movies`) — different sources, different semantics; not this PR's scope.
