## 0. Delivery start

- [x] 0.1 Precondition (end of the propose run): the tracking issue exists (`create_tracking_issue`) and is a sub-issue of #612 (`gh api repos/ekolvah/kinozal_scraper/issues/612/sub_issues` lists it). Run `agent-process start_change delete-local-session-hooks --planner Claude --implementer Claude` (tracking issue 625); enter `.claude/worktrees/delete-local-session-hooks` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and the issue shows In Progress.

## 1. RED

- [x] 1.1 no RED: `skip_specs` deletion-and-docs change with no delta scenario. Dangling references are caught by test collection (an import of a deleted module fails it) and by the 5.3 grep (design D5).

## 2. Re-check the plugin hooks (design D1, D2)

- [x] 2.1 Re-run the observation recorded in proposal.md — Why against the plugin release installed at apply time: `navigation_policy pre-bash` denies a `cat README.md` payload naming `Read`, and `memory_checkpoint post-edit` flags a write under `.claude/projects/<slug>/memory/`. Record commands and output for the PR report. Stop and report if either no longer holds.

## 3. Navigation hooks (design D1, D3)

- [x] 3.1 Remove the two `PreToolUse` entries from `.claude/settings.json`; remove `pre-bash`/`pre-read` from `scripts/hooks.py` (docstring, `_PRE_TOOL_USE`, usage line, the `navigation_policy` import) and their cases from `tests/test_hooks.py`; `git rm scripts/navigation_policy.py tests/test_navigation_policy.py` (its deny-shadowing tests go with it, design D3).
- [x] 3.2 In `tests/test_doc_headers.py` replace the `read_budget_hint` import with a local `28_000`-byte constant compared to the file size, with a comment naming the plugin's budget as the source.

## 4. Memory checkpoint (design D2)

- [x] 4.1 From `scripts/hooks.py` remove `_MEMORY_DIR_RE`, `_is_memory_write`, `memory_write_signal`, the `memory_write` branch of `plan_checks`/`run_on_paths`, the `memory_write` signal kind, the #353 docstring paragraph, and the `re` import if nothing else uses it; remove `TestMemoryWriteGuard` and other memory cases from `tests/test_hooks.py`. Verify: `python -m pytest tests/test_hooks.py -q` passes.

## 5. Docs and ledger (design D3, D4)

- [x] 5.1 Point `.claude/rules/mindset.md` (the "policy is canonical in" sentence) and `docs/architecture/ci-local.md` §Session hooks at the plugin's `navigation_policy` and `memory_checkpoint` hooks; `ci-local.md` keeps the `on-edit` ruff and `pip-compile` checks and drops the sentence on why navigation entries stay out of `permissions.deny`. In `docs/architecture/project-map.md` drop the `navigation_policy.py` row and the `pre-bash`/`pre-read`/memory clauses of the `hooks.py` row.
- [x] 5.2 Retire `AM` in `docs/architecture/coverage-gaps-agent-tooling.md` and add `AS` there; in `docs/architecture/coverage-gaps.md` set the ID range to `A` through `AS` and list `AM` as retired with #612 in the agent-tooling bullet.
- [x] 5.3 Verify: `git grep -n -e 'scripts/navigation_policy' -e 'scripts\.navigation_policy' -e test_navigation_policy -e 'scripts\.hooks pre-' -e memory_write -e _MEMORY_DIR_RE -- ':!openspec' ':!docs/adr'` prints nothing, and `python -m pytest tests/test_hooks.py tests/test_doc_headers.py tests/test_doc_links.py tests/test_settings_deny.py tests/test_always_load_budget.py -q` passes; commit.

## 6. Verify

- [x] 6.1 Run `npx -y @fission-ai/openspec@1.13.0 validate --strict --all`. Verify: exit 0.
- [x] 6.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 7. Deliver

- [x] 7.1 Run `agent-process archive_change delete-local-session-hooks`, then `gh pr create --title "chore: delete-local-session-hooks" --body-file <report>`; the report closes the tracking issue, names #612 and #616 as plain references, carries the 2.1 observation and the scenario map.
- [ ] 7.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
| --- | --- |
| — | n/a: `skip_specs: true`, no delta scenarios; dangling references are caught by test collection and the 5.3 grep (design D5) |
