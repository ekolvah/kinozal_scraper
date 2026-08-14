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

Substantive features and fixes start from a GitHub Issue. The nine base headings
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

Which sections a given issue must carry is **per-change-class data, not a rule an
agent remembers**: `.agents/orchestration/change-classes.yaml` holds one row per
type label, declaring what that class `adds` to the nine base sections and what
it `omits` from them. `scripts/validate_issue_sections.py` resolves the row from
the issue's single type label and validates against the result, so a new class is
a data edit rather than another branch inside the validator.

| Type label | Adds | Omits |
| --- | --- | --- |
| `bug` | `Evidence` | — |
| `chore` | `Prior art` | — |
| `ci` | `Prior art` | — |
| `documentation` | `Prior art` | — |
| `enhancement` | `Prior art` | — |
| `perf` | `Prior art` | — |
| `refactor` | `Prior art` | — |
| `security` | `Prior art` | — |
| `testing` | `Prior art` | — |

Every class adds exactly one **discovery** section, and which one is the only
thing the split decides: `bug` records a completed observation of the external
system, everything else records the search outside this repository before the
code is designed. The line runs at `bug` rather than at `enhancement` because
the case that motivated the rule is `refactor` work: a flat `src/` layout was
chosen and then had to be reversed once it turned out to cut the project off
from `import-linter`/`grimp` (#204, #237).

`documentation` omits nothing on purpose: here the documentation *is* the
product, guarded by its own tests, so such an issue routinely carries a real test
node and can hold a real architecture decision; a trivial one still uses the
`skipped:` escape inside `Architect review`. Two obligations are **derived** from
the resolved set rather than stored beside it, so no two fields can contradict
each other: an architect review is required when `Architect review` is part of
it, and RED is required when `Test plan` is. On a passing issue the validator
prints both, e.g. `class: documentation — RED required`.

Exactly one type label (governance convention 3) is therefore a gate, not a
convention: zero or several is a `type label` gap. The **maintainer** fixes it
with `gh issue edit <N> --add-label <type>`, because a planner may not change
labels (§Planner runbook).

The `## Evidence` section that `bug` adds records a completed observation of the
external system. When the plan describes how to read, parse, or classify external
data, use this shape:

```md
capture: `<source-specific reproducible command that writes the path below>`
path: `<repository-relative path>`
observed: <the source fact that explains the reported failure>
preserve: <the exact valid record from the same captured response that must keep working>
change: <the exact invalid record from that captured response whose behaviour must change>
boundaries: <candidate fix boundaries compared, from broad to narrow>
collateral: <whether each candidate preserves or loses that exact valid record>
reuse: <current production path traced to the existing input/fetch usable by the narrow boundary>
paired-test: <the same captured input through one pipeline run keeps the valid record and rejects the invalid record>
```

The command is specific to the external source and must include the exact path
named on the next line. That path is under `evidence/issue-<N>/`; the captured
file is working-tree-only planning evidence, ignored by Git and kept locally
only until merge. The reviewer receives a verified, safe, compressed observation
record in the public issue, not the full payload. The validator checks the
command, safe relative path, record fields, and explicit failed-capture output;
it deliberately does not require the local file, which will be absent in a fresh
clone or worktree. It does not try to recognize every possible source tool or
prove that the recorded conclusions are true.

The remaining fields turn the capture into a reviewable design decision:
compare at least the reported invalid record with an exact valid record from the
same captured response. A sibling feed, query, category, or alternative source
does not count as preservation of that record. A candidate that loses the
preserved record is BLOCKING unless the issue records an explicit product
decision authorizing that loss. Choose the narrowest boundary supported by the
observation, trace the current production path before claiming that it needs
another fetch, and expose collateral loss instead of silently accepting it. Use
the narrowest read-only route below; never run a full pipeline that writes
Sheets rows or sends Telegram notifications merely to collect evidence:

| Source | Capture route |
| --- | --- |
| Kinozal | `python scripts/capture_kinozal_fixture.py <url> <path>` |
| GitHub REST | `python scripts/capture_external_fixture.py github <endpoint> <path> --confirm-repository-safe` |
| Telegram channel input | `python scripts/capture_external_fixture.py telegram <channel-url> <path> --confirm-repository-safe` |
| Gemini summarization | `python scripts/capture_external_fixture.py gemini <saved-input> <path> <--broadcast|--chat> --confirm-repository-safe` |
| Existing Sheets worksheet | `python scripts/capture_external_fixture.py sheets <spreadsheet-url> <worksheet> <path> --confirm-repository-safe` |
| Another source with a read-only CLI | `<read-only command> | python scripts/capture_external_fixture.py stdin <path> --confirm-repository-safe` |

The safety flag is an explicit claim, not a sanitizer: inspect the payload and
never commit credentials, private messages, or other sensitive data. The
Telegram route calls `TelethonReader` without Gemini or a notifier; the Gemini
route replays an already saved input without Telegram delivery; the Sheets
route only reads an existing worksheet; and the GitHub route permits one
`gh api` GET rather than arbitrary subprocess arguments. The `stdin` route
persists output but does not execute the upstream tool, so it adds no generic
process-execution capability.

If no safe read-only route exists, do not improvise with a side-effecting
production entry point. A failed capture records `status: failed` plus a
non-empty fenced block after `output:` containing the attempted command's
output; an unsupported claim that the source is unavailable is still a gap.
This makes the access failure reviewable but does not prove source behaviour: a
plan whose design depends on the missing fact remains blocked, the validator
stays red with `missing: successful capture`, and no implementer handoff may be
recorded. The command and compressed record make the observation reviewable
without treating planning history as repository state.

Captured bytes belong in `tests/fixtures/` only when a production-behaviour
regression test reads the captured bytes in the same commit. By contrast, full
issue bodies, transcripts, and planning history are not test fixtures; removing
the local `evidence/` copy after merge does not remove the issue's durable
decision record.

For a bug with no external-system behaviour to observe, the section instead
starts with `n/a: <reason>`, naming why live capture does not apply. The section
is still required, so choosing that branch is a visible claim rather than a
silently omitted discovery step.

The `## Prior art` section that every other class adds records the search
**outside** this repository — the maintained library, standard tool, or upstream
feature that may already solve the problem — in three lines:

```md
searched: <where you looked: the queries, and the repository paths you compared them against>
candidates: <what exists, each one named and linked>
verdict: reuse|build — <why, in one sentence>
```

`verdict:` is red unless it starts with `reuse` or `build`: the section exists to
record one of two decisions, so `TBD` or a restatement of the search decides
nothing. Prose after that word is free and matching is case-insensitive. The gate
never judges whether the verdict is *right*, exactly as `Architect review` and
`ADR` do not.

This section, too, accepts `n/a: <reason>` as its whole content, for a class of
change with no ecosystem to search — a broken-link fix, a guard test over
existing code. The branch exists rather than forcing `searched:` everywhere
because `searched:` names no verifiable artifact the way a capture path does: a
mandatory field would be satisfiable by fabrication, and a fabricated record is
indistinguishable from an honest one (§IV) while charging a web round trip
against goal 2. Abusing the branch is a nameable architect-review finding, not a
silent pass.

`Test plan` names executable test nodes. `Architect review` opens with a
provenance line — `reviewer: <carrier>` or `skipped: <reason>` — followed by the
findings; `ADR` contains a record link or `none: <reason>`.
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
On a passing issue, the validator also prints a non-blocking reminder when a
top-level `Out of scope` bullet explicitly promises a follow-up but names
neither `#N` nor a `wontfix`/`YAGNI` decision. The planner or maintainer still
decides what the prose means; the reminder never changes the passing exit code
and does not create an issue automatically (#368).
Issues planned before this contract had eight sections. A planner adds
`Agent handoff` before implementation; an implementer that sees the missing
section stops and returns the issue to a planner rather than guessing it. An
issue planned before its class carried a discovery section reds the same way at
the implementer's pre-branch re-run, and the same rule applies with less room to
improvise: a discovery section records work someone actually carried out, so an
implementer that finds `## Prior art` missing stops and
returns the issue to a planner rather than inventing the search.

## Planner runbook

These steps belong to the `planner` role, not to an adapter. Every planner entry
point runs them; an adapter adds only its own interface — how the issue number
arrives, how a reviewer is invoked, how the body is written back.

1. Run `python scripts/validate_issue_sections.py <N>`. A passing issue is
   already planned: report that and stop.
2. Use all four sources of answers. Read and search the repository first. Ask
   at most three clarifying questions per session, and only about decisions such
   as priority or product intent. When the plan describes how to read, parse, or
   classify data from an external system, observe that live system before
   writing the plan. From that observation, record one invalid record and one
   exact valid record from the same response, compare candidate fix boundaries,
   and state whether each boundary loses that preserved record. Replacing it
   with a sibling feed or category is data loss, not preservation. Inspect the
   current call path before deciding whether a narrower classification needs a
   new fetch. Record both the capture and that decision in `## Evidence`, and
   name one paired test that sends the same captured input through one pipeline
   run and proves that the valid record remains while the invalid one changes.
   Otherwise record `n/a: <reason>` there; this is discovery, not an E2E test or
   a substitute for a human product decision. For every other class, the fourth
   source is the world outside this repository: search for the maintained
   library, standard tool, or upstream feature that already solves the problem
   **before** designing the code, and record the search, the candidates, and the
   reuse/build verdict in `## Prior art`.
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

### Who reviewed, recorded

Not every route has a second carrier for this role. Where one exists, it reviews
a plan it did not write; where it does not, the planning agent reviews its own
plan. **Self-review is a legitimate way to satisfy this role and a weaker one**:
one agent works from shared session context, so it cannot contradict an
assumption it already made — it is not an independent check, and it does not
replace the PR review of the diff. What it must never do is pass for
independent, because a filled section otherwise looks identical whether an
independent carrier reviewed the plan, its author did, or nobody did (#474).

The section therefore opens with a machine-readable provenance line naming the
carrier, which
[`validate_issue_sections.py`](../../scripts/validate_issue_sections.py)
resolves against `architect_reviewer.adapter_independence` in the role
catalogue:

```md
reviewer: <a carrier declared in .agents/orchestration/roles.yaml>
```

Only the **first non-empty line** of the section counts, because findings prose
routinely quotes the marker while discussing it. The kind — independent or
self — is read from the catalogue rather than written by the author, so a
marker cannot claim independence for a self-review carrier. An unknown carrier
name is a gap; a self-review passes and prints a non-blocking note naming what
it is. The gate does not judge how substantial the findings are: it guarantees
that the carrier was named, exactly as `Agent handoff` guarantees that the
planner was.

A section written before this rule carries no marker, and an implementer that
sees one returns the issue to a planner instead of guessing who reviewed it —
the same rule as for a missing `Agent handoff`.

The reviewer reads the [goal function](principles.md#goal-function) and the
principles themselves rather than working from memory, then checks the plan
against §I–§VII and for:

- **Scope creep** — documentation, refactor, and feature mixed into one PR.
- **Work for work** — a script, agent, or abstraction created
  for a need that does not exist yet, or duplicating what `ci_check`, the PR
  review, or an existing test already does.
- **Reinvented prior art** — the plan builds what the ecosystem already ships.
  The `## Prior art` verdict is the plan's own claim about this, so trace it
  rather than accept it. The line is not "no bespoke scripts": workflow glue
  that encodes *this* repository's contract — `check_red`,
  `validate_issue_sections` — has no upstream equivalent and is legitimate.
  What this finding names is
  reimplementing what a maintained tool already provides, such as a
  hand-rolled header checker where `ruff` has the rule.
- **A workaround with no named root cause** (§V).
- **An over-broad external-data boundary** — the plan does not compare a valid
  and invalid record from the same captured response, substitutes a sibling
  source for preservation, accepts loss of the preserved record without an
  explicit product decision, or claims a new fetch without tracing the current
  production path. Its paired test must exercise both records from the same
  input and run, not separate allowed/rejected sources.
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
3. The implementer validates the issue again, verifies its Project Priority
   with `python scripts/set_issue_priority.py <N> --check`, then creates the
   branch only with `python scripts/issue_branch.py <N>`. It writes and proves
   failing tests, then commits RED before production logic. Implement the
   agreed outline, update required documentation and ADRs, and run the local CI
   gate once in the foreground.
4. Create the PR only with `python scripts/open_pr.py`; it verifies the issue
   closing reference. Replace an existing delivery PR report only with
   `python -m scripts.update_pr_body <PR> --body-file <path>`: it re-applies the
   branch-derived closing line and verifies the resulting linkage. A normal
   `open_pr.py` re-run remains idempotent and never replaces an existing body.
   Fix CI findings up to three improving iterations and enter the review/fix
   loop. After every push, run `gh pr checks <PR> --watch`,
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
on the current head and how many distinct heads `agent-review` has already
reviewed. It changes nothing and posts nothing.

| Verdict | Exit code | Meaning |
| --- | --- | --- |
| `ready-for-human` | `0` | Loop over. Report the PR ready; remaining findings are the maintainer's call. |
| `fix-blocking` | `10` | One minimal fixer commit, push, run the gate again. |
| `escalate` | `20` | Loop over with a named anomaly: the fixer budget is spent. |
| `review-pending` | `30` | Evidence is not final. Wait once with `gh pr checks <PR> --watch`, then re-run the gate; a second `review-pending` goes to the maintainer, never a polling loop. |

Exit `2` is not a verdict: it is a `gh`, argument, or capture failure, and it
leaves the PR `not ready` the same way a missing review does. A red
`agent-review` cannot distinguish blocking findings from an unavailable review
— the check-run conclusion collapses both — so the gate names the ambiguity and
the agent reads the run before changing anything. The fixer budget comes from
`fixer.max_runs` in the role catalogue; the count is a proxy — distinct heads
reviewed, minus the first — so a review re-run on an unchanged head spends none
of it. The verdict goes into `## Agent record`, which is how a skipped gate
becomes visible at merge time.

## Review outcome enforcement

A structured outcome is enforced the same way on every PR. `clean` and `rework` pass,
`blocking` reds the check, and an empty or malformed outcome is an unavailable
review, which is red too. There is no path-based exception (#483): a PR
changing the review controller used to pass an empty outcome with a warning,
because the action refused to review it at all, and that carve-out is gone
together with its cause. The trust model behind the fix is recorded in
[ADR-0004](../adr/0004-controller-pr-review-runs-on-the-workflow-token.md).

The review still executes code from the PR head, so a controller PR verifies
itself; the agent review reports, it does not authorise the merge. Keep such a
PR limited to `.github/workflows/agent-review.yml`,
`scripts/check_branch_protection.py`, `scripts/check_agent_review_outcome.py`,
`scripts/request_codex_review.py`, their direct tests, and documentation; do not
mix application changes into it. That keeps the diff a maintainer reads before
merging small enough to read.

A controller PR can therefore break its own gate. That failure is fail-closed
and recoverable without any special right: the enforcement scripts are always
checked out from the default branch, so a broken head can red the check but
cannot turn an absent review green, and `main` keeps running its own copy of the
workflow. Recovery is a push to the same branch — fix the controller change,
or revert it inside the PR and re-run the check. Never merge a controller PR
whose `agent-review` is red, and never disable the required context to get
past it.

On every PR the workflow first checks out the default-branch verifier source,
then reads the current PR body and head SHA from the GitHub API. It maps the
primary review's validated structured outcome directly to the `agent-review`
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
   changed area. Non-type labels are outside this taxonomy. The label is the
   machine-read route key into the change-class matrix above and the maintainer
   owns it: `validate_issue_sections.py` fails on zero or several type labels.
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
  the architect review itself and records that in the section's provenance line,
  as §Who reviewed, recorded requires.
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

Every role declares how its carrier is chosen in `carrier_selection:`, because
having alternatives and choosing between them per run are two different facts:

| Mode | Who chooses | `adapter_routes` |
| --- | --- | --- |
| `run_route` | the human, by which chat the request is sent in | route-to-adapter map |
| `ci_failover` | the workflow at run time, from whether the previous carrier answered | `null` |
| `sole` | nobody; the role has one carrier | `null` |

`route` in the state file selects the adapter for one run rather than for the
repository, because a constant naming the wrong agent is confident
misinformation from a tool whose whole purpose is inspectability (#475). A role
the route does not choose for — the `human_merge` human, or the `pr_reviewer`
review gate, which asks its second carrier only when the first returns no
verdict (#478) — answers every known route with `adapter:`, the carrier asked
first. Deriving the mode from the number of adapters instead would make the
review gate claim a `codex` run was reviewed by Codex. A route that no role
declares is a visible error naming the known routes, and a known route that a
`run_route` role does not offer is a visible error naming that role and its
routes. Neither falls back quietly.

To add another agent, add an adapter that points to this document, records its
role in the hand-off or PR record, and passes the same issue, RED, CI, PR-link,
and branch-protection checks. Do not fork the workflow or issue schema.
The Claude and Codex post-edit adapters both call `scripts/hooks.py` for the
same ruff feedback and dependency-lock reminder. Codex uses the documented
`apply_patch` hook payload (`tool_input.command`) and invokes
`python -m scripts.codex_hooks` from the repository root so imports resolve.
