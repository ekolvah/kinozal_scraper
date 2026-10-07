## 0. Delivery start

- [x] 0.1 Run `agent-process start_change delete-git-deny-block --planner Claude --implementer Claude` (tracking issue 632); enter `.claude/worktrees/delete-git-deny-block` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and #632 shows In Progress.

## 1. RED

- [x] 1.1 no RED: `skip_specs` deletion of configuration a plugin hook carries, one test and doc mentions; no delta scenario. The guard's behaviour is the plugin's (proposal §Why records it per entry).

## 2. Delete the deny entries and their test (design D1, D2)

- [x] 2.1 In `.claude/settings.json` delete the 13 `Bash(git …)` / `Bash(gh …)` entries of `permissions.deny`, keeping `"Bash(sleep:*)"` and every other key byte-for-byte. Verify: `git grep -n -e 'Bash(git' -e 'Bash(gh' -- .claude/settings.json` prints nothing and `python -c "import json;print(json.load(open('.claude/settings.json',encoding='utf-8'))['permissions']['deny'])"` prints `['Bash(sleep:*)']`.
- [x] 2.2 `git rm tests/test_settings_deny.py`; in `tests/test_ruff_silence_rules.py` docstring drop "Mirrors `test_settings_deny.py` —" so the sentence starts "It pins *enforcement*…". Verify: `python -m pytest tests/test_ruff_silence_rules.py -q` passes; commit with the ticks of 2.1–2.2.

## 3. Docs (design D3)

- [x] 3.1 `docs/architecture/ci-local.md` §Edit-time lint, last paragraph: add the git guard (`git_guard`, `PreToolUse` `Bash`: risky git/`gh` commands, denied with the alternative) to the plugin hooks gated on `agent-process.yml`; replace "The security carrier stays local: `permissions.deny`, guarded by `tests/test_settings_deny.py`." with one clause that `permissions.deny` keeps only `Bash(sleep:*)`. `docs/architecture/project-map.md`: the `.claude/settings.json` row → "`SessionStart` hooks, the `sleep` deny entry and the plugin marketplace; risky git/`gh` commands are denied by the plugin's git guard, the ruleset remains final"; delete the `permissions.deny` / `tests/test_settings_deny.py` row. `docs/architecture/testing.md` l. ~317: drop ", `test_settings_deny` guards a security invariant". `docs/architecture/coverage-gaps-agent-tooling.md` `AS`: the plugin hooks named are the navigation policy, memory checkpoint and git guard; delete the clause "a deny entry that shadows a plugin hook only drops its 'use this tool instead' message" (false: proposal §Why, design D3) and keep "both cost tokens, not correctness" true of what remains; the git guard's absence when the plugin is not loaded is visible through the `SessionStart` marker; a gate-file rename silently turns the git guard off too; revisit trigger covers the git guard.
- [x] 3.2 Verify: `git grep -n -e test_settings_deny -e 'security carrier' -e '#632' -- ':!openspec' ':!docs/adr'` prints nothing; `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py -q` passes; commit with the ticks of 3.1–3.2.

## 4. Verify

- [x] 4.1 Run `openspec validate --strict --all`. Verify: exit 0.
- [x] 4.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 5. Deliver

- [x] 5.1 Run `agent-process archive_change delete-git-deny-block`, then `gh pr create --title "chore: delete-git-deny-block" --body-file <report>`; the report names the tracking issue and #616 as plain references, carries the scenario map, the Dropped guards table of design.md, the finding that the issue's premise does not hold (proposal §Why), and the post-merge follow-up of design D4 (close #632 quoting that observation).
- [ ] 5.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
| --- | --- |
| — | n/a: `skip_specs: true`, no delta scenarios; the removed entries are verified by the 2.1 grep, the guard by the plugin's `tests/publisher/test_git_guard.py`, its precedence over deny rules by the proposal §Why observation |
