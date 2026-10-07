# CI and quality gates

**Question this document answers:** Which focused document explains each CI or quality-gate question.

This is the navigation entry for CI state. It links to the canonical document for each
question and does not repeat their rules. Production environment, operations, and operator
runbooks belong in [operations.md](operations.md), not here.

Keep rules with the one sentence that explains why they remain valid; move task history and
what a particular review caught to the issue or PR. A rejected tool belongs beside its gate, or
in [Rejected CI tooling](ci-tooling-decisions.md) when it has no gate-specific section.

- [Local CI gate](ci-local.md) — run and interpret the local pre-commit gate.
- [Continuous-integration workflow](ci-workflow.md) — CI job composition, lint ratchets, and document guards.
- [Production workflow](ci-production.md) — scheduled production execution.
- [Rejected CI tooling](ci-tooling-decisions.md) — consciously not adopted tools.

The agent-process plugin's managed workflows, `agent-process.yml` (quality, reading
`.github/agent-process-quality.json`) and `agent-review.yml` (review), run on every PR next to
the v1 jobs. They are the merge gate: the plugin ruleset requires them, and the v1 jobs block
nothing. They are rendered by the installer, so a change
goes through the plugin, not through an edit here.
