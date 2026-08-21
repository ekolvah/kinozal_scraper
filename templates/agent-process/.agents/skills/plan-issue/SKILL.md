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
- Work through every source of answers the runbook step 2 names, in its order;
  the list is canonical there and is not enumerated here.
- There is no local discovery subagent here either, so run the observation
  yourself against
  [the discovery runbook](../../../docs/architecture/agent-process.md#discovery-runbook),
  reading it rather than working from memory. How far the observation goes and
  which route it may use are defined there, not here. This adapter's provenance
  line, the first line of the block it produces:
  `discovery: Codex $plan-issue #N self-discovery`. Write that block to a file
  outside the repository and run
  `python scripts/validate_issue_sections.py <N> --evidence-only --body-file <file>`
  before recording it in the body.
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
