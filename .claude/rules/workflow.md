# Claude planner adapter

**Question this document answers:** How Claude participates as the repository's
planner adapter without becoming the source of the workflow contract.

The canonical development workflow, roles, issue contract, delivery gates, and
agent provenance are in
[`docs/architecture/agent-process.md`](../../docs/architecture/agent-process.md).
Do not duplicate them here.

Claude is the selected `planner` adapter in the default setup: `/plan #N` runs
the [planner runbook](../../docs/architecture/agent-process.md#planner-runbook)
and invokes the local `architect-reviewer` subagent. Claude does not write
implementation code or create the implementation branch; a passing issue is
handed to an implementer, by default Codex `$implement-issue #N`.

When creating an issue, ask the user for priority and set the GitHub Project
field with `python scripts/set_issue_priority.py <N> <High|Medium|Low>`.
