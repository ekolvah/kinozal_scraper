## 0. Delivery start

- [x] 0.1 Run `agent-process start_change delete-principles-governance-layer --planner Claude --implementer Claude` (tracking issue 627); enter `.claude/worktrees/delete-principles-governance-layer` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and #627 shows In Progress.

## 1. RED

- [ ] 1.1 no RED: `skip_specs` deletion-and-docs change with no delta scenario. Dangling links are caught by `tests/test_doc_links.py`, bare mentions by the 4.4 grep (design D3).

## 2. Product facts and convention 4 land first (design D1, D2)

- [ ] 2.1 In `docs/architecture/runtime.md` §Configuration extend the `pipeline_config.py` bullet: `validate_sources_config()` is the central load-time validator; a new class of config error grows a check there in the same PR. In `docs/architecture/testing.md` §External-data capture routes add the `evidence/<change>/` rule: Git-ignored, working tree only until merge; the proposal carries the compressed observation record, never the full payload.
- [ ] 2.2 In `CLAUDE.md` §Dependencies state the rule itself (changing `requirements*.in` → `pip-compile` its lockfile in the same commit; `ci_check.py` catches drift) instead of pointing at `agent-process.md`; in `scripts/hooks.py` point the reminder, and in `pyproject.toml` (l. ~11) the `(workflow.md §7)` comment, at `CLAUDE.md` §Dependencies. Verify: `python -m pytest tests/test_hooks.py tests/test_doc_links.py -q` passes; commit.

## 3. Delete the layer (design D1, D3)

- [ ] 3.1 `git rm docs/architecture/principles.md docs/architecture/agent-process.md docs/architecture/ci-branch-protection.md .claude/rules/mindset.md .claude/rules/workflow.md scripts/set_issue_priority.py tests/test_set_issue_priority.py`.
- [ ] 3.2 `CLAUDE.md`: §Environment's `([mindset](…))` → the `agent-process` skill's Claude harness section; §Debugging §V link → plugin `principles.md#v-root-cause-before-fix`; §PR Workflow drops the repository-additions sentence; §Architecture decisions: Principles row → plugin URL (canon is the plugin's), Mindset row removed.
- [ ] 3.3 Retarget §I/§II/goal-function links to the plugin URL in `docs/architecture/testing.md` (lines ~12, ~318, ~350, ~363) and `.claude/rules/testing.md`; `ci-production.md` Quality Gates link → plugin `#quality-gates`; `ci-workflow.md` "`principles.md` is deliberately not edited" → "the plugin's `principles.md`"; `information-architecture.md`: the `principles.md §II` example → plugin §II, drop the "`principles.md` delegates the IA policy" sentence.
- [ ] 3.4 `ci.md`: remove the branch-protection router line; `ci-local.md`: replace the `ci-branch-protection.md` link with the inline clause of design D3.
- [ ] 3.5 `tests/test_always_load_budget.py`: delete `_EXPECTED_ALWAYS_LOAD` and `test_expected_files_are_in_scope` (design §Dropped guards), set `_BUDGET_BYTES` = new LF-normalised `CLAUDE.md` size + 700, rounded up to the next 100 (design D4); docstrings name `CLAUDE.md` instead of `mindset.md`. `tests/test_doc_headers.py`: drop the `ci-branch-protection.md` entry. Docstring mentions of `principles.md` in `test_doc_headers.py`, `test_doc_links.py`, `test_subprocess_encoding.py` and of `mindset.md` in `scripts/token_trend.py`, `tests/test_token_trend.py` → the plugin's principles / the skill's Claude harness section. Verify: `python -m pytest tests/test_always_load_budget.py tests/test_doc_headers.py tests/test_doc_links.py -q` passes; commit.

## 4. Project map, ledger, ADR links (design D3)

- [ ] 4.1 `docs/architecture/project-map.md`: remove the rows for `agent-process.md`, `mindset.md`, `workflow.md`, `principles.md`, `ci-branch-protection.md`, `set_issue_priority.py`; the global-`CLAUDE.md` row drops its `mindset.md` mirror clause; the `testing.md`, `evidence/` and pipeline-layer rows link the plugin §II, `testing.md#external-data-capture-routes` and `runtime.md` respectively.
- [ ] 4.2 `coverage-gaps-quality-gates.md`: `V` cites the plugin's one-PR-one-unit goal instead of `agent-process.md`; `X` drops the `set_issue_priority` sentences; `AD` inlines the recovery clause instead of the `ci-branch-protection.md` link; `AR` says "the plugin's `principles.md` §V" without "not this repository's copy". `coverage-gaps-agent-tooling.md`: drop `.claude/rules/mindset.md` from the stale-copy list.
- [ ] 4.3 ADRs 0003 (l. ~158), 0004 (l. ~135), 0011 (l. ~31), 0013 (l. ~16, ~32, ~37, ~180): each relative link to a removed file → `https://github.com/ekolvah/kinozal_scraper/blob/eee5599/<same path and anchor>`.
- [ ] 4.4 Verify: `git grep -n -e 'principles\.md' -e mindset.md -e '[^-]workflow\.md' -e 'agent-process\.md' -e ci-branch-protection -e set_issue_priority -- ':!openspec' ':!docs/adr'` prints no `mindset.md`, `workflow.md`, `agent-process.md`, `ci-branch-protection` or `set_issue_priority` line, and every `principles.md` line names the plugin's file (URL or "the plugin's `principles.md`"); `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py tests/test_adr_records.py tests/test_always_load_budget.py -q` passes; commit.

## 5. Verify

- [ ] 5.1 Run `openspec validate --strict --all`. Verify: exit 0.
- [ ] 5.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 6. Deliver

- [ ] 6.1 Run `agent-process archive_change delete-principles-governance-layer`, then `gh pr create --title "chore: delete-principles-governance-layer" --body-file <report>`; the report names #627, #612, #616 and #601 as plain references, carries the scenario map, and lists the post-merge follow-ups of design D5/D6 (Priority field, #601, memory file).
- [ ] 6.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
| --- | --- |
| — | n/a: `skip_specs: true`, no delta scenarios; dangling links are caught by `tests/test_doc_links.py`, bare mentions by the 4.4 grep (design D3) |
