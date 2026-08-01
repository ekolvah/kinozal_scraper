# Claude planner adapter

**Question this document answers:** How Claude participates as the repository's
planner adapter without becoming the source of the workflow contract.

The canonical development workflow, roles, issue contract, delivery gates, and
agent provenance are in
[`docs/architecture/agent-process.md`](../../docs/architecture/agent-process.md).
Do not duplicate them here.

Claude fills the `planner` role in the default setup:

1. Use `/plan #N` for substantive features and bug fixes.
2. Research the repository, complete all required issue sections, and run the
   `architect-reviewer` once unless the issue explicitly records a trivial
   `skipped:` rationale.
3. Fill `## Agent handoff` with planner identity, successful validation, and
   `next role: implementer`; then run
   `python scripts/validate_issue_sections.py N`.
4. Hand off a passing issue to an implementer. The default Codex adapter is
   `$implement-issue #N`; Claude does not write implementation code or create
   the implementation branch.

When creating an issue, ask the user for priority and set the GitHub Project
field with `python scripts/set_issue_priority.py <N> <High|Medium|Low>`.
