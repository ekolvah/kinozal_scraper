# Feature Specification: Lift legacy ruff/mypy exclusions for the Telegram summarizer files

**Feature Branch**: `codex-issue-45-lift-exclusions`

**Created**: 2026-05-18

**Status**: Draft

**Parent**: #45 (PR3 of 3 — closes the issue)

**Input**: PR1 (#90) refactored the three legacy files to Protocol-injected,
mypy-clean shape. PR2 (#91) added taxonomy-coverage tests. The exclusions
across `pyproject.toml`, `scripts/ci_check.py`, and `.github/workflows/ci.yml`
remained in place so behaviour changes could be reverted independently. With
both PRs merged, the gate flip is structural — and the docs that still
mention "excluded from ruff and mypy" become stale the moment this PR lands.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — quality gates uniformly enforced (P1)

As the engineer making changes to `telegram_summarizer.py`,
`TelegramChannelSummarizer.py`, or `crypto.py`, when I run
`python scripts/ci_check.py` (or push, triggering CI), I want ruff and
mypy to be applied to those files exactly like every other Python file
in the repo. No silent skip.

**Why this priority**: The whole point of #45 was to remove the
"production code with no quality gate" anomaly. PR1 + PR2 set up the
foundation; this PR completes it.

**Independent Test**: `python scripts/ci_check.py` reports
`27 source files` (or whatever the post-lift count is — exact number
verified at commit time) for the mypy stage, where today it reports 27
*excluding* the three legacy files. Manually introduce a type error in
`TelegramChannelSummarizer.py` → ci_check fails. Revert → green.

**Acceptance Scenarios**:

1. **Given** `pyproject.toml` with the three legacy entries removed from
   both ruff `extend-exclude` and mypy `exclude`, **When** I run
   `python -m ruff check .` and `python -m mypy <all .py>`, **Then**
   both pass against the merged content from PR1 + PR2.
2. **Given** `scripts/ci_check.py` with the `_LEGACY` set removed,
   **When** the script enumerates modules for type-checking, **Then**
   the three previously-excluded files are included in the list and
   pass.
3. **Given** the CI workflow `.github/workflows/ci.yml` with the three
   `! -name "..."` filters removed from the `find` invocation,
   **When** CI runs on this PR, **Then** the `Type check` step
   succeeds with the legacy files included.
4. **Given** the architecture docs (`ci.md`, `runtime.md`) updated to
   remove the "legacy files excluded" notes, **When** a developer reads
   the docs after this PR, **Then** they get a consistent story —
   nothing claims the files are excluded, because they no longer are.

## Requirements *(mandatory)*

- **FR-001**: `pyproject.toml` `[tool.ruff].extend-exclude` MUST NOT
  reference `TelegramChannelSummarizer.py`, `crypto.py`, or
  `telegram_summarizer.py`. All other entries unchanged.
- **FR-002**: `pyproject.toml` `[tool.mypy].exclude` MUST NOT reference
  `^TelegramChannelSummarizer\.py$` or `^crypto\.py$`. The
  `^scraper\.py$` exclusion stays.
- **FR-003**: `scripts/ci_check.py` `_LEGACY` set MUST be removed
  entirely. `_find_modules()` filtering no longer skips by filename.
- **FR-004**: `.github/workflows/ci.yml` `Type check` step's `find`
  command MUST drop the three `! -name "..."` filters. Everything else
  unchanged.
- **FR-005**: `docs/architecture/ci.md` lines that say _"Legacy files
  (`telegram_summarizer.py`, `TelegramChannelSummarizer.py`,
  `crypto.py`) are excluded from ruff and mypy"_ MUST be deleted. The
  `mypy excludes the same legacy files as ci_check.py, plus .claude
  directory` line MUST be rewritten to reflect the new state
  (only `.claude/` and `scraper.py`).
- **FR-006**: `docs/architecture/runtime.md` MUST drop the "legacy code
  (excluded from linting)" claim about `telegram_summarizer.py` and
  the "Legacy modules" section's "excluded from ruff and mypy"
  sentence. The structural note that these modules use Telethon +
  Gemini directly (not the generic pipeline) MAY stay — that
  distinction is still true and useful.
- **FR-007**: `python scripts/ci_check.py` MUST be green after the
  edits, with the three files now included in the mypy module list.

### Success Criteria

- **SC-001**: After this PR, `git grep -i "excluded from ruff\|excluded
  from linting\|_LEGACY\|TelegramChannelSummarizer.*exclude"` over the
  repo returns no matches in source/config (specs and old commit
  messages are fine).
- **SC-002**: Closes #45 — the issue's Definition of Done is fully
  satisfied: ruff clean without exclusions, mypy clean without
  exclusions, tests exist, docs match.

## Assumptions

- The local-only verification done in PR1 (mypy --strict + ruff clean
  on the three files against project config) is still valid post-PR2.
  This PR re-runs ci_check end-to-end to confirm.
- `scraper.py` exclusion is **not** in scope. That file pre-dates the
  cleanup, has its own issue trail, and #45's text focuses on the
  three Telegram-summarizer files only.

## Out of scope

- Migrating from `google.generativeai` to `google.genai` (separate
  issue suggested by the `FutureWarning` we see in tests).
- Reworking the cron workflow that runs these files.
- Adding more tests (PR2's coverage is judged sufficient for closing
  #45; further taxonomy gaps go in new issues).
