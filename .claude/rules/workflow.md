# Claude workflow adapters

**Question this document answers:** Which workflow roles Claude adapts in this
repository, without becoming the source of the workflow contract.

The canonical development workflow, roles, issue contract, delivery gates, and
agent provenance are in
[`docs/architecture/agent-process.md`](../../docs/architecture/agent-process.md).
Do not duplicate them here.

Claude is the selected `planner` adapter in the default setup: `/agent-process:plan #N` runs
the [planner runbook](../../docs/architecture/agent-process.md#planner-runbook)
and invokes the local `architect-reviewer` subagent.

Claude also carries `discovery` through the `discovery` subagent that same
`/agent-process:plan #N` run invokes on a bug issue whose Evidence block is not yet accepted.
There is no separate human entry point: the role is chained inside the planner
run, the way the architect review already is.

Claude also adapts `implementer` and `fixer` through `/agent-process:implement #N`, so one
agent carries an issue from plan to PR. It is a declared entry point, not the
default: `adapter:` in `.agents/orchestration/roles.yaml` still names Codex for
both roles, and the user picks the route.

When creating an issue, ask the user for priority and set the GitHub Project
field with `python scripts/set_issue_priority.py <N> <High|Medium|Low>`.
