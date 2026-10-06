# Claude workflow adapters

**Question this document answers:** Which workflow steps Claude carries in this
repository, without becoming the source of the workflow contract.

Claude carries the whole agent-process plugin route — `/opsx:propose` →
`/opsx:apply` → archive → PR — whose steps are the plugin's `agent-process`
skill. The parts this repository owns on top of it are in
[`docs/architecture/agent-process.md`](../../docs/architecture/agent-process.md).
Do not duplicate either here.

When creating an issue, ask the user for priority and set the GitHub Project
field with `python scripts/set_issue_priority.py <N> <High|Medium|Low>`.
