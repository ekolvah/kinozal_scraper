## Why

Epic #616, step 3, issue #632. `.claude/settings.json` `permissions.deny` holds 13 git/`gh`
entries that duplicate the agent-process plugin's git guard: two carriers of one rule, the local
one pinned by a test of its own. The plugin's carrier is the one that answers.

Observations (main @ `15942e3`, plugin 3.10.1):

- **The issue's premise does not hold: the hook already answers first.** With the 13 entries
  loaded, in this session: `gh pr merge 0` → `PreToolUse:Bash hook error: The person merges:
  report the PR and wait with \`agent-process wait_for_pr\`.` (the guard's reason, not a bare
  refusal); `sleep 0` → `Permission to use Bash with command sleep 0 has been denied.` (the
  project's deny rules were active in the same session). So the deny block does not hide the
  guard's alternative, and the issue's second "Done when" bullet holds before this change. What
  remains is the duplication; the person chose to remove it on that ground (design D1).

- **The blockers are cleared.** `gh pr view 358 -R ekolvah/agent-process-distribution` →
  `MERGED`, `2026-10-06T17:21:01Z` ("feat: ship-git-guard"). 3.10.1 is installed here (#636); its
  `hooks/hooks.json` wires `git_guard pre-bash` as a `PreToolUse` hook, gated on
  `.github/workflows/agent-process.yml`, which exists in this repository.
- **The guard covers every git/`gh` entry.** Each entry's command, piped as a `PreToolUse` payload
  into `sh <plugin>/bin/agent-process git_guard pre-bash` from the repository root, returned
  `permissionDecision: "deny"`: `gh pr merge 5` → "The person merges: report the PR and wait with
  `agent-process wait_for_pr`."; `git push origin main` and `git push origin HEAD:main` → "Push
  the branch and open a PR; …"; `git push --force-with-lease` and `git push -f` → "Add a commit on
  top …"; `git commit --no-verify -m x` and `git push --no-verify` → "Fix what the hook reports;
  …"; `git branch -D b` → "`git branch -d` deletes a merged branch; …"; `git reset --hard` →
  "`git stash` keeps the changes; …"; `gh repo delete x` → "Deleting a repository is the
  person's action." `git push --force` is the same `_push` branch as `-f` (`git_guard.py`
  l. 114–116) and is in the plugin's `tests/publisher/test_git_guard.py`.
- **`Bash(sleep:*)` is outside the guard** and stays, per the issue's scope.

## What Changes

- **Delete** the 13 git/`gh` entries of `permissions.deny`; `Bash(sleep:*)` remains the only entry.
- **Delete** `tests/test_settings_deny.py` whole (design D2).
- **Update** the docs that name the deny block as the local security carrier (design D3).
- **After merge:** close #632, quoting the observation above for its second "Done when" bullet
  (design D4).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. `skip_specs: true`: the change removes local configuration that a plugin hook now carries,
plus one test and doc mentions; no product behaviour changes.

## Impact

Removed:

- `tests/test_settings_deny.py`

Edited:

- `.claude/settings.json` — 13 git/`gh` deny entries removed.
- `docs/architecture/ci-local.md` — §Edit-time lint's closing paragraph names the plugin's
  `git_guard` among its hooks instead of the local security carrier.
- `docs/architecture/project-map.md` — `.claude/settings.json` row reworded; the
  `permissions.deny` / `test_settings_deny.py` row of the "process files remain" table removed.
- `docs/architecture/testing.md` — the `test_settings_deny` example in §Rule: when a test is NOT
  worth writing removed.
- `docs/architecture/coverage-gaps-agent-tooling.md` — ledger `AS` covers the git guard too and
  drops its false "a deny entry that shadows a plugin hook only drops its … message" clause.
- `tests/test_ruff_silence_rules.py` — docstring drops "Mirrors `test_settings_deny.py`".

Not edited: `docs/adr/0013-…` l. 181 (a decision record of what was kept then); 
`information-architecture.md` l. 18 (`permissions.deny` as a generic example of a keyed fact,
still true).
