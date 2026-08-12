# Repository agent guidance

Use [the agent development process](docs/architecture/agent-process.md) as the
source of truth. Roles are interchangeable: do not assume that the current
Claude or Codex adapter is the only permitted executor.

## Codex adapter

- Plan an issue with `$plan-issue #N`; it runs the same planner runbook as any
  other planner adapter and performs the architect review itself.
- Implement a planned issue with `$implement-issue #N`.
- Run `python scripts/validate_issue_sections.py N` before creating a branch.
  If it fails, stop and hand the issue back to a planner.
- Run `python scripts/set_issue_priority.py N --check` before creating a branch.
  If it fails, stop and have the maintainer set the issue Priority.
- Use `python scripts/issue_branch.py N`; never create an issue branch directly.
- Follow RED -> GREEN: write the issue's named tests, prove them with
  `python scripts/check_red.py <test paths>`, commit RED, then implement.
- Before a PR, run `python scripts/ci_check.py` once in the foreground. Use
  `python scripts/push_issue_branch.py` to publish the current issue branch,
  write the report to `.codex/pr-body.md`, then use
  `python scripts/publish_pr_report.py` to create or update the PR. Leave
  merging to a human.
- After every PR push, stay in the review/fix loop defined in
  `agent-process.md`: wait for checks, then ask
  `python -m scripts.review_gate <PR>` whether the loop continues. Its exit code
  decides — `0` ends the loop, `10` means one more fixer commit, anything else
  leaves the PR `not ready` for the maintainer. Do not substitute your own
  reading of the findings for the verdict; `should-fix` findings are the
  maintainer's call, not a gate (#458).
- The advisory control plane (`scripts/agent_orchestrator.py` plus
  `.agents/orchestration/roles.yaml`) reports evidence-based routing and budget
  escalation. It never authorizes bypassing its required delivery gates.

## Repository conventions

- Windows: use `python`, not `python3`; use PowerShell syntax in PowerShell.
- Capture Python subprocess output with `encoding="utf-8"`; do not turn a
  `None` stdout or stderr into an empty string.
- Keep a PR to one logical unit. Update planned docs and ADRs, or explicitly
  record why they do not apply.
- Never bypass hooks, push directly to `main`, force-push, hard-reset,
  force-delete a branch, or self-merge. The repository hook is supplementary;
  GitHub branch protection remains the final barrier.

## Code Review Rules

Codex code review is carrier 2 of the required review gate (#478): it is asked for
a review when carrier 1 left no verdict, and `scripts/request_codex_review.py` reads
the review it posts on the pull request. The rules below are that review contract —
the same one carrier 1 gets as its prompt in
`.github/workflows/agent-review.yml`, guarded against drift by
`tests/test_agent_review_workflow.py`.

- Read `CLAUDE.md` and the repository docs it links to first: repository
  conventions take precedence over your defaults.
- Look at bugs and logic errors, security issues, adherence to the `CLAUDE.md`
  conventions (PR workflow, branch naming, dependency rules, no workarounds without
  a root cause), and whether the change has matching test coverage — or a
  consciously-rejected coverage decision recorded in the accepted-gaps ledger
  `docs/architecture/coverage-gaps.md`.
- For changed documentation, follow `docs/architecture/project-map.md`: it
  describes the current implemented state, not history or ideas. Issue and PR
  references are pointers; removing one must not change the statement's meaning.
- Coverage first: report every issue you find. Grade findings, never drop them — a
  finding you decided was too small to mention is indistinguishable from a review
  that never ran. Each finding carries `severity` (blocking / should-fix /
  nice-to-have) and `confidence` (high / medium / low); the human filters, not you.
- `blocking` is a concrete bar, not a feeling: wrong behaviour, a failing test, a
  missing test for changed behaviour, a result that misleads the reader, a leaked
  secret, or a violation of the `CLAUDE.md` conventions above.
- `should-fix` has an equally concrete bar: the finding changes behaviour, contract,
  or what the operator reads. Comment wording, a doc example, a constant's name,
  ordering and style are `nice-to-have` — still reported, never graded should-fix.
- Do not re-raise a finding that the diff already answers with a recorded rationale
  — a code comment, a `coverage-gaps.md` entry, an ADR, or an explicit point in the
  issue body. If the rationale is wrong, that is a `blocking` finding naming what it
  gets wrong. On a re-run, review the increment since the previously reviewed SHA:
  do not re-list consciously-kept tradeoffs "for coverage".
- A finding the deterministic gate already catches (ruff / mypy in
  `scripts/ci_check.py`) is graded `nice-to-have, duplicate of ci_check`: redundancy
  with another executor is a reason to rank it last, not to withhold it.
- In the review body, list findings grouped by severity — including the ones that
  got no inline comment — and state `Reviewed head SHA:` followed by the head SHA
  you reviewed. Found nothing at any severity? Say exactly that, in one line.
- The review state *is* the verdict the gate reads, so set it literally: request
  changes when you have a blocking finding; comment without requesting changes when
  your findings are should-fix or lower; approve only when you found nothing. Never
  merge — that stays with the human reviewer.
