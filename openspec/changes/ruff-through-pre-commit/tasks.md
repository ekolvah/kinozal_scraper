## 0. Delivery start

- [x] 0.1 Run `agent-process start_change ruff-through-pre-commit --planner Claude --implementer Claude` (tracking issue 628); enter `.claude/worktrees/ruff-through-pre-commit` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and #628 shows In Progress.

## 1. RED

- [ ] 1.1 In `tests/test_ci_check.py` add `class TestLint` with `test_lint_runs_the_commit_stage_hooks` (patch `subprocess.run` as `TestRunner` does, call `CHECKS["lint"]()`, assert the one command is `[sys.executable, "-m", "pre_commit", "run", "--hook-stage", "pre-commit", "--all-files", "--show-diff-on-failure"]`; design D1) and `test_ruff_is_pinned_only_by_the_hook` (`.pre-commit-config.yaml`, read with PyYAML, has the `https://github.com/astral-sh/ruff-pre-commit` repo with hooks `ruff-check` and `ruff-format` at `stages: [pre-commit]`, and `ruff` is absent from `requirements-dev.in` and `requirements-dev.txt`; design D2).
- [ ] 1.2 Rewrite `tests/test_ruff_first_party_imports.py` per design D3: `git init` the temporary project, write a config holding only the `ruff-pre-commit` repo at the `rev` read from the repository's `.pre-commit-config.yaml` with one `ruff-check` hook, `args: [--no-cache, --select, I001, --config, <repo pyproject>]`, run `python -m pre_commit run ruff-check --files tests/test_candidate.py` (`encoding="utf-8"`, `PYTHONUTF8=1`); assertions unchanged; the `rev` is read inside the test body, so before 2.1 the failure is in the body, not at collection. The module docstring names the hook instead of the venv binary.
- [ ] 1.3 Run `agent-process check_red tests/test_ci_check.py::TestLint::test_lint_runs_the_commit_stage_hooks tests/test_ci_check.py::TestLint::test_ruff_is_pinned_only_by_the_hook tests/test_ruff_first_party_imports.py::TestStableFirstPartyClassification::test_scripts_import_is_first_party_before_module_exists tests/test_ruff_first_party_imports.py::TestStableFirstPartyClassification::test_package_import_is_first_party_before_module_exists`. Verify: every id fails in its body; commit RED with the Group 1 ticks.

## 2. Ruff through pre-commit (design D1, D2)

- [ ] 2.1 Add the `astral-sh/ruff-pre-commit` block of design D1 after `# agent-process:end` in `.pre-commit-config.yaml`, with a one-line comment that edit-time lint and `ci_check lint` run its `pre-commit` stage.
- [ ] 2.2 `scripts/ci_check.py`: `check_lint` runs the design D1 command with a docstring saying why the stage (keeps the `pre-push` `quality` hook out) and that a formatter rewrite fails the run with the diff; delete `check_format` and the `format` entry; the module docstring stays accurate.
- [ ] 2.3 Remove `ruff` from `requirements-dev.in`; run `pip-compile --constraint=requirements.txt requirements-dev.in` (the lockfile header's command). Verify: the `.txt` diff removes only the `ruff` pin and its `# via` line.
- [ ] 2.4 Verify: `python -m pytest tests/test_ci_check.py tests/test_ruff_first_party_imports.py -q` passes and `python scripts/ci_check.py --only lint` exits 0 with no file rewritten (`git status --short` shows only this change's files); commit.

## 3. Delete the post-edit hook (design D4)

- [ ] 3.1 `git rm scripts/hooks.py tests/test_hooks.py`; delete the `PostToolUse` block of `.claude/settings.json`.
- [ ] 3.2 Drop the `scripts/hooks.py` entry of `tests/test_subprocess_encoding.py` (l. ~284); in `scripts/token_trend.py` (l. ~687) and `tests/test_token_trend.py` (l. 8) state the rule without "mirrors `scripts/hooks.py`".
- [ ] 3.3 Verify: `python -m pytest tests/test_subprocess_encoding.py tests/test_token_trend.py -q` passes; commit.

## 4. Docs and the list of what stays (design D1, D2, D4, D5)

- [ ] 4.1 `docs/architecture/ci-local.md`: gate order (l. ~22) reads "pre-commit-stage hooks (ruff check, ruff format) → detect-secrets → …"; §Session hooks becomes "Edit-time lint": the plugin's `edit_lint` runs the `pre-commit`-stage hooks on each edited file and a formatter may rewrite it; keep the navigation/memory and `permissions.deny` paragraph; drop the `hooks.py` text and the "unrelated to the `pre-commit` framework" sentence.
- [ ] 4.2 `docs/architecture/ci-workflow.md`: l. ~146 "`ruff check .` recurses through the full tree from cwd" → the `lint` run passes every tracked file; delete the `scripts/hooks.py` `errors="replace"` paragraph (l. ~192).
- [ ] 4.3 `docs/architecture/ci-tooling-decisions.md`: rewrite the `pre-commit` entry per design D2 (file linters adopted with the hook `rev` as the only pin, #628; gates with their own logic and mypy stay `CHECKS` scripts and why), linking upstream ADR 0034.
- [ ] 4.4 `docs/architecture/coverage-gaps-quality-gates.md`: `X` keeps only the `ci_check._tracked_files` example; add `AT` per design D4. `docs/architecture/coverage-gaps.md`: ID range "`A` through `AT`", quality-gates line lists `AT`.
- [ ] 4.5 `docs/architecture/project-map.md`: delete the `scripts/hooks.py` row; `.claude/settings.json` row says `SessionStart` hook and `permissions.deny`; `.pre-commit-config.yaml` row adds the repository's ruff hooks outside the plugin block; add the section of design D5.
- [ ] 4.6 Verify: `git grep -n -e 'hooks\.py' -e 'scripts\.hooks' -e 'python -m ruff' -e 'check_format' -e '"-m", "ruff"' -- ':!openspec' ':!docs/adr'` prints nothing; `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py tests/test_always_load_budget.py tests/test_adr_records.py -q` passes; commit.

## 5. Verify

- [ ] 5.1 Run `openspec validate --strict --all`. Verify: exit 0.
- [ ] 5.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 6. Deliver

- [ ] 6.1 Run `agent-process archive_change ruff-through-pre-commit`, then `gh pr create --title "chore: ruff-through-pre-commit" --body-file <report>`; the report names #628, #612 and #616 as plain references, carries the scenario map, and notes that #612 can be closed after merge (acceptance 3 is the new `project-map.md` section).
- [ ] 6.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
| --- | --- |
| — | n/a: `skip_specs: true`, no delta scenarios. Design decisions are pinned by `tests/test_ci_check.py::TestLint::test_lint_runs_the_commit_stage_hooks` (D1), `::test_ruff_is_pinned_only_by_the_hook` (D2) and `tests/test_ruff_first_party_imports.py` (D3); doc references by the 4.6 grep and `tests/test_doc_links.py` |
