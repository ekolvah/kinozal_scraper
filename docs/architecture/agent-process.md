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
   process one review pass. A human merges the PR.

One PR is one logical unit. Do not bypass hooks, push to `main`, force-push,
reset hard, delete branches forcefully, self-merge, or replace these gates with
an agent assertion. Local agent hooks are defense in depth; GitHub branch
protection is authoritative.

## Agent records and adapters

The PR template records the implementer, reviewer/fixer, and concrete CI
evidence in `## Agent record`. This makes agents comparable without treating a
particular provider as part of the workflow contract.

An adapter supplies a role's user interface and platform-specific permissions:

- The Claude adapter exposes `/plan #N` and invokes the local architect
  reviewer. It plans and hands off; it does not implement.
- The Codex adapter exposes `$implement-issue #N` through the repository skill
  in `.agents/skills/implement-issue/`. It implements and fixes; it does not
  invent a replacement plan.

To add another agent, add an adapter that points to this document, records its
role in the hand-off or PR record, and passes the same issue, RED, CI, PR-link,
and branch-protection checks. Do not fork the workflow or issue schema.
