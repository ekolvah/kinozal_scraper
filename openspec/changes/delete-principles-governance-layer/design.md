## Context

See proposal.md §Why for the upstream observations. Current state relevant to the approach:

- `principles.md` has ~60 inbound mentions across `CLAUDE.md`, `.claude/rules/`, 12 architecture
  docs, 4 ADRs, 3 scripts and 6 tests (Grep, main @ `eee5599`). `tests/test_doc_links.py` fails on
  a dangling relative link or anchor and skips external URLs by form (`_is_external`), so it is the
  catcher for every retarget below; prose mentions and docstrings are outside it.
- The always-load set is `CLAUDE.md` 4443 B + `mindset.md` 4193 B + `workflow.md` 654 B = 9290 B
  against a 10 000 B budget (LF-normalised, measured with the test's own rule).
- Project 1 has fields `Status`, `Priority`, `Area` among others (`gh project field-list 1 --owner
  ekolvah`). The plugin reads `Status` and `Area` (`start_change`, `create_tracking_issue`); nothing
  in the plugin reads `Priority` (Grep over the 3.10.1 skill: only review-thread priority `P0–P3`).

## Goals / Non-Goals

**Goals:** after this PR no repository file restates a generic principle or governance convention,
and no link dangles.

**Non-Goals:** `scripts/hooks.py` and its wiring (#628, D); the `permissions.deny` block and
`tests/test_settings_deny.py` (epic #616, after the #312 guard settles); `.claude/rules/testing.md`
itself (product tooling, #613); rewriting any ADR beyond broken-link fixes.

## Decisions

**D1. Delete `agent-process.md` and `.claude/rules/workflow.md` whole, not just their governance
parts.** Once the six conventions go (proposal §Why maps each to its carrier), `agent-process.md`
holds only an intro restating the plugin route and `workflow.md` only a pointer that `CLAUDE.md`
§PR Workflow already gives. Alternative — keep both as pointer stubs: two always-on or navigable
files that answer no question of their own, against §VII and the IA rule that a file answers one
question. Convention 4 (pip-compile) is the one repository-owned rule; it moves to `CLAUDE.md`
§Dependencies, which already names it and points at `ci_check.py`'s drift check.

**D2. Product facts go to the product doc that already owns the subject.** Only two lack a home:
§VI's central validator → `runtime.md` §Configuration (the `pipeline_config.py` bullet), §V's
`evidence/` retention (working tree only, Git-ignored, compressed record in the proposal) →
`testing.md` §External-data capture routes. Everything else in §II–§VI and Quality Gates is either
already in `runtime.md`/`storage.md`/`pipeline.md`/`testing.md`/`ci-production.md` or generic and in
the plugin file. Alternative — a local "project principles delta" file: PR #340 declined an
extension point, so nothing would read it.

**D3. Retarget by reader.** Living docs, `.claude/rules/testing.md`, `CLAUDE.md` and
script/test docstrings → the plugin's public file
`https://github.com/ekolvah/agent-process-distribution/blob/main/skills/agent-process/principles.md`
(with the same anchors: `#goal-function`, `#i-test-first-non-negotiable`, …), because the canon
moves with the plugin. ADRs 0003, 0004, 0011, 0013 → permalinks at `eee5599` to the file they cited,
because a decision record cites what was true then (precedent: ADR-0009 in #637). Mentions of the
token tactics (`mindset.md`) → the skill's `## Claude harness` section, named in prose (it lives in
the plugin cache, not at a stable repository path). `ci-branch-protection.md` inbound links in
`ci-local.md` and ledger `AD` are inlined as one clause ("recovery is a manual ruleset edit in
Settings → Rules") rather than linked, since #311 declined an upstream page. Bare prose mentions,
which `test_doc_links.py` cannot see, are caught by one `git grep` over the removed names outside
`openspec/` and `docs/adr/` (tasks 4.4).

**D4. Ratchet the always-load budget down.** The name-pin test goes (§Dropped guards); the budget is
set at apply time to the new `CLAUDE.md` size plus the existing ~700 B allowance, rounded up to the
next 100. The test's own docstring requires lowering after moving rules out, so freed space is not
banked. `test_path_scoped_rule_is_excluded` keeps its witness (`.claude/rules/testing.md`).

**D5. Delete the Priority field, not just the setter.** A field nothing writes drifts silently
(§IV); the plugin's Status/Area flow does not use it. Irreversible: the field's values on every
Project 1 item are lost. It is a **post-merge follow-up**, not an apply task: before the merge,
`main` still carries the setter and `workflow.md` that write the field, so deleting it earlier would
break `main` if the PR is not merged. The PR report lists it; after the person merges, on their
request, the agent shows `gh project field-list` output and asks before `gh project field-delete`.

**D6. Close #601, do not retarget it.** Its three statements described the local Quality Gates;
the plugin's Quality Gates were rewritten in PR #340 to the same effect. A retarget would be an
upstream issue with nothing left to fix.

### Dropped guards and what still catches

- `tests/test_set_issue_priority.py`: its subject (setter and field) is deleted; no proof is lost.
- `test_always_load_budget.py` `test_expected_files_are_in_scope` and `_EXPECTED_ALWAYS_LOAD`:
  deleted. With `mindset.md`/`workflow.md` gone the only entry would be `CLAUDE.md`, which
  `_always_load_files()` adds unconditionally, so the pin could never fail. A future always-load
  rule is still caught by the byte budget, and the `paths:` filter by
  `test_path_scoped_rule_is_excluded`, both run by `ci_check.py` `pytest` on every push.
- `test_doc_headers.py` entry for `ci-branch-protection.md`: subject deleted.
- **Lost without a local catcher:** nothing in this repository checks that a change honours the
  principles. That was already true — the plugin's `architect-reviewer` reads its own copy
  (upstream #316) — so the catcher stays the architect review at plan stage and `agent-review` on
  each PR head.

## Risks / Trade-offs

- [Token tactics leave always-load] Sessions that never load the `agent-process` skill no longer
  see them, and three `mindset.md` tactics are not in the skill's section at all: TodoWrite,
  Edit-over-heredoc, MEMORY.md consultation. Observed in the planning session (2026-10-06): its
  tool list has no `TodoWrite`, and its system context loads the `MEMORY.md` index; the
  Edit-over-heredoc tactic has no carrier. → Accepted loss for all three; revisit if
  `token_trend.py` shows the per-turn charge rising on non-process branches.
- [Upstream anchors drift] A plugin release renaming a `principles.md` heading breaks the `main`
  links silently (external URLs are not checked). → Accepted: liveness of external URLs is the class
  `test_doc_links.py` deliberately skips; a reader still lands on the right file.
- [Priority data loss] → confirmation at that step; the setter's `--check` mode is deleted in the
  same PR, so no tooling half-depends on the field.

## Migration Plan

One PR. Rollback is `git revert` of the PR for repository files. After merge, as follow-ups the
PR report lists: the Project field (D5, asked first; cannot be restored with its values), closing
#601 (D6), and deleting the out-of-repo memory that names the field.
