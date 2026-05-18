# Implementation Plan: GitHub Trending source for late-bloomer repositories

**Branch**: `codex-issue-60-github-trending` | **Date**: 2026-05-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-github-trending/spec.md`

**Note**: This plan is the output of `/speckit-plan`. The `setup-plan.sh` script was bypassed manually because of a Windows-specific `python3` parser bug (see `memory/feedback_speckit_windows_python3.md`); plan template was copied by hand.

## Summary

Add a second GitHub source — `github.com/trending?since=daily` — as a new HTML-typed declarative entry in `sources.json`, served by a new thin pipeline module (`github_trending_pipeline.py`) modelled directly on `events_pipeline.py`. Both GitHub sources (existing `github_new_popular` + new `github_trending`) share one Google Sheets tab (`github_projects`) so dedupe spans both — a repository visible to either source produces at most one Telegram notification. No changes to `generic_pipeline.py`; the only library-level change is tightening `pipeline_config.validate_sources_config()` so HTML sources must declare `row_selector` at startup (Principle VI).

## Technical Context

**Language/Version**: Python 3.12 (matches `run-script.yml::setup-python` and existing pipelines).

**Primary Dependencies**: `requests` (HTTP), `beautifulsoup4` (HTML parse — already used by `generic_pipeline.extract_from_html`), `gspread` (Sheets storage, already used). No new runtime dependency.

**Storage**: Google Sheets via `SheetsStorage` (existing `Storage` Protocol implementation). Shared tab `github_projects` for both GitHub sources. Row schema unchanged: `ROW_HEADERS = ["dedupe_key", "title", "url", "metric", "source_id", "notified_at"]`.

**Testing**: `pytest` with in-memory `Storage`/`Notifier` doubles (existing pattern in `tests/test_*_pipeline.py`). HTML fixture saved under `tests/fixtures/github_trending/` for deterministic extraction tests. One live E2E smoke test (`tests/test_e2e_github_trending.py` style) hits the real URL and asserts ≥1 row with non-empty title/url — gated on network availability like the existing `test_e2e_kinozal_titles.py`.

**Target Platform**: GitHub Actions `ubuntu-latest` runner (production cron) + local Windows dev machines.

**Project Type**: Single-module Python application (not web/mobile/library). Source layout: flat `*.py` at repo root, `tests/` directory at root.

**Performance Goals**: No tight SLA — one execution per day per source. Each source run completes under ~30s (HTTP fetch + 25-row parse + ≤25 Sheets writes + ≤25 Telegram sends). Not a constraint that drives design.

**Constraints**:
- Must not introduce a new third-party dependency.
- Must not break the existing `github_new_popular` source's stored rows (they were keyed by `full_name = "owner/repo"`; new source must produce dedupe keys in that exact format).
- Must not require changes to `generic_pipeline.py`'s public contract (other sources depend on it).

**Scale/Scope**: ~25 trending repositories per day, deduped — steady-state means 0–25 new notifications per day. Sheet grows by a few thousand rows over a year, well within Sheets limits.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Justification |
|---|---|---|
| **I. Test-First (NON-NEGOTIABLE)** | PASS | Tasks in `/speckit-tasks` will list test tasks (one per Acceptance Scenario in `spec.md`) before implementation tasks. `tasks-template.md` already enforces this. |
| **II. Protocol Boundaries with DI** | PASS | New `github_trending_pipeline.run_github_trending_pipeline(storage, notifier, sources_config=None)` accepts `Storage` and `Notifier` Protocol instances (same signature as `events_pipeline.run_events_pipeline`). Tests use `InMemoryStorage` + `InMemoryNotifier`. No mocks of `extract_from_html` or `run_*_pipeline`. |
| **III. Write-Before-Notify Ordering** | PASS | `run_github_trending_pipeline` performs `storage.append_rows(...)` **before** `notifier.send_items(...)` — same ordering as `events_pipeline.py` (lines 73–77). |
| **IV. Visibility Over Silence** | PASS (new code) / DEFERRED (legacy) | New `github_trending_pipeline.py` exits non-zero on zero-row extraction. Existing `events_pipeline.py` and `kinozal_pipeline.py` retain their pre-existing silent-zero behaviour — out of scope for this PR; see Complexity Tracking below. Partial rows (missing description / metric) still emit — `_html_field` returns "" for missing selectors, the item still passes the `dedupe_key && title` check and is sent with the gap. WARNING log added when `metric == ""`. |
| **V. Root Cause Before Fix** | N/A | New feature, no bug to root-cause. The `setup-plan.sh` bypass was root-caused (Windows `python3` stub) and the fix is recorded as a follow-up scope, not as a workaround. |
| **VI. Fail-Fast Configuration** | PASS | `validate_sources_config()` is extended in the same PR to require `row_selector` for `type == "html"` sources. Test `test_config_validation_html_requires_row_selector` covers this. |

**Gate result**: PASS. Re-evaluate after Phase 1 design (see end of file).

## Project Structure

### Documentation (this feature)

```text
specs/001-github-trending/
├── plan.md              # This file
├── spec.md              # /speckit-specify output (already authored)
├── research.md          # Phase 0 — decisions on selectors, dedupe key shape, source ordering
├── data-model.md        # Phase 1 — entities and validation rules
├── quickstart.md        # Phase 1 — manual end-to-end verification recipe
├── contracts/
│   └── sources_json.md  # Phase 1 — JSON delta for the new `github_trending` entry
├── checklists/
│   └── requirements.md  # /speckit-specify output (already authored)
└── tasks.md             # /speckit-tasks output (not yet created)
```

### Source Code (repository root)

Single-project flat layout — no `src/` directory in this repo. Concrete files touched:

```text
github_trending_pipeline.py   # NEW — pipeline runner, modelled on events_pipeline.py
sources.json                  # MODIFIED — append `github_trending` source entry
pipeline_config.py            # MODIFIED — validate_sources_config() requires row_selector for type=html
.github/workflows/run-script.yml  # MODIFIED — add `python github_trending_pipeline.py` step
docs/architecture/test-coverage.md  # MODIFIED — register the new test file

tests/
├── fixtures/
│   └── github_trending/
│       └── trending_daily.html     # NEW — saved snapshot of github.com/trending?since=daily
├── test_github_trending_pipeline.py  # NEW — US1/US2/US3 acceptance via Protocol doubles
├── test_pipeline_config.py           # MODIFIED — add test_config_validation_html_requires_row_selector
└── test_e2e_github_trending.py       # NEW — live HTTP, ≥1 row check; skipped when no network
```

**Structure Decision**: Flat layout, no new package. New code lives next to existing pipelines (`json_pipeline.py`, `events_pipeline.py`, `kinozal_pipeline.py`) following the established "one file per pipeline" convention. No abstraction layer added — the new pipeline is ~60 lines and a thin orchestrator, not a class hierarchy.

## Phase 0: Research output

See [research.md](./research.md). Key resolved questions:

1. **Live CSS selectors** for `github.com/trending?since=daily` — verified.
2. **Shared dedupe key format** — both sources emit `"owner/repo"` (no leading slash). New source normalises after `extract_from_html`.
3. **Source ordering for FR-005a** — implicitly handled by workflow step order (`json_pipeline.py` runs before `github_trending_pipeline.py`).
4. **Why no `generic_pipeline.py` change** — local post-processing in the new pipeline is enough; introducing a `transform:` system in the shared module is rejected on YAGNI grounds.

## Phase 1: Design output

- [data-model.md](./data-model.md) — entities (`NormalizedItem` reuse, source config shape) and validation rules.
- [contracts/sources_json.md](./contracts/sources_json.md) — exact JSON delta to be added to `sources.json`.
- [quickstart.md](./quickstart.md) — manual verification: run pipeline against fixture, against live URL, against in-memory storage with seeded dedupe row.

### Constitution Check (post-design re-evaluation)

| Principle | Status | Re-evaluation notes |
|---|---|---|
| I | PASS | `tasks.md` (Phase 2) will pin one test per acceptance scenario. |
| II | PASS | `data-model.md` confirms no new Protocol introduced; existing `Storage`/`Notifier`/`Enricher` cover this source's needs. `Enricher` not used (no LLM summary requested for trending in v1 — matches issue #60 wording). |
| III | PASS | `quickstart.md` step 4 explicitly proves write-before-notify ordering by inducing a notifier failure and verifying the dedupe row is still present. |
| IV | PASS | `data-model.md` documents the "partial row → WARNING + emit" contract as a row-level invariant. Workflow non-zero exit on zero-row extraction is captured in `contracts/sources_json.md`. |
| V | N/A | Still new feature. |
| VI | PASS | `pipeline_config.py` validator change is in scope of this PR, with its own unit test. |

**Re-check result**: PASS for new code. One deliberate scope-limit deferral documented in Complexity Tracking below — does not block the gate.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation / Deviation | Why needed | Simpler alternative rejected because |
|---|---|---|
| Principle IV ("Visibility Over Silence") **partially upheld**: new `github_trending_pipeline.py` exits non-zero on zero-row extraction, but pre-existing `events_pipeline.py` and `kinozal_pipeline.py` are left as-is — they continue to log + return 0 when extraction yields zero rows. | This PR closes issue #60 (adding the trending source). Retrofitting two unrelated legacy pipelines in the same PR would (a) expand blast radius beyond #60's scope, (b) risk surprise red CI for `soldout_events` / `kinozal_movies` whose drift modes are not analysed in this spec, (c) violate the one-PR-one-logical-unit rule in `CLAUDE.md`. New code holds the line; legacy retrofit gets its own issue post-merge (research.md item 5). | "Fix legacy in this PR" — rejected: scope creep, would mix a feature-add with a behaviour-change to two unrelated sources; if the new exit-1 broke soldout/kinozal in cron tomorrow, root-causing would conflate two changes. "Leave new pipeline silent too" — rejected: constitution explicitly requires visibility, and the new code path has no pre-existing user expectation of silent zeros to preserve. |
