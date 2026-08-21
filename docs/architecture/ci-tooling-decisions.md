# Rejected CI tooling

**Question this document answers:** Which CI tools are consciously not adopted and why.

## Consciously not adopted

**What belongs here:** “tool or rule Y was not adopted”—and only a whole tool
without its own gate section above (otherwise, a line at the gate's location). The other branches of the
“where the decision goes” route are in [`project-map.md`](project-map.md) §Canonical-home, its canon.

- **`pre-commit` (#255)—no-go.** **Root reason:** every hook pins a tool version through `rev:` and
  runs it in an **isolated venv**—a second source of the tool version besides
  `requirements-dev.txt` (today `python -m ruff`/`mypy` use the single locked version),
  meaning a systematic return of the same local↔CI drift class (#153). A sharp illustration is
  `mypy`: its isolated
  venv cannot see project dependencies, forcing `additional_dependencies:`—
  a manually copied duplicate of the dependency set outside `requirements.txt`. **The partial-migration
  trap:** file linters in `pre-commit`, other gates as scripts ⇒ two overlapping
  systems and **three-way** parity (`pre-commit` config ↔ `CHECKS` ↔ `ci.yml`), whose third
  edge is **unguarded**—more surface area instead of benefit. Half the checks are not
  file linters at all (`requirements`, `imports` have their own logic); under `pre-commit`, they would remain
  scripts in `local` hooks with zero benefit. **Revisit (wait-for-pain):** partial
  `pre-commit` only for file linters—*iff* contributors experience real pain from
  manual hook-version management.
- **`tox`/`nox` (#255)—no.** They solve a matrix of **Python versions**; the project is pinned to one, 3.12.
  **Revisit:** a real requirement for a multi-version matrix emerges.
- **Spec Kit (#114)—removed.** Its role—specification → plan → tasks—is covered by local
  `/plan #N` → `$implement-issue #N`, which lives in the repository, is gated by
  `scripts/validate_issue_sections.py`, and keeps the plan in the issue body rather than a separate
  artifact tree. The cost of an external framework is `/speckit-*` commands and spec files on top of the same
  contract. **Revisit:** a need emerges that the local flow does not cover.
