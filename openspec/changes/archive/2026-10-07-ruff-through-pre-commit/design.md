## Context

The observations are in `proposal.md` §Why. Ruff runs from two bespoke callers (`ci_check.py`,
`hooks.py`); the plugin's `edit_lint` (upstream ADR 0034) is the standard edit-time trigger and
reads `.pre-commit-config.yaml`, which here declares no `pre-commit`-stage hook.

## Goals / Non-Goals

**Goals:** ruff declared once, in `.pre-commit-config.yaml`; `hooks.py` and its wiring gone; the
list of what stays in `project-map.md` (#612 acceptance 3).

**Non-goals:** moving mypy, detect-secrets, pip-audit or pytest into `pre-commit` hooks (mypy's
isolated venv would need a copy of the dependency set; the others are not per-file linters here);
the `permissions.deny` block (#632); the telemetry files (epic #614, track 2); fixture tooling
(#613).

## Decisions

### D1. Ruff runs as `astral-sh/ruff-pre-commit` hooks; `lint` runs the `pre-commit` stage

`.pre-commit-config.yaml` gains, after `# agent-process:end`:

```yaml
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.12
    hooks:
      - id: ruff-check
        stages: [pre-commit]
      - id: ruff-format
        stages: [pre-commit]
```

`rev` equals today's `ruff==0.15.12`, so the move changes no verdict. No `args`: both hooks read
`pyproject.toml`, and the v0.15.12 manifest gives both `ruff-check` and `ruff-format`
`--force-exclude`, so `[tool.ruff] extend-exclude` (which alone keeps the tracked
`.claude/agent-process-check.py` out) still applies to passed paths. `stages: [pre-commit]` keeps them out of the installed `pre-push`
run; they reach push and CI through `ci_check`. `check_lint` becomes
`python -m pre_commit run --hook-stage pre-commit --all-files --show-diff-on-failure` (upstream's
command verbatim); `check_format` and the `format` entry go, since `ruff-format` runs inside `lint`.

- **Standard and problem:** closes #628 / #612 (two bespoke ruff callers); the standard is
  pre-commit hook repositories read by every trigger, as upstream ADR 0034. No new script.
- **Alternative — `repo: local`, `language: system`, `entry: python -m ruff …`:** keeps the
  `requirements-dev` pin, but a system hook depends on whichever `python` the trigger inherits
  (the edit hook's environment is not the venv's by contract), and it is the bespoke shape the
  hook repository exists to replace.
- **Alternative — `ruff-format` with `--check`/`--diff`:** no rewrite, but the agent then
  reformats by hand on every finding; the live observation in §Why removes the reason for it.

Lost or changed proofs and their catchers:

| Before | After | Catcher actually reached |
| --- | --- | --- |
| `ruff format --check .` failed without touching files | `ruff-format` rewrites, `--show-diff-on-failure` fails the run with the diff | `agent-process / quality`, job `lint`, on every PR head (plugin `quality.yml` runs `ci_check.py --only lint`) |
| `ruff check .` saw untracked `.py` | `--all-files` passes tracked files only | the same CI job: every pushed file is tracked; locally the edit hook lints the file at edit time |
| `hooks.py` `setup_broken` marker when ruff could not run | `edit_lint`: "a missing `pre-commit` is a marker"; a failed hook-environment install is a non-zero `pre-commit` run → exit 2 to the agent | plugin `edit_lint` on each `Edit`/`Write` (upstream `tests/publisher/test_edit_lint.py`) |
| one ruff version via `requirements-dev.txt` for local and CI | one ruff version via `rev` for local, edit time and CI | `pre-commit` resolves the same `rev` everywhere; no second pin exists (D2) |

The local gate's format step can rewrite unformatted tracked files across the checkout (upstream's
stated "Bad" consequence); the agent commits only its own change's files.

### D2. `ruff` leaves `requirements-dev`

The hook's `rev` is the only pin. Keeping `ruff` in `requirements-dev.txt` would be the second
source of the tool version that `ci-tooling-decisions.md` names as the root reason of the #255
no-go (local↔CI drift, #153). With one pin that reason no longer applies to file linters, so the
entry is rewritten: file linters run as `pre-commit` hooks; gates with their own logic
(`requirements`, `imports`, `secrets` with its fixture exclusion, mypy) stay `CHECKS` scripts. The
three-way parity worry does not arise: CI still runs only `CHECKS`, and `lint` reaches the config
through `CHECKS`.

No new ADR: reverting is two lines of `ci_check.py`, one line of `requirements-dev.in` and the
hook block — a low cost of change (`information-architecture.md` §Decision records).
ADR-0013's sentence "ruff and mypy still come from `requirements-dev.txt`" described step B and
stays as written (accepted records are not rewritten); `ci-tooling-decisions.md` is the living
statement.

### D3. The #440 guard runs ruff through the pinned hook

`tests/test_ruff_first_party_imports.py` exercised `python -m ruff`, which D2 removes. It now builds
its temporary project as a git repository (`git init`), writes a `.pre-commit-config.yaml` holding
only the `ruff-pre-commit` repository at the `rev` read from the repository's own config (PyYAML),
with one `ruff-check` hook whose `args` are today's
`--no-cache --select I001 --config <repo pyproject>`, and runs
`python -m pre_commit run ruff-check --files tests/test_candidate.py`. Asserted outcome unchanged:
non-zero exit and `I001` in the output before and after the leaf module exists.

- **Alternative — keep `ruff` in `requirements-dev` for this test:** reintroduces the second pin
  of D2.
- **Alternative — delete the test:** loses the only check that the real binary honours
  `known-first-party` cold and warm (#440).

Cost: locally the hook environment is cached by pre-commit per `rev` (shared with `lint`), so the
test adds a `git init` and a pre-commit start (~0.5 s, upstream ADR 0034). In CI the `pytest`
matrix job has no pre-commit cache, so it clones ruff-pre-commit and installs ruff itself (network);
a failed fetch surfaces as pre-commit's error in the assertion message, not a skip.

### D4. No `pip-compile` hook; the reminder is deleted, not moved

Proposal §Why: the lockfiles are Windows output (`colorama` via `build`, `click`), and pip-tools
documents that output differs per environment. A `pre-commit`-stage hook also runs in `lint` on
Linux CI and would rewrite `requirements-dev.txt` on every run. So per #628, `check_requirements`
stays the catcher, and with the reminder gone `hooks.py` has nothing left.

What stops proving: the edit-time nudge to re-run `pip-compile` after editing a `requirements*.in`.
Catchers actually reached:

- a package added to an `.in` without a lockfile pin → `check_requirements` (`ci_check --only
  requirements`) at pre-push and in `agent-process / quality` on the PR head;
- a prod/dev pin mismatch → the same check;
- a changed specifier of a package already pinned (e.g. `foo>=2` while the lock holds `1.x`) →
  **no mechanical catcher**; `CLAUDE.md` §Dependencies states the rule. Recorded as ledger entry
  `AT` in `coverage-gaps-quality-gates.md` (wait-for-pain: a cross-platform lock, e.g.
  `uv pip compile --universal`, would make a hook deterministic).

### D5. The list of what stays lives in `project-map.md`

A new section, "Repository-owned process files that stay", one row per file or group with its
reason or owning issue: `CLAUDE.md`; `.claude/rules/testing.md` and the fixture tooling (#613);
`scripts/ci_check.py` and the `ci*.md` docs; `.pre-commit-config.yaml` hooks outside the block;
`.claude/settings.json` `SessionStart` `token_trend` hook and telemetry files (#614 track 2);
`permissions.deny` with `tests/test_settings_deny.py` (until #632); the doc guards
(`test_doc_headers`, `test_doc_links`, `test_adr_records`, `test_always_load_budget`,
`test_repo_layout`) with `information-architecture.md`; the `coverage-gaps-*` ledgers and ADRs. It
is the index's own question ("which file answers which question"), so it lives there rather than
in a new document.

## Risks / Trade-offs

- [First `lint` run on a machine without the hook environment needs the network] → same as
  `pip-audit` today; CI already fetches.
- [`--show-diff-on-failure` reformats files locally] → accepted, D1.
- [Plugin upgrade rewrites the config] → spec guarantees bytes outside the block are kept (§Why).

## Migration Plan

One PR. Rollback: revert it; `pip install -r requirements-dev.txt` restores `ruff`. After merge,
each clone's next `ci_check` or edit installs the hook environment once.
