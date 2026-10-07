## Why

Epic #616 (step 3) split the #612 audit into four PRs; this is **C** (#627): the local principles
and governance layer. A (#629/#630) and B (#637) are merged; D is #628.

Observations behind C (main @ `eee5599`, plugin 3.10.1):

- **The plugin carries the generic principles.** `gh issue view 316 -R
  ekolvah/agent-process-distribution` → `CLOSED/COMPLETED`; its PR #340 says: "The generic
  statements from a consumer's copy of `principles.md` now live in the plugin's
  `skills/agent-process/principles.md`. This lets the consumer delete its copy … Not taken: the
  consumer's type-label taxonomy, judged obsolete; its one-line-skip convention, already covered by
  the `planning` spec and the §I (c) exception; product facts, which stay in the consumer's product
  documents." The plugin file is public at `main` (`gh api
  repos/ekolvah/agent-process-distribution/contents/skills/agent-process/principles.md` →
  `skills/agent-process/principles.md`; repo `PUBLIC`).
- **The token tactics shipped.** `gh issue view 315 -R ekolvah/agent-process-distribution` →
  `CLOSED/COMPLETED`; the installed skill's `## Claude harness` section carries them.
- **The branch-protection doc has no upstream home by decision.** `gh issue view 311 -R
  ekolvah/agent-process-distribution` → `CLOSED/NOT_PLANNED`; epic #616 lists the file for deletion
  without a replacement.
- **Convention 5 has a plugin carrier.** The installed 3.10.1 skill, §Install: "its git guard denies
  `gh pr merge`, `gh repo delete`, a push to `main`, a force push, `--no-verify`, `git reset --hard`
  and `git branch -D`".
- **Every governance convention is now carried elsewhere or retired.** 1 → plugin goal 3 and §V's
  CI-unblock mitigation; 2 and 6 → declined upstream (above); 3 → the Priority setter, deleted
  here; 4 → pip-compile rule moves to `CLAUDE.md` §Dependencies, where `ci_check.py` already
  catches drift; 5 → plugin git guard. `agent-process.md` then holds only its own intro, and
  `.claude/rules/workflow.md` only a pointer that duplicates `CLAUDE.md` §PR Workflow.
- **Product facts already have homes.** Boundary table and ready-client rule: `runtime.md`,
  `storage.md`; confirmed delivery: `runtime.md`, `pipeline.md`; capture routes: `testing.md`;
  daily cron as E2E smoke: `ci-production.md`, `testing.md`. Two facts have none: the central
  config validator `pipeline_config.validate_sources_config()` (§VI) and the `evidence/` retention
  rule (§V).
- **#601 has no subject left.** It asks to fix three Quality Gates statements of the local file;
  PR #340 rewrote the same gates upstream ("the claim that the review verdict's hard block 'is not
  enforced' was false and is gone").

## What Changes

- **Delete** `docs/architecture/principles.md`, `docs/architecture/agent-process.md`,
  `docs/architecture/ci-branch-protection.md`, `.claude/rules/mindset.md`,
  `.claude/rules/workflow.md`, `scripts/set_issue_priority.py`, `tests/test_set_issue_priority.py`.
- **Move** the two homeless product facts: §VI's central validator into `runtime.md`
  §Configuration; the `evidence/` retention rule into `testing.md` §External-data capture routes.
- **Move** governance convention 4 (pip-compile in the same commit) into `CLAUDE.md`
  §Dependencies.
- **Retarget** every inbound reference (design D3): living docs to the plugin's public
  `principles.md` or the product doc; superseded/accepted ADRs to permalinks at `eee5599`.
- **Ratchet** `tests/test_always_load_budget.py`: the name-pin test goes (it could no longer
  fail) and the budget drops to the new total plus the existing allowance.
- **After merge (follow-ups listed in the PR):** delete the `Priority` field of GitHub Project 1
  (irreversible; asked first); close #601 as superseded; delete the out-of-repo memory that names
  the Priority field.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. `skip_specs: true`: the change deletes process documents and one process helper, and edits
documentation; no product behaviour changes, and `openspec/specs/` is empty.

## Impact

Removed:

- `docs/architecture/principles.md`
- `docs/architecture/agent-process.md`
- `docs/architecture/ci-branch-protection.md`
- `.claude/rules/mindset.md`
- `.claude/rules/workflow.md`
- `scripts/set_issue_priority.py`
- `tests/test_set_issue_priority.py`

Edited:

- `CLAUDE.md` — §Environment mindset pointer, §Debugging §V link, §PR Workflow, §Dependencies
  (convention 4 lands here), §Architecture decisions (Principles → plugin; Mindset row gone).
- `docs/architecture/runtime.md` — §Configuration names the central validator.
- `docs/architecture/testing.md` — §II/§I canon links → plugin; `evidence/` rule added to capture
  routes; goal-function link → plugin.
- `docs/architecture/project-map.md` — rows for the seven removed files gone; `global CLAUDE.md`,
  `testing.md`, `evidence/` and pipeline-layer rows retargeted.
- `docs/architecture/information-architecture.md` — `principles.md §II` example and the delegation
  sentence retargeted/removed.
- `docs/architecture/ci.md` — router line to `ci-branch-protection.md` removed.
- `docs/architecture/ci-local.md` — drift-check pointer no longer links the removed doc.
- `docs/architecture/ci-production.md` — Quality Gates link → plugin.
- `docs/architecture/ci-workflow.md` — `principles.md` mention → plugin's principles.
- `docs/architecture/coverage-gaps-quality-gates.md` — `V`, `X`, `AD`, `AR` references updated.
- `docs/architecture/coverage-gaps-agent-tooling.md` — `mindset.md` dropped from the stale-copy list.
- `docs/adr/0003-…`, `0004-…`, `0011-…`, `0013-…` — relative links to removed files → permalinks
  (broken-link fix, allowed by the ADR policy).
- `.claude/rules/testing.md` — §I/§II links → plugin.
- `scripts/hooks.py` — pip-compile reminder points to `CLAUDE.md` §Dependencies.
- `pyproject.toml` — the `(workflow.md §7)` comment points to `CLAUDE.md` §Dependencies.
- `scripts/token_trend.py`, `tests/test_token_trend.py` — `mindset.md` mention → plugin skill.
- `tests/test_always_load_budget.py` — name-pin test removed, budget lowered.
- `tests/test_doc_headers.py` — `ci-branch-protection.md` out of the read-budget list; docstring
  `principles.md` mention retargeted.
- `tests/test_doc_links.py`, `tests/test_subprocess_encoding.py` — docstring `principles.md`
  mentions retargeted.

Outside the repository, after merge: Project 1 `Priority` field deleted; #601 closed; one memory
file and its `MEMORY.md` line deleted.
