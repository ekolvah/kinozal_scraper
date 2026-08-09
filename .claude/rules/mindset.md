# Mindset — agent operating mode in this repository

**Question this document answers:** which token tactics are specific to the Claude harness
in this repository’s main session. **Do not paraphrase a principle, goal function, or procedural rule here—link to it only.**

Always-load (without `paths:`): the tactics are needed in every session, not only when working with tests.

## Where things live

- **Goal function** (three priorities) and “scripts > instructions” —
  [`../../docs/architecture/principles.md#goal-function`](../../docs/architecture/principles.md#goal-function).
- **Principles §I–VII** — [`principles.md`](../../docs/architecture/principles.md):
  root cause → §V, visibility → §IV, test-first → §I, simplicity/minimal-diff → §VII.
- **Procedure** (roles, branch, PR discipline, gates, planner runbook, and architect-review
  contract) — [`agent-process.md`](../../docs/architecture/agent-process.md).
- **Tests**: consult [`testing.md`](testing.md) **before choosing the test level**—it is
  path-scoped (`tests/**`) and may load only after the strategy has been chosen.

## Claude harness token tactics (their home is here)

- **Reading files**: use `Grep` or `Read` with an `offset/limit` for the needed fragment *before* a whole-file
  `Read`. Read the whole file only when it is needed (especially expensive before compaction).
- **Spawning a subagent**: only for research requiring >3 round trips on a topic OR an independent parallel
  task. A cold-start agent costs more than a direct call for a single `Grep`/`Read`.
- **`TodoWrite`**: only if the task truly has ≥3 steps and context loss is likely; do not use it for a single-step
  or linear edit.
- **Be concise by default—in the response and in files on disk**: provide extensive analysis only when explicitly
  needed; `.md` files should stay substantive, without duplicate summary sections or boilerplate. An explicit instruction
  is needed: lowering `effort` reduces reasoning volume, not the visible response.
- **`MEMORY.md`**: consult the index at session start before re-“discovering” a fact
  (verify-before-act for stale facts).
- **Waiting for a long command**: make one foreground invocation with a clearly increased `timeout`. A loop of
  `sleep`/repeated `Read` operations on an output file is forbidden (`Bash(sleep:*)` in `permissions.deny`)—every
  idle iteration reprocesses the entire session context. Use the background only when the command is known to exceed
  the Bash tool limit. Command-specific timings are in `CLAUDE.md` §Environment.
- **Edit files with `Edit`/`Write`, not a heredoc script** (`python - <<'PY'`): the harness draws the changed
  file into context and retains it until the session ends.
