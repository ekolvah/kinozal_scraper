# Agent development process

**Question this document answers:** How roles, artifacts, gates, and adapters
work together when planning and implementation use different agents.

This document is the canonical, agent-neutral development process. An agent is
an implementation detail; a role is a contract. The current default adapters
are Claude for `planner` and `reviewer`, and Codex for `implementer` and
`fixer`, but any adapter that satisfies the contract may be substituted.

## Roles and hand-offs

| Role | Required input | Required result | Next role |
| --- | --- | --- | --- |
| `planner` | Issue, repository context, and user decisions | Complete issue body, architect-review decision, passing issue validator | `implementer` |
| `implementer` | Passing issue body | Focused branch, RED evidence, implementation, docs, PR | `reviewer` |
| `reviewer` | Plan, diff, and checks | Visible, actionable findings or an explicit clean result | `fixer` or human |
| `fixer` | Review or CI finding | Minimal correction with passing relevant checks | `reviewer` or human |

The artifact, not an agent report, authorizes a hand-off. A planner must run
`python scripts/validate_issue_sections.py <N>` successfully; an implementer
must use `python scripts/check_red.py` for RED and `python scripts/ci_check.py`
before delivery. GitHub branch protection and required checks are the final
delivery gate.

## Issue contract

Substantive features and fixes start from a GitHub Issue. The required headings
are defined only by `REQUIRED_SECTIONS` in
`scripts/validate_issue_sections.py`:

1. Context / Why
2. Acceptance criteria
3. Test plan
4. Implementation outline
5. Docs to update
6. Out of scope
7. Architect review
8. ADR
9. Agent handoff

`Test plan` names executable test nodes. `Architect review` contains findings
or `skipped: <reason>`; `ADR` contains a record link or `none: <reason>`.
`Agent handoff` is concise provenance, with all four fields below:

```md
planner: <agent name> [<model/version if known>]
validation: `python scripts/validate_issue_sections.py <N>` — passed
next role: implementer
handoff: ready
```

Do not store prompts, transcripts, secrets, or private reasoning in the issue.
The recorded `validation: passed` is never an authorization by itself: every
implementer re-runs the validator before creating a branch.
Issues planned before this contract had eight sections. A planner adds
`Agent handoff` before implementation; an implementer that sees the missing
section stops and returns the issue to a planner rather than guessing it.

## Deterministic delivery flow

1. Before creating an issue, fetch `origin/main` and inspect recent closed
   issues and merged PRs for semantic duplicates. Ask the user for priority,
   then set it with `python scripts/set_issue_priority.py <N> <priority>`.
2. The planner researches the repository, writes the issue contract, obtains
   the architect review, fills `Agent handoff`, and validates the result.
3. The implementer validates the issue again, creates the branch only with
   `python scripts/issue_branch.py <N>`, writes and proves failing tests, then
   commits RED before production logic. Implement the agreed outline, update
   required documentation and ADRs, and run the local CI gate once in the
   foreground.
4. Create the PR only with `python scripts/open_pr.py`; it verifies the issue
   closing reference. Fix CI findings up to three improving iterations and
   enter the review/fix loop. After every push, run `gh pr checks <PR> --watch`,
   inspect a failed CI run with `gh run view <run-id> --log-failed`, and wait for
   the current-head
   reviewer outcome and all required checks. Fix every blocking and should-fix
   finding in a separate fixer commit, push it, then repeat. The implementer
   reports the PR ready for human merge only after the current head has a
   `clean` reviewer outcome, no actionable review threads, and every required
   check passes.

One PR is one logical unit. Do not bypass hooks, push to `main`, force-push,
reset hard, delete branches forcefully, self-merge, or replace these gates with
an agent assertion. Local agent hooks are defense in depth; GitHub branch
protection is authoritative.
A review check that is skipped, missing, malformed, or still pending is not a
clean review. It leaves the PR `not ready`. GitHub cannot resume a local agent
after its session ends; a session-driven adapter must stay active through this
loop, while a fully autonomous loop needs separately operated runner and
credential infrastructure.

## Review-controller bootstrap

The Claude provider refuses to review a PR that changes its own workflow file;
this is a deliberate supply-chain protection, never a `clean` result. The
trusted `agent-review-gate` therefore treats changes to the review-controller
surface as an exceptional human decision:

1. Keep the PR limited to `.github/workflows/claude-review.yml`,
   `.github/workflows/agent-review-gate.yml`, `scripts/check_claude_review.py`,
   their direct tests, and documentation; do not mix application changes into it.
2. A configured maintainer reviews the complete diff and posts exactly
   `<!-- review-controller-bootstrap: sha=<current-head-sha> -->` in the PR
   conversation. The SHA binds the exceptional decision to one head; any push
   requires a new marker.
3. The required gate runs from `main`, detects the protected paths through the
   GitHub PR-files API, and accepts that marker only from a configured
   maintainer. It otherwise remains red rather than treating the provider skip
   as approval.
4. The provider self-skip is visible, not a clean Claude review. On ordinary
   PRs the primary review's validated structured outcome is mapped directly to
   the `claude-review` job result; comments have no merge authority and there
   is no repair invocation. A quota, transport, or malformed-output failure is
   red until re-run. The trusted gate exists only for the controller exception.

No agent may treat the provider skip as an approval or post the maintainer
marker. The first PR that installs this trusted default-branch gate cannot use
code that is not yet on `main`; it needs a one-time human bootstrap with the
same narrow review and temporary protection change. After that installation,
ordinary PRs use Claude outcomes and later controller changes use the marker
path without changing branch protection.

## Governance conventions
1. Create issue branches only with `python scripts/issue_branch.py <N>`; it
   starts from fresh `origin/main`. Never create a branch directly.
2. Keep one PR to one logical unit. A temporary CI unblock for an unrelated
   failure may accompany the blocked change only when it has a tracked
   follow-up for the root cause.
3. Assign exactly one type label when creating an issue: `bug` for broken
   behaviour; then `perf` / `security` / `enhancement` for user-visible work;
   otherwise `refactor`, `testing`, `ci`, `documentation`, or `chore` by the
   changed area. Non-type labels are outside this taxonomy.
4. Ask the user for issue priority, then set the GitHub Project field with
   `python scripts/set_issue_priority.py <N> <High|Medium|Low>`. Propose High
   for user-facing bugs and development-process work, Medium for agentic
   capability work outside the process, and Low otherwise; name the rule used.
5. If a `requirements*.in` file changes, run `pip-compile` for its matching
   lockfile in the same commit.
6. Trivial non-behavioural one-line changes may skip the issue workflow only
   with the explicit rationale recorded in the issue or PR.

## Agent records and adapters

The PR template records the implementer, reviewer/fixer, concrete CI evidence,
selected route, model-invocation counts, fixer revisions, and conditional
skips/escalations in `## Agent record`. Invocation counts are a quota proxy,
not invented provider token totals. This makes agents comparable without
treating a particular provider as part of the workflow contract.

`.agents/orchestration/roles.yaml` is the single machine-readable catalogue of
the initial roles. `python scripts/agent_orchestrator.py <state.json>` is its
read-only, advisory control plane: it reports the next adapter/action, missing
evidence, and a bounded escalation path. It never invokes a model, changes the
repository, posts to GitHub, or replaces deterministic CI and branch
protection. The default route is planner → conditional architect reviewer →
implementer → deterministic CI → PR reviewer → conditional fixer → human
merge. The initial caps are one planning pass, one architect review, one
implementation pass, one PR review per head SHA, and three fixer revisions; an
exhausted cap escalates to a human rather than silently retrying. After ten
completed PRs, compare rework rate, actionable-review yield, cycle time, and
invocation counts before adding a specialist such as the separate code-critic
proposal.

The tracked [state example](../../.agents/orchestration/state.example.json)
starts at a planned nontrivial issue. Copy it for a local advisory decision;
it is not generated by CI and no command changes it.

| Field | Required | Accepted value |
| --- | --- | --- |
| `plan_completed` | yes | boolean |
| `issue_kind` | yes | `trivial` or `nontrivial` |
| `architect_completed` | no | boolean; default `false` |
| `architect_skip_reason` | no | string or `null`; required for a trivial issue |
| `implementation_completed` | no | boolean; default `false` |
| `ci_passed` | no | boolean; default `false` |
| `head_sha` | no | string or `null` |
| `reviewed_heads` | no | list of head-SHA strings; default `[]` |
| `review_outcome` | no | `clean`, `rework`, `blocking`, or `null` |
| `fixer_revisions` | no | non-negative integer; default `0` |
| `planner_runs` | no | non-negative integer; default `0` |
| `architect_runs` | no | non-negative integer; default `0` |
| `implementer_runs` | no | non-negative integer; default `0` |

| Role | Max runs | Scope |
| --- | --- | --- |
| `planner` | 1 | per issue |
| `architect_reviewer` | 1 | per issue |
| `implementer` | 1 | per issue |
| `pr_reviewer` | 1 | per head SHA, enforced by `reviewed_heads` |
| `fixer` | 3 | per PR review/fix loop |
| `human_merge` | 1 | terminal hand-off; descriptive, not a retry counter |

An adapter supplies a role's user interface and platform-specific permissions:

- The Claude adapter exposes `/plan #N` and invokes the local architect
  reviewer. It plans and hands off; it does not implement.
- The Codex adapter exposes `$implement-issue #N` through the repository skill
  in `.agents/skills/implement-issue/`. It implements and fixes; it does not
  invent a replacement plan.

To add another agent, add an adapter that points to this document, records its
role in the hand-off or PR record, and passes the same issue, RED, CI, PR-link,
and branch-protection checks. Do not fork the workflow or issue schema.
The Claude and Codex post-edit adapters both call `scripts/hooks.py` for the
same ruff feedback and dependency-lock reminder. Codex uses the documented
`apply_patch` hook payload (`tool_input.command`) and invokes
`python -m scripts.codex_hooks` from the repository root so imports resolve.
