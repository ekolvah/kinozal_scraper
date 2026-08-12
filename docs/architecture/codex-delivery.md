# Codex issue-branch publication

**Question this document answers:** how a maintainer gives VS Code Codex the
least privilege needed to publish this repository's issue branches through the
`ready-for-human` boundary.

## Trust boundary

Codex separates the sandbox capability from the approval policy. An action may
therefore be technically sandboxed and still require approval before it leaves
that boundary. The local CLI and IDE extension default to workspace-limited
writes and no command network access; these controls should remain in place.
See the official OpenAI documentation for
[agent approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security).

This repository does not install an approval rule and does not auto-approve its
own network mutations. It provides two reviewable artifacts:

- `scripts/push_issue_branch.py`, a no-argument publisher that accepts only a
  clean current `issue-N-*` branch and the canonical repository `origin`;
- `scripts/publish_pr_report.py`, a no-argument PR creator/updater that reads
  only the fixed ignored `.codex/pr-body.md` workspace path and derives the
  issue, title, branch, and PR;
- `docs/examples/kinozal-delivery.rules`, an inactive user-layer template that
  allows the named delivery entry points and forbids direct `git push` plus
  `gh pr merge`.

The wrapper is the authority boundary. It accepts no remote, refspec, force
flag, or hook-bypass argument. GitHub branch protection remains the final
barrier, and merge remains a human action.

## One-time VS Code setup

1. Mark this exact checkout trusted in the user-level
   `~/.codex/config.toml`; untrusted projects do not load project hooks or other
   project-scoped Codex configuration. Use the checkout's actual absolute path:

   ```toml
   [projects.'C:\path\to\kinozal_scraper']
   trust_level = "trusted"
   ```

2. Review `docs/examples/kinozal-delivery.rules`, then copy it to the user
   layer as `~/.codex/rules/kinozal-delivery.rules`. Do not place it under the
   repository's active `.codex/rules/` directory: installation must remain an
   explicit maintainer decision.
3. Restart Codex. If `/hooks` reports an untrusted project hook hash, review and
   trust that exact hook definition before delivery.

Project trust and rule discovery are defined in the official
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [rules guide](https://learn.chatgpt.com/docs/agent-configuration/rules).
No token, credential, personal absolute path, unrestricted network switch, or
`approval_policy = "never"` belongs in the repository.

## Verification

Use Codex's own deterministic policy evaluator after installation:

```powershell
codex execpolicy check --pretty --rules "$HOME/.codex/rules/kinozal-delivery.rules" -- python scripts/push_issue_branch.py
codex execpolicy check --pretty --rules "$HOME/.codex/rules/kinozal-delivery.rules" -- python scripts/publish_pr_report.py
codex execpolicy check --pretty --rules "$HOME/.codex/rules/kinozal-delivery.rules" -- git push -u origin issue-499-example
codex execpolicy check --pretty --rules "$HOME/.codex/rules/kinozal-delivery.rules" -- gh pr merge 501
```

The first two decisions must be `allow`; the latter two must be `forbidden`.
`python -m pytest -q tests/test_push_issue_branch.py
tests/test_publish_pr_report.py tests/test_codex_delivery_rules.py
tests/test_settings_deny.py` checks the
repository half of the contract. The execpolicy test runs when the Codex CLI is
installed and otherwise skips without pretending to validate a missing tool.

## Delivery and recovery

After the GREEN commit and foreground `ci_check`, publish with:

```powershell
python scripts/push_issue_branch.py
```

Write the completed PR template to `.codex/pr-body.md`, then run
`python scripts/publish_pr_report.py`. Continue with `gh pr checks <PR>
--watch` and `review_gate` as
defined in [the deterministic delivery flow](agent-process.md#deterministic-delivery-flow).
Use the same push and report wrappers for each fixer commit and the final
review-gate record.

Wrong remote, wrong branch, a dirty worktree, a failed Git probe, or a failed
push is a visible non-zero result. Correct the named state and rerun the
wrapper. Do not fall back to direct push, `--force`, `--no-verify`, or merge
from Codex.
