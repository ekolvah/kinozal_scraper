## Why

Epic #616 (step 3) re-scopes the #612 audit: every process file outside telemetry is either
delivered by the agent-process plugin or deleted. The maintainer split that step into four PRs on
2026-10-05, each tracked by a sub-issue of #612:

- **A — this change:** the local copies of the session hooks the plugin now ships;
- **B:** local subagents and the discovery layer (`discovery.md`, the `workflow.md` trigger,
  §Evidence and §Discovery runbook, `ci-agent-review.md`, `tests/test_agent_frontmatter.py`);
- **C:** principles and governance (`principles.md`, the governance conventions, `mindset.md`,
  `ci-branch-protection.md`, the Priority setter and Project 1 field, #601);
- **D:** ruff through `pre-commit` (`ci_check.py`, the ruff and `pip-compile` branches of
  `hooks.py on-edit`, `requirements-dev`) and the "what stays" list; closes #612.

Observations behind A (main @ `e49baae`, plugin 3.8.3):

- **The plugin ships both hooks this repository copies.** The installed
  `agent-process/3.8.3/hooks/hooks.json` wires `PreToolUse` `Bash` → `navigation_policy pre-bash`,
  `PreToolUse` `Read` → `navigation_policy pre-read` (upstream #310, PR #319) and `PostToolUse`
  `Edit|Write` → `memory_checkpoint post-edit` (upstream #313, PR #322), each gated on
  `.github/workflows/agent-process.yml`, which exists here. `.claude/settings.json` still wires
  `scripts.hooks pre-bash|pre-read|on-edit`, so every `Bash`, `Read` and memory write runs both
  copies.
- **The plugin hooks fire here (observed 2026-10-05, during the architect review).** The harness
  denied an `ls .github/workflows/agent-process.yml` with a message ending "Repository navigation
  goes through tools." and no "(#485)", which is the plugin's text, not the local one. Piping a
  `cat README.md` payload into `sh <plugin>/bin/agent-process navigation_policy pre-bash`
  printed a deny naming `Read("README.md")` (rc 0); piping a `Write` to
  `.claude/projects/slug/memory/x.md` into `memory_checkpoint post-edit` printed the checkpoint
  question (rc 2).
- **The memory part of `on-edit` is the plugin's now.** #313 asks consumers to "drop the memory
  part of the local `hooks.py` and its wiring once the plugin delivers it". The ruff and
  `pip-compile` parts of `on-edit` are not shipped (`edit_lint` runs the project's `pre-commit`
  stage, which has no ruff hook here yet); they are D's.
- **The deny list is not A's.** Upstream #312 (a plugin guard for merge, main-branch and
  irreversible git commands) was closed as not planned, and #616 lists the `permissions.deny`
  block for deletion. The maintainer decided on 2026-10-05 that agent behaviour, prohibitions
  included, belongs in the plugin, not in a consumer. Deleting the block before the plugin carries
  the guard would leave `gh pr merge` unguarded, so the block, `tests/test_settings_deny.py` and
  their documentation stay until a plugin release carries the guard.

## What Changes

- **Delete** `scripts/navigation_policy.py`, `tests/test_navigation_policy.py`, the
  `pre-bash`/`pre-read` branches of `scripts/hooks.py`, their cases in `tests/test_hooks.py`, and
  the two `PreToolUse` entries of `.claude/settings.json`.
- **Delete** the memory branch of `hooks.py on-edit` (`_is_memory_write`, `memory_write_signal`)
  and its cases in `tests/test_hooks.py`; `on-edit` keeps ruff and the `pip-compile` reminder
  until D.
- **Delete** the two deny-shadowing tests with `tests/test_navigation_policy.py` rather than
  moving them: the regression they guard costs tokens only (design D3).
- **Replace** the `read_budget_hint` import in `tests/test_doc_headers.py` with a local
  28 000-byte constant.
- **Ledger:** retire `AM` (its subject moved to the plugin); add `AS` for what can drift from
  the plugin unnoticed (the local read-budget constant, the hook semantics described in local
  docs).
- **Docs:** point `mindset.md`, `ci-local.md` §Session hooks and `project-map.md` at the plugin's
  hooks.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. `skip_specs: true`: the change deletes duplicated process tooling and edits
documentation; no product or spec-level behaviour changes.

## Impact

Removed:

- `scripts/navigation_policy.py`
- `tests/test_navigation_policy.py`

Edited:

- `scripts/hooks.py` — `pre-bash`, `pre-read` and the memory branch removed; `on-edit` keeps ruff
  and `pip-compile`.
- `tests/test_hooks.py` — the matching cases removed.
- `tests/test_doc_headers.py` — local read-budget constant.
- `.claude/settings.json` — the two `PreToolUse` entries removed; `PostToolUse on-edit` and
  `permissions.deny` stay.
- `.claude/rules/mindset.md` — the policy pointer names the plugin.
- `docs/architecture/ci-local.md` — §Session hooks describes the remaining `on-edit` checks and
  says the navigation policy and memory checkpoint are the plugin's.
- `docs/architecture/project-map.md` — `navigation_policy.py` row removed; `hooks.py` row
  trimmed.
- `docs/architecture/coverage-gaps-agent-tooling.md` — `AM` removed, `AS` added.
- `docs/architecture/coverage-gaps.md` — router ID range `A` through `AS`, agent-tooling bullet.

The `settings.json`, `mindset.md` and `ci-local.md` edits and the two wiring tests were delivered
first in #630 (design D6).

Outside the repository:

- The tracking issue is created at the end of the propose run (`create_tracking_issue`) and
  linked as a sub-issue of #612 through the GitHub sub-issues API; no plugin script links
  sub-issues.
