---
status: "accepted"
date: 2026-10-01
decision-makers: ekolvah
---

# This repository runs the agent process from the `agent-process` plugin v2 and stops maintaining its own v1

## Context and Problem Statement

This repository authored the v1 agent process and carries all of it in-tree: the
issue-section contract (`scripts/validate_issue_sections.py`,
`.agents/orchestration/change-classes.yaml`), `/plan` and `/implement`, the local
`architect-reviewer` and `discovery` subagents, Codex as the default implementer
(`.agents/orchestration/roles.yaml`), and a control plane of about twenty scripts behind
[`agent-process.md`](../architecture/agent-process.md). [ADR-0011](0011-agentic-process-distribution-mechanism.md)
then chose how to export that process to other repositories, and #571 ruled that this
repository does not install its own plugin.

The exported process has since moved on without us. Release **3.2.8** of
`agent-process@agent-process-marketplace` (`ekolvah/agent-process-distribution`) is a v2
process that replaces the bespoke control plane with standards (plugin ADR 0027): an
OpenSpec change (`/opsx:propose` → `apply` → `archive`) carries the plan, Claude Code is
the only carrier (plugin ADR 0033), and the gates are managed workflows plus a ruleset
installed by `/agent-process:init` (plugin ADRs 0018, 0023, 0032). The plugin is already
loaded at user scope in this repository's sessions, next to v1: two processes and two
`architect-reviewer` agents are visible at once.

The question is whether this repository keeps maintaining v1 or adopts v2 and, if it
adopts, how it gets there without a window in which no gate blocks a merge to `main`.
This reverses #571, the copier-consumer design of #579, and the §VII rationale in
[`principles.md`](../architecture/principles.md#vii-simplicity-first) that rejected an external process package as
duplication.

## Decision Drivers

* Goal 2 of the [goal function](../architecture/principles.md#goal-function): every v1 script, test and doc
  is support surface this repository pays for, while the same guarantees are maintained
  upstream.
* At every point of the migration at least one required check blocks a merge to `main`;
  a step that removes a gate lands together with the step that installs its replacement.
* Project-specific content survives the switch: the #580 lesson is that generic exported
  docs dropped real operative content.
* Each step has a recorded, cheap rollback.

## Considered Options

* Adopt plugin v2 and decommission v1, in four ordered steps
* Keep v1 in-tree and stop tracking the plugin
* Keep v1 and add the plugin's v2 gates on top permanently

## Decision Outcome

Chosen: **adopt plugin v2 and decommission v1**, because it is the only option that
shrinks what this repository maintains; the gate-gap risk is handled by the ordering
below, not by keeping v1.

Maintainer decisions (2026-10-01):

* **Codex route is removed** with v1, following plugin ADR 0033: `.agents/**`, `.codex/`,
  `roles.yaml`, `AGENTS.md`, and the Codex-only scripts, tests and telemetry assets.
* **Plugin updates follow the plugin's standard channel.** The skill auto-updates at user
  scope from the `stable` branch; `release_drift` then reports the mismatch with
  `openspec/config.yaml` and blocks `start_change` until `/agent-process:init` is re-run,
  which produces an upgrade PR. The plugin's `dependabot.yml` ignores
  `ekolvah/agent-process-distribution*`, so dependabot does **not** carry plugin releases.
* **#579, #564 and #572 are closed as superseded** once this record merges.

### Quality declaration

`.github/agent-process-quality.json` (added in step A) keeps `scripts/ci_check.py` as the
single source of the check set:

* `setup` — the one-line form of the current `ci.yml` install step:
  `python -m pip install --upgrade pip && python -m pip install -r requirements.txt -r requirements-dev.txt && python -m pip install -e . --no-deps`.
  The reusable `quality.yml` installs nothing on its own; without `setup` the first run is red.
* `test` — `python scripts/ci_check.py`.
* `checks` — `python scripts/ci_check.py --list-checks`, a new flag printing the `CHECKS`
  registry as a JSON array; the reusable workflow runs each name as `test --only <name>` in
  its own job, which keeps the per-check parallelism of `ci.yml`.
  `tests/test_ci_check.py::TestStepParity` is re-pointed from `ci.yml` to this declaration.

### Protection end state

Before (recorded 2026-10-01): classic protection on `main`, required contexts `quality`,
`pr-link`, `agent-review`, strict up-to-date, `enforce_admins: true`; no rulesets.

After: one ruleset from `agent-process activate_protection` requiring exactly
`agent-process / quality` and `agent-review / agent-review`, strict up-to-date, no bypass
actors, plus deletion, non-fast-forward and pull-request rules (zero approvals). Classic
protection's required-context list is emptied **in the same sitting** as the ruleset
activation; `ci.yml`, `pr-link.yml` and the v1 review workflow are deleted only after that.
Merge ergonomics do not change: the classic setup already applied to admins and required
an up-to-date branch.

### Migration sequence

Each step is its own issue and PR; this record is PR 1 of #592.

1. **A — pre-install unblock.** Rename `.github/workflows/agent-review.yml` to
   `agent-review-v1.yml` with its job id unchanged, so the classic `agent-review` context
   survives. RED first: `scripts/review_gate.py` `REVIEW_WORKFLOW_FILE` and every reference
   to the old file name move in the same PR, otherwise the gate 404s and later silently
   resolves to the plugin's workflow. Add the quality declaration and
   `ci_check.py --list-checks`. Add `pre-commit` to `requirements-dev.in` with pip-compile.
   Do not reformat `.claude/settings.json`: the installer merges its block only when the
   file is byte-identical to `json.dumps(indent=2)`. Done when `init --dry-run` on a clean
   `main` prints no `conflict`.
2. **B — install and pilot.** `/agent-process:init --confirm` on a clean `main`. Before its
   PR merges, v2 `agent-process / quality` and `agent-review / agent-review` and v1
   `quality`, `pr-link`, `agent-review` are green on the same head. `.githooks` stays the
   active hook path until the pre-commit pre-push hook is shown to run the declared `test`
   against the repository venv on this machine; the hook strips the venv from `PATH` and
   resolves `bash` through `shutil.which`, so it is verified to be Git Bash, not WSL. Only
   then `core.hooksPath` is unset. Pilot: one small real backlog issue through
   `/opsx:propose → apply → archive`; plugin gaps are filed upstream, not patched here.
3. **C — protection cut-over**, right after the pilot to keep the double-review window
   short. `agent-process activate_protection --dry-run`, then `--confirm`, then empty the
   classic contexts. Before and after of `…/branches/main/protection` and `…/rulesets` are
   recorded as an issue comment.
4. **D — decommission v1.** RED first: a guard that every path marked *deleted* below is
   absent and referenced by no tracked file; then delete those paths and rewrite the docs
   marked *rewritten*. The guard is removed in D's last commit, since
   `tests/test_doc_links.py` already catches dangling doc links afterwards. Stale
   project-scope plugin installs of the sandbox repositories are uninstalled by hand.

Rollback: PR 1, A and D are reverted as PRs. B: revert, `claude plugin disable
agent-process@agent-process-marketplace`, `git config core.hooksPath .githooks`. C: delete
the ruleset and restore the classic contexts from the recorded "before".

### v1 → v2 mapping

Verdicts: **replaced** (the plugin provides it), **kept** (consumer-owned, survives D),
**deleted** (in D, no replacement needed), **rewritten** (kept, content changes in D),
**gap** (v1 guarantee v2 lacks, with its resolution).

| v1 path | Verdict | Replacement or reason |
| --- | --- | --- |
| `scripts/validate_issue_sections.py`, `.agents/orchestration/change-classes.yaml`, `tests/test_validate_issue_sections.py` | replaced | OpenSpec `validate --strict` over proposal/specs/design/tasks. `find_gaps`, which `tests/test_adr_records.py` imports, moves into that test in D |
| `.claude/commands/plan.md`, `.claude/commands/implement.md` | replaced | `/opsx:propose`, `/opsx:apply`, `/opsx:archive` |
| `.claude/agents/architect-reviewer.md` | replaced | `agent-process:architect-reviewer` writing `architect-review.json` |
| `.claude/agents/discovery.md`, `tests/test_agent_frontmatter.py` | gap → kept | v2 has no discovery role; §V still requires a live observation when a design depends on external behaviour. The subagent stays and is invoked from a proposal; the gap is filed upstream |
| `scripts/capture_external_fixture.py`, `scripts/capture_kinozal_fixture.py`, `tests/test_capture_external_fixture.py` | kept | Evidence capture against this repository's external systems |
| `scripts/check_fixture_ratchet.py` | gap → kept | Its only caller is the issue validator; D re-wires it as a `ci_check.py` check |
| `scripts/check_orphan_scope.py`, `tests/test_check_orphan_scope.py` | replaced | Tracked deferrals in the PR report (plugin ADR 0020) |
| `scripts/check_red.py`, `tests/test_check_red.py` | replaced | `agent-process check_red` |
| `scripts/agent_orchestrator.py`, `tests/test_agent_orchestrator.py`, `.agents/orchestration/state.example.json` | replaced | `start_change` and the OpenSpec task groups |
| `scripts/issue_branch.py`, `scripts/new_branch.py`, `tests/test_issue_branch.py`, `tests/test_new_branch.py` | replaced | `start_change` (branch from `origin/main` in its own worktree, Status In Progress) |
| `scripts/set_issue_status.py`, `tests/test_set_issue_status.py` | replaced | `agent-process set_status` |
| `scripts/set_issue_priority.py`, `tests/test_set_issue_priority.py` | gap → kept | v2 sets Status and Area, not Priority; the `Project 1` Priority field stays ours |
| `scripts/open_pr.py`, `scripts/update_pr_body.py`, `scripts/verify_pr_link.py`, their tests, `.github/workflows/pr-link.yml` | replaced | `archive_change` + `gh pr create`, and the `link` job of the managed quality workflow |
| `scripts/review_gate.py`, `scripts/gh_io.py`, `tests/test_review_gate.py` | replaced | `wait_for_pr` and the three-round limit; `resolve_review_thread` |
| `.github/workflows/agent-review.yml` (→ `agent-review-v1.yml` in A), `tests/test_agent_review_workflow.py`, `tests/_model_pin_policy.py` | replaced | managed `agent-review.yml` calling `reusable-agent-review.yml@v<version>` |
| `scripts/check_agent_review_outcome.py`, `scripts/request_codex_review.py`, their tests | deleted | Codex review carrier removed; the managed review job reports itself |
| `.github/workflows/ci.yml` | replaced | managed `agent-process.yml` calling `quality.yml@v<version>` with the declaration above |
| `scripts/ci_check.py`, `tests/test_ci_check.py` | kept | The declared `test`; gains `--list-checks` in A |
| `.githooks/pre-push` | replaced | `pre-commit` pre-push hook running the declared `test` |
| `scripts/check_branch_protection.py`, `tests/test_branch_protection.py`, `BRANCH_PROTECTION_ALLOW_DRIFT` | gap → accepted loss | The local pre-push drift check goes with `.githooks`. GitHub enforces the ruleset regardless, and `activate_protection --dry-run` shows the expected state on demand |
| `.agents/orchestration/roles.yaml`, `.agents/skills/**`, `.codex/hooks.json`, `scripts/codex_hooks.py`, `tests/test_codex_hooks.py`, `AGENTS.md` | deleted | Codex route removed (plugin ADR 0033) |
| `scripts/check_codex_otel_config.py`, `observability/codex/`, `tests/test_codex_otel_assets.py` | deleted | Codex telemetry; ADR-0007 becomes `deprecated` in D |
| `scripts/agent_policy.py`, `tests/test_settings_deny.py` | kept | The deny-list source `.claude/settings.json` is checked against; its Codex caller goes |
| `scripts/hooks.py`, `scripts/navigation_policy.py`, `scripts/token_trend.py`, their tests, `tests/test_settings_hooks.py` | kept | This repository's `PreToolUse`/`PostToolUse` hooks; the plugin owns only its marker block in `settings.json` |
| `.claude/settings.json` | kept | The installer adds its marker block; permissions and hooks stay ours |
| `.claude/rules/testing.md`, `tests/test_always_load_budget.py` | kept | Repository test discipline |
| `.claude/rules/workflow.md`, `.claude/rules/mindset.md`, `CLAUDE.md` | rewritten | Point at the plugin procedure; the Claude token tactics stay |
| `scripts/check_language.py`, `tests/test_language_policy.py` | kept | English-documentation policy (ADR-0005); a `ci_check` check |
| `.github/pull_request_template.md`, `tests/test_pr_template.py` | deleted | The v2 delivery report is the PR body |
| `scripts/agent_process_plugin.py`, `tests/test_agent_process_plugin.py`, `templates/agent-process-plugin/**` | deleted | The plugin is published from its own repository |
| `templates/agent-process/**`, `tests/test_agent_process_template.py`, `docs/architecture/agent-process-export.md` | deleted | v2 removed the copier mirror; the export manifest has no consumer |
| `docs/architecture/agent-process.md`, `tests/test_agent_process.py` | rewritten | Reduced to the consumer-owned parts (Priority, discovery, Evidence capture) and a pointer to the plugin skill |
| `docs/architecture/{ci,ci-local,ci-workflow,ci-agent-review,ci-branch-protection}.md`, `docs/architecture/coverage-gaps-{agent-tooling,quality-gates}.md`, `docs/architecture/project-map.md`, `docs/architecture/information-architecture.md` | rewritten | Describe the managed workflows, the ruleset and the pre-commit hook |
| `tests/test_doc_{headers,links,narrative}.py`, `tests/test_adr_records.py`, `tests/test_subprocess_encoding.py` | kept | Documentation and subprocess guards of this repository |
| `docs/adr/0003`, `0004`, `0009`, `0011` | kept | Append-only history; each gains a cross-link to this record when D touches it |
| `docs/architecture/principles.md` | rewritten | Amended in PR 1 (this record) |
| `.github/workflows/run-script.yml`, product tests and docs | kept | Out of the process; unaffected throughout |

v2 conventions that do not apply here: release-please (plugin ADRs 0030, 0031) is the
publisher's own release flow, not installed in consumers; telemetry left the v2 migration
(plugin ADR 0029), so ADR-0006 stays as is.

### Consequences

* Good, because about twenty process scripts, their tests and the export templates leave
  this repository; their maintenance moves upstream.
* Good, because the gates become managed, versioned workflows and a ruleset instead of
  repository-specific copies.
* Bad, because every plugin release is one maintainer-merged upgrade PR. Accepted: the
  update stays explicit, which [ADR-0011](0011-agentic-process-distribution-mechanism.md) required of any channel.
* Bad, because between B and C each PR is reviewed twice by Claude (v1 and v2 review
  jobs), and the Codex failover is gone. Accepted for a window kept short by running C
  right after the pilot.
* Bad, because `pre-commit` becomes a new dev dependency and the local branch-protection
  drift check is lost.
* Bad, because a plugin defect now blocks this repository's delivery. Mitigated by the
  per-step rollback and by filing gaps upstream rather than patching locally.

### Confirmation

PR 1 is gated by `tests/test_adr_records.py`, `tests/test_doc_links.py` and
`tests/test_language_policy.py`. Steps A–D are confirmed by their own issues: A by a
conflict-free `init --dry-run`, B by both gate sets green on the install PR's head, C by the
recorded before/after protection, D by the deleted-paths guard.

## Pros and Cons of the Options

### Adopt plugin v2 and decommission v1

* Good, because it is the only option that reduces the maintained surface.
* Bad, because it ties the process to an external release cadence.

### Keep v1 in-tree

* Good, because nothing changes and no migration risk is taken.
* Bad, because this repository keeps maintaining a process its own upstream has replaced,
  and the user-scope plugin keeps loading a second process next to it.

### Keep v1 and add v2 gates permanently

* Bad, because it doubles review cost on every PR and maintains both processes —
  duplication instead of reuse, a §VII violation in itself.

## More Information

* Issue: [#592](https://github.com/ekolvah/kinozal_scraper/issues/592) (umbrella; steps
  A–D are follow-up issues linked from it).
* Supersedes for this repository the consumer half of [ADR-0011](0011-agentic-process-distribution-mechanism.md) and #571;
  ADR-0011 stays `accepted` as the record of the export decision.
* Revisit this record if a plugin release removes a guarantee listed above as replaced, if
  upgrade PRs arrive more than weekly for a month, or if the plugin stops being maintained.
