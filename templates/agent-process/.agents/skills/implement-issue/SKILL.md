---
name: implement-issue
description: Implement a reviewed GitHub issue in this repository. Use when the user asks to implement, fix, or deliver issue #N after planning; validate the issue contract, follow RED-to-GREEN, open the PR, and process its CI or review findings.
---

# Implement issue

Treat [the agent process](../../../docs/architecture/agent-process.md) as the
workflow contract. This skill is the Codex adapter for the `implementer` role,
whose contract is
[the deterministic delivery flow](../../../docs/architecture/agent-process.md#deterministic-delivery-flow),
and for the `fixer` role, whose contract is
[the review-gate verdicts](../../../docs/architecture/agent-process.md#review-gate-verdicts).
Do not replace a missing plan with an invented implementation.

1. Run `python scripts/validate_issue_sections.py <N>`. If it fails, stop and
   direct the task to a `planner`; do not create a branch or edit production
   code.
2. Run `python scripts/set_issue_priority.py <N> --check`. If it fails, stop
   before branch creation and have the maintainer set the issue Priority.
3. Read the issue and the repository areas it names. Create the branch only
   with `python scripts/issue_branch.py <N>`.
4. Write the exact tests named in `## Test plan`, then run
   `python scripts/check_red.py <test paths>`. Commit successful RED evidence
   as `test: failing tests for #<N>`. A signature-only stub is permitted only
   when necessary for a test to import.
5. Implement `## Implementation outline`, running focused tests until they are
   green. Update `## Docs to update` and any ADR named by the issue.
6. Run `python scripts/ci_check.py` once in the foreground. Fix root causes,
   not symptoms.
7. Create the PR with `python scripts/open_pr.py`, using the repository
   template. Fill `## Agent record` exactly as
   [agent records and adapters](../../../docs/architecture/agent-process.md#agent-records-and-adapters)
   defines it; that section is the canonical field list, and this skill does
   not restate it. The provider-neutral control plane in
   `.agents/orchestration/roles.yaml` may report the next bounded action; it
   does not replace the checks below or invoke a provider.
8. Stay active through the review/fix loop, and let
   `python -m scripts.review_gate <PR>` end it — not your own reading of the
   findings. After every push:
   - `gh pr checks <PR> --watch` — wait for the checks to finish.
   - `python -m scripts.review_gate <PR>` — its exit code is the decision.
     - `0` (`ready-for-human`): stop, report the PR ready. Any `should-fix` or
       `nice-to-have` finding is published for the maintainer to decide on and
       is not another round.
     - `10` (`fix-blocking`): inspect the root cause — `gh run view <run-id>
       --log-failed` for a red CI check, the review run for a red
       `agent-review` — fix it in a separate fixer commit, push, go back to the
       first bullet.
     - `20` (`escalate`) / `30` (`review-pending`) / `2` (gh or capture
       failure): the PR is `not ready`. Report the named blocker to the
       maintainer instead of handing off the merge decision; for
       `review-pending`, re-run the gate once first, never in a polling loop.
   Record the final verdict in the PR's `## Agent record`.

Never bypass hooks, force-push, push to `main`, self-merge, or use an agent
statement in place of a required script or GitHub check.
