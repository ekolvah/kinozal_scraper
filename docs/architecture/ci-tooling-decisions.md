# Rejected CI tooling

**Question this document answers:** Which CI tools are consciously not adopted and why.

## Consciously not adopted

**What belongs here:** “tool or rule Y was not adopted”—and only a whole tool
without its own gate section above (otherwise, a line at the gate's location). The other branches of the
“where the decision goes” route are in [`information-architecture.md`](information-architecture.md#decision-records), its canon.

- **`pre-commit` as the registry of every check (#255)—no-go; as the hook launcher and for file
  linters—adopted ([ADR-0013](../adr/0013-adopt-agent-process-plugin-v2.md), #598, #628).** The
  plugin's `quality` hook runs the declared `test` (`ci_check.py`) at push. The ruff hooks
  (`astral-sh/ruff-pre-commit`, `pre-commit` stage) are the file linters: the plugin's edit-time
  lint runs them per edited file
  ([upstream ADR 0034](https://github.com/ekolvah/agent-process-distribution/blob/v3.10.1/.agent-process/docs/adr/0034-edit-time-lint-runs-the-projects-pre-commit-config.md)),
  and the `lint` check runs them over all tracked files, so `CHECKS` stays the only registry CI
  reads. The hook `rev` is ruff's **only** pin: `ruff` is not in `requirements-dev`, so the
  #255 root reason—a second tool-version source, the local↔CI drift class of #153—does not
  arise. What stays rejected is moving the other gates into hooks: `requirements`, `imports` and
  `secrets` (with its fixture exclusion) have their own logic and would be `local` hooks with
  zero benefit, and `mypy`'s isolated venv cannot see project dependencies, forcing
  `additional_dependencies:`—a hand-copied duplicate of the dependency set outside
  `requirements.txt`. **Revisit:** a gate turns out to be a plain per-file linter.
- **`tox`/`nox` (#255)—no.** They solve a matrix of **Python versions**; the project is pinned to one, 3.12.
  **Revisit:** a real requirement for a multi-version matrix emerges.
- **Spec Kit (#114)—removed.** Its role—specification → plan → tasks—is covered by the
  agent-process plugin's OpenSpec route (`/opsx:propose` → `/opsx:apply`), which the repository
  consumes rather than owns ([ADR-0013](../adr/0013-adopt-agent-process-plugin-v2.md)). A second
  spec framework would put `/speckit-*` commands and spec files on top of the same contract.
  **Revisit:** a need emerges that the plugin route does not cover.
