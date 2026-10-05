# Rejected CI tooling

**Question this document answers:** Which CI tools are consciously not adopted and why.

## Consciously not adopted

**What belongs here:** “tool or rule Y was not adopted”—and only a whole tool
without its own gate section above (otherwise, a line at the gate's location). The other branches of the
“where the decision goes” route are in [`information-architecture.md`](information-architecture.md#decision-records), its canon.

- **`pre-commit` as a linter registry (#255)—no-go; as the hook launcher—adopted
  ([ADR-0013](../adr/0013-adopt-agent-process-plugin-v2.md), #598).** The agent-process plugin's
  `.pre-commit-config.yaml` declares one hook, `quality`, which runs the declared `test`
  (`ci_check.py`) in the pusher's environment, so no tool version gets a second source and
  `CHECKS` stays the only registry. What stays rejected is moving the checks themselves into
  `pre-commit` hooks. **Root reason:** every hook pins a tool version through `rev:` and
  runs it in an **isolated venv**—a second source of the tool version besides
  `requirements-dev.txt` (today `python -m ruff`/`mypy` use the single locked version),
  meaning a systematic return of the same local↔CI drift class (#153). A sharp illustration is
  `mypy`: its isolated
  venv cannot see project dependencies, forcing `additional_dependencies:`—
  a manually copied duplicate of the dependency set outside `requirements.txt`. **The partial-migration
  trap:** file linters in `pre-commit`, other gates as scripts ⇒ two overlapping
  systems and **three-way** parity (`pre-commit` config ↔ `CHECKS` ↔ the CI quality declaration), whose third
  edge is **unguarded**—more surface area instead of benefit. Half the checks are not
  file linters at all (`requirements`, `imports` have their own logic); under `pre-commit`, they would remain
  scripts in `local` hooks with zero benefit. **Revisit (wait-for-pain):** partial
  `pre-commit` only for file linters—*iff* contributors experience real pain from
  manual hook-version management.
- **`tox`/`nox` (#255)—no.** They solve a matrix of **Python versions**; the project is pinned to one, 3.12.
  **Revisit:** a real requirement for a multi-version matrix emerges.
- **Spec Kit (#114)—removed.** Its role—specification → plan → tasks—is covered by the
  agent-process plugin's OpenSpec route (`/opsx:propose` → `/opsx:apply`), which the repository
  consumes rather than owns ([ADR-0013](../adr/0013-adopt-agent-process-plugin-v2.md)). A second
  spec framework would put `/speckit-*` commands and spec files on top of the same contract.
  **Revisit:** a need emerges that the plugin route does not cover.
