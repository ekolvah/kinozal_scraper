## Context

See proposal.md §Why for the guard observations. Current state:

- `.claude/settings.json` `permissions.deny`: 13 git/`gh` entries plus `Bash(sleep:*)`.
  `tests/test_settings_deny.py` pins all 14 by substring (`FORBIDDEN_COMMANDS`).
- Ruleset 24323423 "agent-process default branch" (`gh api repos/ekolvah/kinozal_scraper/rulesets/24323423`):
  `active`, `refs/heads/main`, no bypass actors, rules `deletion`, `non_fast_forward`,
  `pull_request`, `required_status_checks`.
- The untracked change `otel-project-label` adds an `env` key to the same `settings.json`; the
  keys differ, so whichever PR merges second rebases without a semantic conflict.

## Goals / Non-Goals

**Goals:** one carrier for the git/`gh` denials — no deny entry in `.claude/settings.json`
matches a command the plugin's git guard covers; no doc names the deny block as the local
security carrier or claims a deny entry hides a hook's message.

**Non-Goals:** `Bash(sleep:*)` (outside the guard, kept by the issue's scope); the plugin's guard
itself; ADR 0013's historical "kept" row.

## Decisions

**D1. Delete all 13 git/`gh` entries in one edit, as duplication.** proposal §Why shows each
entry's command denied by the guard with its own reason, and the hook answering ahead of the deny
rule, so the entries never change what the agent sees while the plugin is loaded. Their only
effect is a backup for two states: the plugin not loaded (visible: the `SessionStart` marker) and
the gate file renamed (silent; ledger `AS`). Alternative — keep them as defense in depth: costs no
message, but keeps two hand-synced carriers of one rule plus a test that pins the copy; a guard
extension upstream would leave the local copy a stale subset. The person chose deletion
(goal 1: one carrier to maintain; the repository's practice of not insuring against states that
already raise a marker). The trade is the backup for those two states, listed under Dropped
guards.

**D2. Delete `tests/test_settings_deny.py` whole, not just its git part.** Only `sleep` would
remain in `FORBIDDEN_COMMANDS`. A dropped `sleep` entry costs only tokens and wall time (the
harness's Bash tool description also states "Foreground `sleep` is blocked"), which the
repository's rule classes as a resource-only regression: no guard test (`docs/architecture/testing.md`
§Rule: when a test is NOT worth writing).
Alternative — a one-entry test: maintenance for a regression that cannot break correctness.

**D3. Docs follow the carrier.** `ci-local.md` lists `git_guard` beside `navigation_policy` and
`memory_checkpoint` as the plugin's hooks gated on `agent-process.yml`; `project-map.md` drops the
"until #632" row and the `.claude/settings.json` row describes what is left (`SessionStart` hooks,
the `sleep` deny, the plugin marketplace); `testing.md` loses its `test_settings_deny` example;
ledger `AS` names the git guard among the plugin hooks whose gate a rename can silently turn off,
and loses its clause "a deny entry that shadows a plugin hook only drops its … message", which
proposal §Why disproves. Bare mentions are caught by one `git grep` (tasks 3.2).

**D4. No post-merge live check.** The issue's second "Done when" bullet (a live `gh pr merge`
denied with the guard's reason) already holds with the deny block loaded (proposal §Why), so a
probe after merge cannot tell this change apart. After merge #632 is closed with that observation
quoted; the first bullet is verified by task 2.1's grep.

## Dropped guards

The change drops the local deny entries and the test that pinned them. Per lost proof, the catcher
actually reached:

| Lost proof | Catcher |
| --- | --- |
| Each git/`gh` command is denied locally | Plugin `git_guard` (`PreToolUse` `Bash`), tested by the plugin's `tests/publisher/test_git_guard.py` in its own CI; observed here in proposal §Why |
| Plugin not loaded, so no guard | `.claude/agent-process-check.py` at `SessionStart` prints `agent-process skill not loaded (<reason>)` |
| Gate file `.github/workflows/agent-process.yml` renamed, so the guard silently stops | None automatic; ledger `AS` (accepted: installer-rendered file, revisit trigger kept) |
| Push to `main` / force push on `main` / deleting `main` | Ruleset 24323423 server-side (`pull_request`, `non_fast_forward`, `deletion`), unchanged |
| `gh pr merge` by the agent | Plugin guard only; the ruleset still requires the checks before any merge |
| `git reset --hard`, `git branch -D`, `--no-verify` | Plugin guard only; `--no-verify` on push is backstopped by `agent-process / quality` on the PR |
| `Bash(git push origin main:*)` also refused `git push origin main:<other>` | None needed: the guard allows it, and it does not touch `main` |

## Risks / Trade-offs

- [The plugin is disabled or fails to start] → local git guarding drops to the ruleset; the
  `SessionStart` marker makes it visible rather than silent (§IV).
- [A future plugin release narrows the guard] → the plugin's own tests and release notes; no
  local copy to drift.

## Migration / Rollback

One PR. Rollback is a revert: the entries and the test return together.
