# Tasks: Lift legacy ruff/mypy exclusions (PR3 of 3 — closes #45)

**Input**: [spec.md](./spec.md)

The structural work is done; this PR flips the gates and updates the
docs that referenced "legacy excluded files". One commit.

## Phase 1: Config edits

- [ ] **T1** `pyproject.toml`:
  - `[tool.ruff].extend-exclude` — drop `TelegramChannelSummarizer.py`,
    `crypto.py`, `telegram_summarizer.py`.
  - `[tool.mypy].exclude` — drop `^TelegramChannelSummarizer\.py$` and
    `^crypto\.py$`. Keep `^scraper\.py$`.
- [ ] **T2** `scripts/ci_check.py` — remove the `_LEGACY` constant and
      the `p.name not in _LEGACY` filter in `_find_modules()`.
- [ ] **T3** `.github/workflows/ci.yml` — in the `Type check` step's
      `find` command, drop the three `! -name "..."` filters.

## Phase 2: Doc sync

- [ ] **T4** `docs/architecture/ci.md`:
  - Remove the "Legacy files … are excluded from ruff and mypy"
    sentence under "Local pre-commit".
  - Rewrite "mypy excludes the same legacy files as `ci_check.py`,
    plus `.claude` directory" to reference only `scraper.py` +
    `.claude/`.
- [ ] **T5** `docs/architecture/runtime.md`:
  - Drop the "legacy code (excluded from linting)" parenthetical for
    `telegram_summarizer`.
  - Rewrite the "Legacy modules" section to a "Telethon-direct
    modules" note that explains the architectural distinction without
    claiming exclusion.

## Phase 3: Verify

- [ ] **T6** `python scripts/ci_check.py` — green; verify the mypy
      stage now reports the three files in its module count.
- [ ] **T7** `git grep -i` over the repo for residual mentions of the
      old exclusion state; clean up any I missed.
- [ ] **T8** Commit, push, `gh pr create` closing #45. No self-merge.

## Out of scope

- `scraper.py` exclusion (separate, predates this issue).
- Migrating away from `google.generativeai` (own issue).
