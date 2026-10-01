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
This reverses #571 and the copier-consumer design of #579. It does not reverse the
duplication argument of [§VII](../architecture/principles.md#vii-simplicity-first): the
plugin replaces the in-repository reviewer and hooks instead of running next to them.

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
  `tests/test_ci_check.py::TestStepParity` gains a parity against this declaration in A and
  keeps its `ci.yml` parity until `ci.yml` is deleted: until C, `ci.yml` still carries the
  required `quality` context, and a check added to `ci_check.py` meanwhile must reach both.

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

#592 is the epic. This record closes #596; steps A–D are #597–#600, each its own PR, in
that order. Each step's issue carries its file-level inventory, which its plan re-derives
against the repository of that time.

Documentation outside `docs/adr/` describes the implemented state
([information-architecture policy](../architecture/information-architecture.md#what-documentation-describes-current-state-not-history-or-ideas)),
so no step writes a plan or "until / after" wording into it. Each step's PR rewrites the
documents describing the state that step changes, to that state. `principles.md` therefore
does not change with this record: C rewrites its Quality Gates to the ruleset checks, D
rewrites its references to the v1 reviewer and the Governance delegation, and each of those
PRs carries its own §Governance approval.

1. **A — pre-install unblock.** Rename `.github/workflows/agent-review.yml` to
   `agent-review-v1.yml` with its job id unchanged, so the classic `agent-review` context
   survives. RED first: `scripts/review_gate.py` `REVIEW_WORKFLOW_FILE` and every reference
   to the old file name, the docs included, move in the same PR, otherwise the gate 404s and
   later silently resolves to the plugin's workflow. Add the quality declaration and
   `ci_check.py --list-checks`. Add `pre-commit` to `requirements-dev.in` with pip-compile.
   Do not reformat `.claude/settings.json`: the installer merges its block only when the
   file is byte-identical to `json.dumps(indent=2)`. Done when `init --dry-run` on a clean
   `main` prints no `conflict`.
2. **B — install and pilot.** `/agent-process:init --confirm` on a clean `main`. Before its
   PR merges, v2 `agent-process / quality` and `agent-review / agent-review` and v1
   `quality`, `pr-link`, `agent-review` are green on the same head. `.githooks` stays the
   active hook path until the pre-commit pre-push hook is shown to run the declared `test`
   against the repository venv on this machine. The hook removes only its own pre-commit
   venv from `PATH`, so `python` resolves through the pusher's `PATH`, and it resolves
   `bash` through `shutil.which`; both are verified here (the repository venv, Git Bash
   rather than WSL). Only then `core.hooksPath` is unset. Pilot: one small real backlog issue through
   `/opsx:propose → apply → archive`; plugin gaps are filed upstream, not patched here.
3. **C — protection cut-over**, right after the pilot to keep the double-review window
   short. `agent-process activate_protection --dry-run`, then `--confirm`, then empty the
   classic contexts. Before and after of `…/branches/main/protection` and `…/rulesets` are
   recorded as an issue comment. C's PR rewrites the Quality Gates of `principles.md` and
   `ci-branch-protection.md` to the ruleset, and is the first PR merged under it. A
   throwaway PR from a branch with no linked issue confirms that `agent-process / quality`
   fails on it.
4. **D — decommission v1.** RED first: a guard that every path D deletes is absent and
   referenced by no tracked file outside `docs/adr/`, whose records keep naming what they
   decided. Then delete those paths, rewrite the remaining docs to the implemented state and
   set ADR-0003, ADR-0004 and ADR-0007 to `deprecated`. The fixture-ratchet
   scan moves out of the validator tests in the same commit that deletes them. The guard is
   removed in D's last commit, since
   `tests/test_doc_links.py` already catches dangling doc links afterwards. Stale
   project-scope plugin installs of the sandbox repositories are uninstalled by hand.

Rollback: this record, A and D are reverted as PRs. B: revert, `claude plugin disable
agent-process@agent-process-marketplace`, `git config core.hooksPath .githooks`. C: delete
the ruleset and restore the classic contexts from the recorded "before".

### v1 → v2 mapping

The verdict per v1 area; the files behind each row are listed in the step issue named in
the Step column. Verdicts: **replaced** (the plugin provides it), **kept** (consumer-owned,
survives the migration), **deleted** (no replacement needed), **rewritten** (kept, rewritten
to the implemented state by the step that changes what it describes), **gap** (a v1
guarantee v2 lacks, with its resolution).

| v1 area | Verdict | Step | Replacement or reason |
| --- | --- | --- | --- |
| Issue-section contract, `/plan`, `/implement`, local `architect-reviewer` | replaced | D | OpenSpec `validate --strict` over proposal/specs/design/tasks; `/opsx:propose`, `/opsx:apply`, `/opsx:archive`; `agent-process:architect-reviewer` writing `architect-review.json` |
| Control-plane scripts: orchestrator, branch, Status, `check_red`, orphan scope, PR opening, review gate | replaced | D | `start_change`, `set_status`, `agent-process check_red`, tracked deferrals (plugin ADR 0020), `archive_change` + `gh pr create`, `wait_for_pr` with the three-round limit |
| PR-link check (`pr-link.yml`) | replaced | C, D | The `link` job of the managed quality workflow. It reports as `agent-process / link`, which the ruleset does not list; the required `agent-process / quality` job `needs` it and passes only when every needed job succeeded, so an unlinked PR stays blocked |
| `ci.yml` | replaced | B, D | Managed `agent-process.yml` calling `quality.yml@v<version>` with the declaration above |
| v1 review workflow | replaced | A renames, D deletes | Managed `agent-review.yml` calling `reusable-agent-review.yml@v<version>` |
| `.githooks/pre-push` | replaced | B | `pre-commit` pre-push hook running the declared `test` |
| `scripts/ci_check.py` | kept | A | The declared `test`; gains `--list-checks` |
| Codex route: roles, skills, hooks, `AGENTS.md`, review carrier, telemetry | deleted | D | Plugin ADR 0033; ADR-0007 becomes `deprecated` |
| Export machinery: plugin build, copier mirror, export manifest, PR template | deleted | D | The plugin is published from its own repository; v2 removed the copier mirror; the delivery report is the PR body |
| `discovery` subagent and Evidence capture | gap → kept | — | v2 has no discovery role; §V still requires a live observation when a design depends on external behaviour. The subagent is invoked from a proposal; the gap is filed upstream |
| `Project 1` Priority field | gap → kept | — | v2 sets Status and Area, not Priority |
| Fixture ratchet | gap → kept | D | Its repository scan runs only inside the validator tests D deletes; it moves to its own test in the same commit |
| Local branch-protection drift check | gap → accepted loss | D | GitHub enforces the ruleset regardless, and `activate_protection --dry-run` shows the expected state on demand |
| Repository harness: deny-list, tool hooks, `.claude/settings.json`, test rules, language policy, doc and subprocess guards | kept | — | Consumer-owned; the plugin owns only its marker block in `settings.json` |
| Process documentation, `CLAUDE.md`, `.claude/rules/` | rewritten | A–D | `agent-process.md` is reduced to the consumer-owned parts and a pointer to the plugin skill |
| `principles.md` | rewritten | C, D | Quality Gates in C; the v1 reviewer references and the Governance delegation in D |
| ADR-0003, ADR-0004 | rewritten | D | Status `deprecated`, as ADR-0007: the review failover carrier and the controller-PR token rule they decide leave with v1 review |
| ADR-0009, ADR-0011 | kept | — | Append-only history; ADR-0011 links to this record |
| Product: `run-script.yml`, product code, tests and docs | kept | — | Out of the process; unaffected throughout |

v2 conventions that do not apply here: release-please (plugin ADRs 0030, 0031) is the
publisher's own release flow, not installed in consumers; telemetry left the v2 migration
(plugin ADR 0029), so ADR-0006 stays as is.

### Earlier tooling decisions

[`ci-tooling-decisions.md`](../architecture/ci-tooling-decisions.md) records two decisions
this record revisits; D replaces both entries with the implemented state and a link to
this record.

* **`pre-commit` no-go (#255).** Its root reason was a second source of tool versions: each
  hook pinned through `rev:` runs its linter in an isolated venv, so local and CI versions
  drift (#153), and a partial move would need a three-way parity between the hook config,
  `CHECKS` and `ci.yml`. Neither applies to the plugin's hook. It is a single `quality` hook
  whose `rev:` pins only the plugin's wrapper; the wrapper runs the declared `test`,
  `python scripts/ci_check.py`, against the repository venv (B verifies this on this
  machine), so ruff and mypy still come from `requirements-dev.txt`. `ci_check.py` stays the
  only list of checks, and `pre-commit` is the trigger, not a second registry.
* **Spec Kit removal (#114).** It was removed because the in-repository `/plan` already
  covered specification → plan → tasks. This record retires that in-repository flow itself;
  OpenSpec arrives as part of the process this repository adopts, not as a second framework
  next to its own.

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

This record's PR is gated by `tests/test_adr_records.py`, `tests/test_doc_links.py` and
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

* Epic: [#592](https://github.com/ekolvah/kinozal_scraper/issues/592). This record:
  [#596](https://github.com/ekolvah/kinozal_scraper/issues/596). Steps A–D:
  [#597](https://github.com/ekolvah/kinozal_scraper/issues/597),
  [#598](https://github.com/ekolvah/kinozal_scraper/issues/598),
  [#599](https://github.com/ekolvah/kinozal_scraper/issues/599),
  [#600](https://github.com/ekolvah/kinozal_scraper/issues/600).
* Supersedes for this repository the consumer half of [ADR-0011](0011-agentic-process-distribution-mechanism.md) and #571;
  ADR-0011 stays `accepted` as the record of the export decision.
* Revisit this record if a plugin release removes a guarantee listed above as replaced, if
  upgrade PRs arrive more than weekly for a month, or if the plugin stops being maintained.
