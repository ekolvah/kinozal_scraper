---

description: "Tasks: GitHub Trending source for late-bloomer repositories"
---

# Tasks: GitHub Trending source for late-bloomer repositories

**Input**: Design documents from `/specs/001-github-trending/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: MANDATORY per Constitution Principle I (Test-First, NON-NEGOTIABLE). Every test task ⚠️ MUST be written and observed RED before the matching implementation task is started.

**Organization**: Tasks are grouped by user story (US1, US2, US3) to enable independent implementation and verification. Setup, Foundational, and Polish phases have no story label.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no in-flight dependencies).
- **[Story]**: Maps task to user story from spec.md (US1, US2, US3).
- Each task names the exact file path it touches.

## Path Conventions

This project uses a **flat single-project layout** (per plan.md "Structure Decision"): production Python lives at the repo root next to `generic_pipeline.py`; tests live in `tests/`; fixtures live in `tests/fixtures/`. There is no `src/` directory.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Test scaffolding the rest of the phases depend on.

- [ ] T001 [P] Create directory `tests/fixtures/github_trending/` and save a static snapshot of `https://github.com/trending?since=daily` as `tests/fixtures/github_trending/trending_daily.html` (use `requests.get` with `User-Agent: Mozilla/5.0`; commit the response body verbatim — do not edit). This fixture is consumed by tasks T004–T006, T010–T012, T014.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Tighten `pipeline_config.validate_sources_config()` so HTML sources without `row_selector` fail at startup (Constitution Principle VI, FR-006, SC-005). All user stories depend on a config that loads.

**⚠️ CRITICAL**: No user story phase may start until Phase 2 is complete (validator must catch malformed `github_trending` entries before T007 lands).

- [ ] T002 ⚠️ TEST FIRST in `tests/test_pipeline_config.py`: add `test_config_validation_html_requires_row_selector` that calls `validate_sources_config(...)` on a config whose `type=html` source lacks `row_selector` and asserts `ConfigError` with a message naming the source id. Run the test, observe RED.
- [ ] T003 Implement in `pipeline_config.py`: inside the per-source loop of `validate_sources_config()`, add `if source["type"] == "html" and not source.get("row_selector"): raise ConfigError(f"Source '{source_id}' has type='html' but no 'row_selector' field")`. Re-run T002, observe GREEN. Re-run full `tests/test_pipeline_config.py`, confirm existing tests still pass (existing HTML sources `kinozal_movies` and `soldout_events` already declare `row_selector`).

**Checkpoint**: Validator catches missing `row_selector`. User story phases may begin.

---

## Phase 3: User Story 1 — Catch a late-bloomer repository on the day it goes viral (Priority: P1) 🎯 MVP

**Goal**: Daily trending repositories appear in the morning Telegram digest regardless of their age or total star count.

**Independent Test**: Given the saved fixture in `tests/fixtures/github_trending/trending_daily.html`, the new pipeline produces ≥1 `NormalizedItem` with non-empty `title`, `url` beginning `https://github.com/`, and `dedupe_key` in the form `owner/repo` (no leading slash).

### Tests for User Story 1 (REQUIRED — write FIRST, must FAIL before implementation) ⚠️

- [ ] T004 [P] [US1] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_extracts_rows_from_fixture` — loads the fixture HTML, instantiates the source config inline (matching `contracts/sources_json.md`), calls `extract_from_html(html, source_config)`, applies the pipeline's `_normalize_items` helper (to be added in T008), asserts `len(items) >= 1`, every item has non-empty `title`/`url`/`dedupe_key`, `url.startswith("https://github.com/")`, `dedupe_key` matches `r"^[\w.-]+/[\w.-]+$"`. Observe RED.
- [ ] T005 [P] [US1] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_partial_row_emits_with_warning` — feeds an HTML snippet whose row has no `<p>` element; asserts the item is **still** in `result.items` after pipeline normalisation, `item.description == ""`, and a WARNING log line was emitted referencing the dedupe_key (use `caplog`). Observe RED.
- [ ] T006 [P] [US1] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_respects_limit` — uses the fixture, sets `limit: 5` in the source config, asserts exactly 5 items returned by `extract_from_html` (already supported by `generic_pipeline.py:168`). Observe RED.

### Implementation for User Story 1

- [ ] T007 [US1] Add the `github_trending` source entry to `sources.json` exactly as specified in `specs/001-github-trending/contracts/sources_json.md` (id, enabled=true, type=html, url, base_url, row_selector, limit=25, sheet_tab="github_projects", dedupe_key, fields, message_template). Run `python pipeline_config.py` (validates only) — must succeed silently.
- [ ] T008 [US1] Create `github_trending_pipeline.py` per `contracts/sources_json.md` "Pipeline contract": module exports `run_github_trending_pipeline(storage, notifier, sources_config=None) -> None`. Internal helper `_normalize_items(items)` strips leading `/` from `dedupe_key` and copies the normalised value into `title`. Filter sources by `id == "github_trending" and enabled`. Use `_fetch_html` pattern from `events_pipeline.py:30-33` (UA header, 30s timeout). After `extract_from_html`, run `_normalize_items`, then `storage.get_existing_keys("github_projects")` filter, then `storage.append_rows(...)` **before** `notifier.send_items(...)`. Log WARNING when an item's `metric` is empty. Re-run T004, T005, T006 — observe GREEN.
- [ ] T009 [US1] Add a new workflow step to `.github/workflows/run-script.yml` named "Run GitHub trending pipeline" running `python github_trending_pipeline.py`, **positioned immediately after** "Run JSON sources pipeline" and before "Run events pipeline" (preserves the source-order guarantee from FR-005a / research.md item 3). Env vars: `SPREADSHEET_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CREDENTIALS` (verbatim copy from the events step). No `GITHUB_TOKEN` (unauthenticated HTML fetch).

**Checkpoint**: User Story 1 is fully functional and testable independently. Running `python github_trending_pipeline.py` against the production environment produces Telegram messages for today's trending repos.

---

## Phase 4: User Story 2 — Don't spam the same repository on consecutive trending days (Priority: P2)

**Goal**: A repository visible on the trending page on consecutive days produces only one Telegram notification. Cross-source dedupe with `github_new_popular` shares the same scope.

**Independent Test**: Pre-populate the in-memory storage's `github_projects` tab with one fixture-derived `dedupe_key`. Run the pipeline against the same fixture. Assert zero notifications sent for that pre-seeded repo and the others are sent exactly once each.

### Tests for User Story 2 (REQUIRED — write FIRST, must FAIL before implementation) ⚠️

- [ ] T010 [P] [US2] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_skips_repo_already_in_shared_tab` — `InMemoryStorage` seeded with `github_projects: [["owner/repo", ...]]` where `owner/repo` matches one of the fixture's first entry's normalised key. Run pipeline. Assert notifier received N−1 messages where N is the fixture's row count and the skipped item's URL is absent from the sent set. Observe RED.
- [ ] T011 [P] [US2] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_dedupe_key_normalised_to_owner_repo` — runs the pipeline against the fixture, asserts every `NormalizedItem`'s `dedupe_key` matches the regex `^[\w.-]+/[\w.-]+$` (no leading slash, no `https://`). This is the property that lets dedupe rows interop with `github_new_popular`'s `full_name`-keyed rows. Observe RED.
- [ ] T012 [P] [US2] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_intra_run_overlap_uses_storage_state` — simulates the workflow ordering by: (a) running a small fixture through `extract_from_json` with a fake `github_new_popular` config and writing its items to `InMemoryStorage`, (b) then running `run_github_trending_pipeline` against a trending fixture that includes the same `owner/repo`. Assert the second source sends zero notifications for the overlapping key. Observe RED.

### Implementation for User Story 2

- [ ] T013 [US2] No new file. Verify the implementation from T008 already satisfies T010–T012 (the `storage.get_existing_keys("github_projects")` filter + the `lstrip("/")` normalisation are the entire mechanism). If any of T010–T012 still fail, fix `_normalize_items` or the storage-filter logic in `github_trending_pipeline.py` — root-cause, no shims. Run T010–T012 — observe GREEN.

**Checkpoint**: User Story 2 is functional. Cross-source dedupe with `github_new_popular` works against in-memory storage.

---

## Phase 5: User Story 3 — Visible failure when GitHub changes the trending page layout (Priority: P3)

**Goal**: Zero-row extraction and partial-row drift both reach the operator as a visible signal (non-zero exit / WARNING log / anomaly), never as silent zero output.

**Independent Test**: Point the pipeline at HTML that contains zero rows matching `article.Box-row`. The Python process exits with code 1. Telegram receives zero messages.

### Tests for User Story 3 (REQUIRED — write FIRST, must FAIL before implementation) ⚠️

- [ ] T014 [P] [US3] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_zero_row_extraction_signals_failure` — patches `_fetch_html` (via dependency injection of HTML content, not via `unittest.mock`) so the pipeline receives `<html><body></body></html>`. Asserts: `result.errors` non-empty, zero notifications sent, AND a top-level helper `_did_fail(...)` returns True. Observe RED.
- [ ] T015 [P] [US3] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_partial_row_logs_warning_for_missing_metric` — feeds an HTML row with `<h2><a href=/o/r></a></h2><p>desc</p>` (no metric span). Asserts the item is in `result.items`, `metric == ""`, and `caplog` recorded a WARNING line containing the dedupe_key. Observe RED.
- [ ] T016 [P] [US3] ⚠️ TEST FIRST in `tests/test_github_trending_pipeline.py`: add `test_main_exits_nonzero_on_zero_rows` — uses `subprocess.run([sys.executable, "github_trending_pipeline.py"], env={...stub env that points at an HTML file with no rows...})`. Asserts `returncode == 1`. (This test exercises the actual `__main__` block; mark as `@pytest.mark.slow` if needed.) Observe RED.

### Implementation for User Story 3

- [ ] T017 [US3] In `github_trending_pipeline.py`: introduce a module-level `_FAILED = False` (or equivalent functional signalling — final shape is implementer's choice) that flips to True whenever `extract_from_html` returns zero items AND non-empty errors. The `__main__` block calls `sys.exit(1)` when the flag is set after the source loop. WARNING log on empty `metric` was already added in T008 — verify it satisfies T015 here; if not, adjust. Re-run T014, T015, T016 — observe GREEN.

**Checkpoint**: All user stories are independently functional and verified by their own tests.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Tasks that don't fit inside a single user story but are required before merge.

- [ ] T018 [P] Live E2E smoke test in `tests/test_e2e_github_trending.py`: one test, `test_live_trending_returns_rows`. Performs a real `requests.get("https://github.com/trending?since=daily", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)`, calls `extract_from_html`, asserts ≥1 row with non-empty title and URL starting with `https://github.com/`. Skip mechanism: match the gating used by `tests/test_e2e_kinozal_titles.py` (network-availability guard). This satisfies the Constitution Quality Gates "E2E smoke test for HTML/external-API PRs" clause.
- [ ] T019 [P] Update `docs/architecture/test-coverage.md` to register the new test file `tests/test_github_trending_pipeline.py` (and `tests/test_e2e_github_trending.py`) with one row each in the appropriate category. Note: row schema in `ROW_HEADERS` is unchanged, so no schema-status flip needed in test-coverage.md.
- [ ] T020 Run `python scripts/ci_check.py` from repo root — must pass (ruff format + lint + pytest + mypy + lockfile drift). Fix anything red. Do not bypass `.githooks/pre-push`.
- [ ] T021 [P] Run the manual quickstart steps in `specs/001-github-trending/quickstart.md` end-to-end against the scratch Sheets + Telegram bot. Record any drift between the quickstart text and observed behaviour as an inline correction to `quickstart.md` (same commit). The "cross-source dedupe" step (#5) is the most important; if it fails, this PR is not ready to merge.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup, T001)**: no dependencies — can start immediately.
- **Phase 2 (Foundational, T002→T003)**: depends on T001 only because T001 has no real coupling here — practically T002 and T001 can run in parallel.
- **Phase 3 (US1)**: depends on Phase 2 completion (T003) — the validator must reject malformed configs before T007 modifies `sources.json`.
- **Phase 4 (US2)**: depends on Phase 3 completion — re-runs against the same pipeline module + sources.json entry.
- **Phase 5 (US3)**: depends on Phase 3 completion — visibility logic lives in the same module. Can proceed in parallel with Phase 4 (different test functions, same file; the [P] markers reflect parallelism only across files).
- **Phase 6 (Polish)**: depends on all stories complete. T020 is the final gate.

### Within each user story

- ⚠️ Tests MUST be written and FAIL before implementation (Constitution Principle I, NON-NEGOTIABLE).
- T002 → T003 (test before validator change).
- T004, T005, T006 → T008 (tests before pipeline module).
- T010, T011, T012 → T013 (tests before / during normalisation verification).
- T014, T015, T016 → T017 (tests before failure-signal logic).
- T007 (sources.json) and T009 (workflow yaml) have no test prerequisite of their own — they are pure config — but they MUST land in the same PR as T008 so the runtime never sees a half-configured state.

### Parallel opportunities

- **Phase 2**: T002 alone, then T003 alone. No parallelism.
- **Phase 3 tests**: T004, T005, T006 are independent test functions but live in the same file (`tests/test_github_trending_pipeline.py`). They can be authored in parallel; pytest runs them in parallel by default. [P] markers reflect logical parallelism, not file-level conflict.
- **Phase 3 impl**: T007 (sources.json) and T009 (run-script.yml) touch different files from T008 (github_trending_pipeline.py) and can be authored in parallel.
- **Phase 6**: T018, T019, T021 touch different files and can be authored in parallel; T020 must run last.

---

## Parallel Example: User Story 1 tests

```bash
# All three test functions can be authored in parallel (different test functions, same file is fine):
pytest tests/test_github_trending_pipeline.py::test_extracts_rows_from_fixture -v
pytest tests/test_github_trending_pipeline.py::test_partial_row_emits_with_warning -v
pytest tests/test_github_trending_pipeline.py::test_respects_limit -v
```

All three must be RED before T008 starts. All three must be GREEN before US2 phase begins.

---

## Implementation Strategy

### MVP First (US1 only)

1. T001 (fixture)
2. T002 → T003 (foundational validator)
3. T004–T006 (US1 tests, RED)
4. T007–T009 (US1 implementation, tests GREEN)
5. **STOP and VALIDATE**: run quickstart step 4 (write-before-notify) against scratch infra. If clean, this is already shippable as MVP — the cross-source dedupe (US2) is automatic via shared sheet_tab, the failure visibility (US3) is the polishing layer.

### Incremental Delivery

1. MVP as above → PR review → user approval.
2. T010–T013 (US2): proves cross-source dedupe explicitly. Same PR.
3. T014–T017 (US3): proves visibility. Same PR.
4. T018–T021 (Polish): E2E live test, docs, ci_check, manual quickstart. Same PR.
5. PR ready for human merge (no self-merge — Constitution Workflow rule 3).

### Parallel Team Strategy

Solo developer + AI agent — Parallel Team Strategy section is N/A here, but for future reference: with multiple developers, Phase 3 / Phase 4 / Phase 5 could be split between people once T008 lands.

---

## Notes

- **Tests are MANDATORY** (Constitution Principle I): every implementation task is preceded by ⚠️-marked test task(s) that must be RED before implementation starts and GREEN after it ends.
- **Commit cadence**: One commit per logical group (validator change, fixture, pipeline module, workflow yaml, docs). Bundle in a single PR closing #60.
- **No self-merge** (Constitution Workflow rule 3): when all tasks done, push, open PR, hand off.
- **Branch already correctly named**: `codex-issue-60-github-trending`, started from fresh `origin/main` via `python scripts/new_branch.py` (per Constitution Workflow rule 1).
- **`setup-tasks.sh` bypass**: this `tasks.md` was authored without invoking `.specify/scripts/bash/setup-tasks.sh` because of the Windows `python3` stub bug (see `memory/feedback_speckit_windows_python3.md`); template was copied conceptually but the file is written directly. No artefact lost.
- **Out of scope for this PR**: retrofitting non-zero exit into `events_pipeline.py` / `kinozal_pipeline.py` (separate follow-up issue per research.md item 5); LLM enrichment for trending (per research.md item 6); `generic_pipeline.py` transform DSL (per research.md item 4).
