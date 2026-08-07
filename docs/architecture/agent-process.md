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

## Planner runbook

These steps belong to the `planner` role, not to an adapter. Every planner entry
point runs them; an adapter adds only its own interface — how the issue number
arrives, how a reviewer is invoked, how the body is written back.

1. Run `python scripts/validate_issue_sections.py <N>`. A passing issue is
   already planned: report that and stop.
2. Close the reported gaps from the repository first — read and search the code
   and documents before asking anyone. Ask at most three clarifying questions
   per session, and only about decisions the repository cannot answer, such as
   priority or product intent.
3. Obtain the architect review defined below and record it in
   `## Architect review`. Weave every BLOCKING finding into the other sections
   before writing the body.
4. Fill `## Agent handoff`, then write the complete body back to the issue.
   Never discard existing text: restructure and extend it.
5. Re-run the validator and iterate on what it reports. Stop after
   three planning iterations; an issue still failing then goes back to the user
   with the reason, rather than to an implementer.

`## Test plan` names executable test nodes, because they are the contract of the
RED step. `## Docs to update` lists documents or states explicitly that
behaviour does not change. `## ADR` follows the cost-of-change filter in
[`project-map.md`](project-map.md) §Canonical-home; the bar is deliberately
narrow, so `none: <reason>` is a routine answer rather than an emergency one,
and a record that is created also joins `## Docs to update`. A planner does not
write implementation code, create the issue branch, or change labels — those
belong to the implementer and to the issue templates.

## Architect review contract

An architect review reads a plan or issue body **before** execution; a finished
diff belongs to the PR reviewer. It is read-only: it returns findings, and the
planner applies them.

It is required for every substantive change. A trivial one — a typo, a one-line
non-behavioural edit — records `skipped: <reason>` in the issue section instead.
One pass, never a loop.

The reviewer reads the [goal function](principles.md#goal-function) and the
principles themselves rather than working from memory, then checks the plan
against §I–§VII and for:

- **Scope creep** — documentation, refactor, and feature mixed into one PR.
- **Work for work** — a script, agent, or abstraction created
  for a need that does not exist yet, or duplicating what `ci_check`, the PR
  review, or an existing test already does.
- **A workaround with no named root cause** (§V).
- **Avoidable tokens** — an expensive pass where a deterministic script would
  do, or a model call a cheap pre-filter would answer.
- **A test-first loophole** — a behavioural change declared an exception while a
  deterministic part of it deserves RED → GREEN.
- **A decision with no home** — high cost of change, several modules or
  documents affected, and its rationale recorded nowhere.

### Findings format

Grade findings; do not filter them. A finding left unwritten is
indistinguishable from a review that never ran (§IV), and the marginal one is
always the cheapest to drop, so the rule is to shorten each finding rather than
to report fewer of them. Filtering is the planner's job.

Each finding is concrete and actionable, and carries a confidence — high,
medium, or low — wherever the reviewer is unsure of the finding itself:

- **BLOCKING** — a named §I–§VII violation, a design defect that would have to
  be redone after execution, a symptom fix over an unnamed cause, or an
  unverified assumption about an external API that the plan rests on.
- **SHOULD-FIX** — a marked improvement to future support cost or token spend.
- **NICE-TO-HAVE** — everything below those two bars. It moves down, it does not
  disappear.
- **OK** — what the plan already gets right.

Bloat and self-justification in the plan are the review's *subject*, never an
instruction to shorten its own output.

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
   inspect a failed CI run with `gh run view <run-id> --log-failed`, then ask
   `python -m scripts.review_gate <PR>` whether the loop continues. Its verdict
   decides, not the agent's reading of the findings; the exit code is the
   carrier. `should-fix` findings are published in the PR and are the
   maintainer's decision — they do not gate the loop. Chasing them is how one
   delivery PR reached ten review rounds with the last four purely cosmetic
   (#458), and how another spent two of its four rounds fixing defects its own
   previous fix had introduced (#465). Stated as a condition, the rule was
   skipped both times, so it became an exit code (#467). A PR is ready once the
   current head has
   no blocking finding and every required check passes — that is what
   `ready-for-human` reports, and a `rework` outcome with its warning is a ready
   PR, not an unfinished one.

One PR is one logical unit. Do not bypass hooks, push to `main`, force-push,
reset hard, delete branches forcefully, self-merge, or replace these gates with
an agent assertion. Local agent hooks are defense in depth; GitHub branch
protection is authoritative.
A review check that is skipped, missing, malformed, or still pending is not a
clean review. It leaves the PR `not ready`. GitHub cannot resume a local agent
after its session ends; a session-driven adapter must stay active through this
loop, while a fully autonomous loop needs separately operated runner and
credential infrastructure.

### Review-gate verdicts

`python -m scripts.review_gate <PR>` reads the live PR — the required contexts
on the current head, the review-controller classification, and how many distinct
heads `claude-review` has already reviewed. It changes nothing and posts
nothing.

| Verdict | Exit code | Meaning |
| --- | --- | --- |
| `ready-for-human` | `0` | Loop over. Report the PR ready; remaining findings are the maintainer's call. |
| `fix-blocking` | `10` | One minimal fixer commit, push, run the gate again. |
| `escalate` | `20` | Loop over with a named anomaly: fixer budget spent, or a controller PR whose green check does not prove a review ran. |
| `review-pending` | `30` | Evidence is not final. Wait once with `gh pr checks <PR> --watch`, then re-run the gate; a second `review-pending` goes to the maintainer, never a polling loop. |

Exit `2` is not a verdict: it is a `gh`, argument, or capture failure, and it
leaves the PR `not ready` the same way a missing review does. A red
`claude-review` cannot distinguish blocking findings from an unavailable review
— the check-run conclusion collapses both — so the gate names the ambiguity and
the agent reads the run before changing anything. The fixer budget comes from
`fixer.max_runs` in the role catalogue; the count is a proxy — distinct heads
reviewed, minus the first — so a review re-run on an unchanged head spends none
of it. The verdict goes into `## Agent record`, which is how a skipped gate
becomes visible at merge time.

## Review-controller manual review

When a review-controller PR has an empty outcome output, `claude-review` emits a
visible warning instead of a successful Claude review. When a structured outcome
exists, it is enforced exactly as on an ordinary PR: `clean` and `rework` pass,
`blocking` reds the check. For this single-maintainer repository, the no-outcome exception
is an accepted operating policy: before merge, the maintainer completes a
manual IDE-agent review of the complete controller diff.

This review is a human merge responsibility, not machine-verifiable evidence.
There is no bootstrap marker and no separate trusted review gate. Keep a
controller PR limited to `.github/workflows/claude-review.yml`,
`scripts/check_branch_protection.py`, `scripts/check_claude_review_outcome.py`,
their direct tests, and documentation; do not mix application changes into it.
Any push requires the maintainer to
review the new complete diff in the IDE before merge.

On every PR the workflow first checks out the default-branch verifier source,
then reads the current PR body and head SHA from the GitHub API. It maps the
primary review's validated structured outcome directly to the `claude-review`
job result. This prevents a manual re-run from
using stale event metadata; the body is untrusted data, never shell input, and
the summary identifies the reviewed SHA. Comments have no merge authority and
there is no repair invocation. An unavailable live context, quota, transport,
or malformed output is red until re-run.

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
skips/escalations in `## Agent record`. Invocation counts are a completed
run-count proxy at the time this record is written, not invented provider token
totals; they exclude a review triggered by a later push. This makes agents
comparable without treating a particular provider as part of the workflow
contract.

`.agents/orchestration/roles.yaml` is the single machine-readable catalogue of
the initial roles. `python scripts/agent_orchestrator.py <state.json>` is its
read-only, advisory control plane: it reports the next adapter/action, missing
evidence, and a bounded escalation path. It never invokes a model, changes the
repository, posts to GitHub, or replaces deterministic CI and branch
protection. The default route is planner → conditional architect reviewer →
implementer → deterministic CI → PR reviewer → conditional fixer → human
merge. The cap table below is the canonical documented copy of the catalogue
limits; an exhausted cap escalates to a human rather than silently retrying.
After ten completed PRs, compare rework rate, actionable-review yield, cycle
time, and invocation counts before adding a specialist such as the separate
code-critic proposal.

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
| `route` | no | a route name declared in `adapter_routes:`, or `null` for the catalogue default |

### Control-plane output contract

The CLI prints one JSON object. `next_role` names the route step; use `adapter`
and `next_action` to perform it rather than assuming every step is a catalogue
role. In particular, `deterministic_ci` is a deliberate non-catalogue step.

| Field | Meaning |
| --- | --- |
| `next_role` | The selected catalogue role, or the explicit non-catalogue `deterministic_ci` step. |
| `status` | `next` when an adapter may act, `blocked` when listed evidence is missing, or `escalate` when a bounded budget needs a human decision. |
| `missing_evidence` | The exact evidence fields preventing the route from proceeding; empty for `next` and `escalate`. |
| `completed_roles` | Snapshot of roles whose current completion evidence is satisfied; it is not an invocation history. When the selected route is `blocked`, that selected role is omitted even if it had prior completion evidence, because more evidence is now required. |
| `adapter` | The existing human-launched adapter or deterministic command for `next_role`, resolved for the requested `route`; the catalogue default when no route is requested. |
| `route` | The route this decision was resolved for, echoing the input; `null` says the catalogue default answered, so a forgotten route field cannot pass for a choice. |
| `contract` | Pointer to the canonical section this role owes, so the output names the role contract and not only a provider command; empty for the non-catalogue `deterministic_ci` step. |
| `next_action` | The concrete action to take, including a human-decision action for blocked or escalated routes. |

| Role | Max runs | Scope |
| --- | --- | --- |
| `planner` | 1 | per issue |
| `architect_reviewer` | 1 | per issue |
| `implementer` | 1 | per issue |
| `pr_reviewer` | 1 | per head SHA, enforced by `reviewed_heads` |
| `fixer` | 3 | per PR review/fix loop |
| `human_merge` | 1 | terminal hand-off; descriptive, not a retry counter |

An adapter supplies a role's user interface and platform-specific permissions;
the role contract itself is a section of this document. The catalogue records
the known entry points in `adapters:`, which route reaches each of them in
`adapter_routes:`, and the fallback in `adapter:` for a run that names no route,
so naming a provider is a default, not a restriction:

- Claude `/plan #N` runs the planner runbook and invokes the local
  `architect-reviewer` subagent.
- Codex `$plan-issue #N` runs the same runbook through the repository skill in
  `.agents/skills/plan-issue/`. Having no local reviewer subagent, it performs
  the architect review itself against the contract above.
- Codex `$implement-issue #N` runs the delivery flow through the skill in
  `.agents/skills/implement-issue/`. It implements and fixes; it does not
  invent a replacement plan.
- Claude `/implement #N` runs the same delivery flow through
  `.claude/commands/implement.md`, so one agent can carry an issue from plan to
  PR. It is a declared entry point, not the fallback: `adapter:` still names
  Codex for `implementer` and `fixer`, and a run reaches this one by requesting
  the `claude` route.

Every entry point above is declared in `adapter_files:` alongside `adapters:`,
mapping each adapter to its carrier file or to an explicit `null` where the
carrier is a GitHub Action or a person. That is what makes a role covered by one
provider and not another a visible fact rather than a silent one (#473).

`route` in the state file selects the adapter for one run rather than for the
repository, because a constant naming the wrong agent is confident
misinformation from a tool whose whole purpose is inspectability (#475). A role
with a single carrier — the `pr_reviewer` GitHub Action, the `human_merge`
human — declares `adapter_routes: null` and answers every known route with that
carrier; it is not a provider's variant of anything. A route that no role
declares is a visible error naming the known routes, and a known route that a
role does not offer is a visible error naming that role and its routes. Neither
falls back quietly.

To add another agent, add an adapter that points to this document, records its
role in the hand-off or PR record, and passes the same issue, RED, CI, PR-link,
and branch-protection checks. Do not fork the workflow or issue schema.
The Claude and Codex post-edit adapters both call `scripts/hooks.py` for the
same ruff feedback and dependency-lock reminder. Codex uses the documented
`apply_patch` hook payload (`tool_input.command`) and invokes
`python -m scripts.codex_hooks` from the repository root so imports resolve.
