# Repository agent guidance

Use [the agent development process](docs/architecture/agent-process.md) as the
source of truth. Roles are interchangeable: do not assume that the current
Claude or Codex adapter is the only permitted executor.

## Codex adapter

- Implement a planned issue with `$implement-issue #N`.
- Run `python scripts/validate_issue_sections.py N` before creating a branch.
  If it fails, stop and hand the issue back to a planner.
- Use `python scripts/issue_branch.py N`; never create an issue branch directly.
- Follow RED -> GREEN: write the issue's named tests, prove them with
  `python scripts/check_red.py <test paths>`, commit RED, then implement.
- Before a PR, run `python scripts/ci_check.py` once in the foreground. Use
  `python scripts/open_pr.py` to create the PR and leave merging to a human.
- After every PR push, stay in the review/fix loop defined in
  `agent-process.md`: wait for checks, inspect failures and review feedback,
  commit fixes separately, and push again. The PR is `not ready` until its
  current head has no blocking finding and every required check passes;
  `should-fix` findings are the maintainer's call, not a gate (#458).
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
