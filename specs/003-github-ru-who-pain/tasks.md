# Tasks: Russian "who/pain" notifications for GitHub sources

**Input**: [spec.md](./spec.md)

**Tests MANDATORY** (Constitution Principle I) — every ⚠️ task must be RED
before its corresponding impl task is started.

## Phase 1: RED tests

- [ ] **T1** ⚠️ `tests/test_pipeline_config.py`: add
      `TestRussianEnrichPrompts::test_both_github_sources_have_summary_ru_enrich`
      — load `sources.json`, assert `github_new_popular` and `github_trending`
      both have `enrich.field == "summary_ru"` and a prompt containing the
      substrings `Для кого` and `Зачем`. Asserts also that each source's
      `message_template` contains `{summary_ru}`.
- [ ] **T2** ⚠️ `tests/test_github_trending_pipeline.py`: add
      `TestRussianEnrichment::test_enricher_field_in_notification` — runs
      pipeline with a fake enricher returning
      `"Для кого: ML-инженер\nЗачем: ускоряет inference"`. Asserts the first
      sent notification's text contains both literal lines.
- [ ] **T3** ⚠️ Same file: add
      `test_no_enricher_no_summary_ru_in_text` — runs pipeline with
      `enricher=None` (current behaviour); asserts notification text does
      NOT contain literal `{summary_ru}` placeholder leakage and does NOT
      contain "Для кого".
- [ ] **T4** ⚠️ Same file: add
      `test_quota_exhausted_falls_back_but_still_sends` — fake enricher
      raises `QuotaExhausted` on second item; asserts all 3 (fixture-derived)
      items still get notified, first contains the enriched text, rest have
      empty `summary_ru` (fallback from `on_error`).

## Phase 2: Implementation

- [ ] **T5** Make T1 GREEN:
  - `sources.json` — update `github_new_popular.enrich.prompt` to the new
    two-line Russian template; keep `field: "summary_ru"`.
  - Update `github_new_popular.message_template` so `{summary_ru}` is the
    only thing on its own line between `<b>{title}</b>` and the stars line.
  - Add an `enrich` block to `github_trending` with the same `field` and a
    prompt that does not reference `$language` (trending HTML has no
    language field).
  - Update `github_trending.message_template` to include `{summary_ru}` on
    its own line.
- [ ] **T6** Make T2–T4 GREEN:
  - `github_trending_pipeline.py` — `run_github_trending_pipeline` gains
    `enricher: Enricher | None = None` parameter (typed import from
    `gemini_enricher`). Insert the enrichment loop between
    `new_items = …` and the storage write, mirroring
    `json_pipeline._run_single_source` quota semantics.
  - Update `__main__` block to construct a `RotatingGeminiEnricher` when
    `GOOGLE_API_KEY` is set, otherwise `NullEnricher()`; pass through.
- [ ] **T7** `docs/architecture/gemini.md`: short note that both GitHub
      sources now use the same `summary_ru` two-line prompt; record the
      `Для кого:` / `Зачем:` invariant so future prompt edits don't drift.

## Phase 3: Polish

- [ ] **T8** `python scripts/ci_check.py` — green.
- [ ] **T9** Commit, push, `gh pr create` closing #88. No self-merge.

## Out of scope

- Backfill of existing rows (no schema migration; `summary_ru` was always
  `item.raw` only).
- Translation of repo title / URL.
- Other tabs (`steam_games`, `movies`, `events`) — different domain, not
  this PR's scope.
- A schema validator that asserts notifications contain "Для кого" /
  "Зачем" at runtime — pin-tests are sufficient.
