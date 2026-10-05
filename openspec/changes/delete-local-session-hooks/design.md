## Context

See proposal.md — Why for the split of #616 step 3 into A–D and the observations. Constraints:

- `docs/adr/` is append-only (ADR-0001); ADRs that name `navigation_policy.py` keep naming it.
- Project-map policy: one row per file, no tests.
- Every plugin hook is gated on `.github/workflows/agent-process.yml`; it is installer-rendered
  and stays.

## Goals / Non-Goals

**Goals:** no hook runs twice; every deletion leaves no dangling reference.

**Non-Goals:** B, C and D of the split (proposal.md — Why); the `permissions.deny` block and
`tests/test_settings_deny.py` (D3); the telemetry stack (#617).

## Decisions

### D1 — Navigation hooks: the plugin's copy replaces the local one

`scripts/navigation_policy.py` and `hooks.py pre-bash|pre-read` deny shell file reads and
over-budget `Read` slices, naming the cheaper call (#485, #534). Plugin 3.8.3 ships the same
policy as `PreToolUse` hooks (upstream #310), and they were observed firing in this repository
(proposal.md — Why), so the local copies go.

What stops being proven locally and its catcher:

- Policy behaviour and rule membership (`tests/test_navigation_policy.py`) → the plugin's
  `tests/publisher/test_navigation_policy.py`, run by the plugin's CI on its release head.
- Local wiring tests (`test_pretooluse_hook_is_wired_for_bash|read`) → go with the wiring.
- The read budget used by `tests/test_doc_headers.py` → a local constant of the same 28 000 bytes
  compared to the file size; drift from the plugin value is accepted (`AS`).
- The plugin hooks stay on → they are gated on the literal path
  `.github/workflows/agent-process.yml`. **Removal** is caught by the ruleset: "agent-process
  default branch" (active on `refs/heads/main`) requires `agent-process / quality`, which only
  that workflow reports, so a PR deleting it cannot merge. A **rename** that keeps the workflow
  `name:` and job id still reports the check, merges, and silently turns every plugin hook off;
  nothing catches it. Accepted: the file is installer-rendered, and the plugin says to rerun the
  installer rather than edit it, so a rename is not a change this repository makes. Recorded in
  `AS`.

### D2 — Memory checkpoint: the plugin's copy replaces the local one

`hooks.py on-edit` flags a write into `.claude/projects/<slug>/memory/` (#353). Plugin 3.8.3
ships it as `memory_checkpoint post-edit` (upstream #313), which asks consumers to drop the local
part. `_MEMORY_DIR_RE`, `_is_memory_write`, `memory_write_signal`, the `memory_write` branch and
signal kind, the #353 docstring paragraph and the `re` import if nothing else uses it go. The
`PostToolUse on-edit` wiring stays for ruff and `pip-compile` until D.

Proofs dropped: `TestMemoryWriteGuard` in `tests/test_hooks.py` (Windows backslash paths, the
`MEMORY.md` root, other subfolders) → the plugin's `tests/publisher/test_memory_checkpoint.py`,
which covers backslash and Cyrillic paths.

### D3 — The deny block stays; its shadowing tests go

The maintainer's rule (2026-10-05): agent behaviour belongs in the plugin, and a consumer holds no
agent prohibitions. #616 lists the `permissions.deny` block for deletion because upstream #312
closed as not planned. Deleting it now would leave `gh pr merge`, pushes to `main` and
`--no-verify` with no mechanical guard: the ruleset requires green checks only, so it does not
stop an agent's merge. Alternative "delete now per #616" was rejected for that reason;
alternative "keep the block permanently" contradicts the maintainer's rule. The block and
`tests/test_settings_deny.py` stay until a plugin release carries the guard; reopening upstream
#312 on the maintainer's rationale is a maintainer decision outside this change.

`tests/test_navigation_policy.py` also asserts that no deny entry shadows a navigation hook.
Those two tests are deleted with it, not moved: a shadowed hook only loses its "use this tool
instead" message, a regression that costs tokens, and `docs/architecture/testing.md` §"Rule: when
a test is NOT worth writing" gives such a regression no guard test; the deny block is temporary
besides. The `ci-local.md` sentence explaining why navigation entries are not in
`permissions.deny` goes with them.

### D4 — Ledger

- `AM` ("no guard on which commands the navigation policy covers") is retired as
  `AK`/`AL`/`AO`–`AQ` are: entry removed, ID listed as retired in the `coverage-gaps.md` router,
  attributed to #612. Its subject is plugin code now.
- `AS` in `coverage-gaps-agent-tooling.md`: the local read-budget constant copies a plugin value,
  and the hook semantics described in `CLAUDE.md` §Environment and `mindset.md` may drift from a
  later plugin release; nothing local detects it. A rename of
  `.github/workflows/agent-process.yml` would silently disable every plugin hook (D1). Revisit
  trigger: a plugin release changing the navigation hooks or their gate.

The router's ID range becomes `A` through `AS`.

### D5 — Catching dangling references

`tests/test_doc_links.py` checks Markdown links to `.md` files only; every reference this change
deletes is a `.py` path, a module or a symbol, so it is not the catcher. The catchers are: an
import of a deleted module or symbol fails test collection in `ci_check.py`; and the grep in task
5.3 over local symbols (`scripts/navigation_policy`, `scripts.navigation_policy`,
`test_navigation_policy`, `scripts.hooks pre-`, `memory_write`, `_MEMORY_DIR_RE`), which the
plugin's own names (`navigation_policy pre-bash`) do not match.

## Risks / Trade-offs

- [Plugin hook regresses in a later release] → the local copy no longer backs it up; the
  plugin's tests are the catcher, and the hook fails open, so a regression costs tokens, not
  work.
- [Deny block outlives its rationale] → D3 names its exit condition.
- [A deny entry later shadows a plugin hook] → the hook's message is lost, nothing else; accepted
  (D3).

Rollback: reverting the PR restores the local hooks; both copies then fire again, which is
harmless.
