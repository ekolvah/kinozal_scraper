# Production workflow

**Question this document answers:** How the scheduled production workflow is configured and what it runs.

## Production workflow (`run-script.yml`)

The production cron is counted as an **E2E smoke gate** in [the plugin's `principles.md`](https://github.com/ekolvah/agent-process-distribution/blob/main/skills/agent-process/principles.md#quality-gates) §Quality Gates—this is
the only facet of the production workflow that answers this file's question. Scheduling, step order,
the workflow's own `pytest` smoke gate, failure isolation, and alerting belong to one home,
[`operations.md` § Production workflow](operations.md#production-workflow-run-scriptyml).
