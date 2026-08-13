---
name: plan-issue
description: Plan a GitHub issue in this repository before implementation. Use when the user asks to plan, scope, or structure issue #N; fill the required issue sections, perform the architect review, and hand a validated issue to an implementer.
---

# Plan issue

Treat [the agent process](../../../docs/architecture/agent-process.md) as the
workflow contract. This skill is the Codex adapter for the `planner` role.

Run the
[planner runbook](../../../docs/architecture/agent-process.md#planner-runbook)
as written; it is the canonical step list and this skill does not restate it.
Only the interface below is specific to this adapter:

- The issue number arrives as `#N` in the user's request.
- Use the planner runbook's three discovery sources: repository context first,
  user decisions when needed, and a captured live observation for plans about
  reading or classifying external data.
- There is no local reviewer subagent here, so perform the architect review
  yourself against
  [the architect review contract](../../../docs/architecture/agent-process.md#architect-review-contract),
  reading it and `docs/architecture/principles.md` rather than working from
  memory. What self-review does and does not give is defined there, not here.
- This adapter's provenance line, the first line of `## Architect review`:
  `reviewer: Codex $plan-issue #N self-review`.
- Write the body back with
  `gh issue edit <N> --body-file <file>`; keep the file out of the repository.
- Hand the passing issue to an implementer, by default `$implement-issue #N`.

Do not write implementation code, create the issue branch, or change labels:
those belong to the implementer and to the issue templates.
