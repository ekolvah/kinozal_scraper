# Claude workflow adapters

**Question this document answers:** Which workflow steps Claude carries in this
repository, without becoming the source of the workflow contract.

Claude carries the whole agent-process plugin route — `/opsx:propose` →
`/opsx:apply` → archive → PR — whose steps are the plugin's `agent-process`
skill. The parts this repository owns on top of it are in
[`docs/architecture/agent-process.md`](../../docs/architecture/agent-process.md).
Do not duplicate either here.

On a bug change whose design reads, parses, or classifies external data,
`/opsx:propose` invokes the local `discovery` subagent before writing the
design and records the block it returns in the change's `proposal.md`
([§Discovery runbook](../../docs/architecture/agent-process.md#discovery-runbook)).
The plugin does not chain this step; this instruction is its only trigger.

When creating an issue, ask the user for priority and set the GitHub Project
field with `python scripts/set_issue_priority.py <N> <High|Medium|Low>`.
