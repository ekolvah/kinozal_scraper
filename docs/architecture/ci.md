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
- [Branch-protection status checks](ci-branch-protection.md) — required GitHub contexts.
- [Agent review workflow](ci-agent-review.md) — review evidence and model-pin policy.
- [Production workflow](ci-production.md) — scheduled production execution.
- [Rejected CI tooling](ci-tooling-decisions.md) — consciously not adopted tools.
