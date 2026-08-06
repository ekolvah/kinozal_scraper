---
name: implement-issue
description: Implement a reviewed GitHub issue in this repository. Use when the user asks to implement, fix, or deliver issue #N after planning; validate the issue contract, follow RED-to-GREEN, open the PR, and process its CI or review findings.
---

# Implement issue

Treat [the agent process](../../../docs/architecture/agent-process.md) as the
workflow contract. This skill is the Codex adapter for the `implementer` and
`fixer` roles; do not replace a missing plan with an invented implementation.

1. Run `python scripts/validate_issue_sections.py <N>`. If it fails, stop and
   direct the task to a `planner`; do not create a branch or edit production
   code.
2. Read the issue and the repository areas it names. Create the branch only
   with `python scripts/issue_branch.py <N>`.
3. Write the exact tests named in `## Test plan`, then run
   `python scripts/check_red.py <test paths>`. Commit successful RED evidence
   as `test: failing tests for #<N>`. A signature-only stub is permitted only
   when necessary for a test to import.
4. Implement `## Implementation outline`, running focused tests until they are
   green. Update `## Docs to update` and any ADR named by the issue.
5. Run `python scripts/ci_check.py` once in the foreground. Fix root causes,
   not symptoms.
6. Create the PR with `python scripts/open_pr.py`, using the repository
   template. Fill `## Agent record` exactly as
   [agent records and adapters](../../../docs/architecture/agent-process.md#agent-records-and-adapters)
   defines it; that section is the canonical field list, and this skill does
   not restate it. The provider-neutral control plane in
   `.agents/orchestration/roles.yaml` may report the next bounded action; it
   does not replace the checks below or invoke a provider.
7. Stay active through the review/fix loop. After every push, wait for all PR
   checks with `gh pr checks <PR> --watch`. For a red CI check, inspect its
   root cause with `gh run view <run-id> --log-failed`, fix it in a separate
   fixer commit, push, and repeat. Inspect the reviewer outcome and threads for
   the current head too; fix every actionable **blocking** finding in a separate
   fixer commit, push, and repeat. `should-fix` findings are published for the
   maintainer to decide on and do not gate the loop (#458). Do not report the PR
   ready for merge until the current head has
   no blocking finding and every required check passes; a `rework` outcome with
   its warning is ready, not unfinished. A skipped, missing,
   malformed, or pending review means `not ready`, not `clean`; report that
   external blocker instead of handing off the merge decision.

Never bypass hooks, force-push, push to `main`, self-merge, or use an agent
statement in place of a required script or GitHub check.
