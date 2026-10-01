# Branch-protection status checks

**Question this document answers:** Which required GitHub status checks protect the main branch and how they are verified.

## Required status checks (branch protection)

The merge gate is the repository ruleset **`agent-process default branch`**, created by
`agent-process activate_protection` from the plugin's template. It applies to the default
branch with no bypass actors, so it binds administrators too, and carries four rules: no
deletion, no force-push, a pull request is required (no approval count), and two required
status checks, strict (the PR must be up to date with `main`): **`agent-process / quality`** and
**`agent-review / agent-review`** (the managed `agent-process.yml` and `agent-review.yml`), both
bound to the GitHub Actions app. `agent-process / quality` is an aggregate: each `ci_check.py`
registry entry and the issue-link check `agent-process / link` (a PR must close its issue)
report as their own check runs, and `quality` `needs` them all and fails unless each one
succeeded. The issue-link requirement therefore blocks a merge through `quality`, without a
context of its own. Classic branch protection carries **no** required contexts; its other
settings are untouched.

The ruleset is owned by the plugin, not by this repository: `agent-process activate_protection
--pr <N> --dry-run` is the drift check — `unchanged <id>` means the live ruleset matches the
template; `<N>` is any PR whose head has both checks green, which the command's preflight
requires — and the same command with `--confirm` is the only sanctioned writer. Rolling back
restores the classic contexts first and deletes the ruleset second, so `main` is never left
without a gate. The drift check runs **on demand**: neither the pre-push hook nor CI runs it,
which is the loss ADR-0013 records (ledger:
[`coverage-gaps-quality-gates.md`](coverage-gaps-quality-gates.md)).

What `agent-review / agent-review` reports — the findings contract, which severity blocks, how an
unavailable review fails — is the plugin's reusable review, documented upstream; this repository
does not restate it.

**A required context blocks the merge when it does not report at all, not only when it is red.**
That happens when the head SHA never ran the job: a first-time contributor's fork PR awaiting
maintainer approval, disabled Actions, or a renamed managed job. The ruleset has no bypass
actors. When the checks merely did not run on this head, pushing a commit re-runs them. When a
required check can never report — a plugin release renamed `quality` or `agent-review`, so the
listed context stays "Expected" on every PR, including the one that would fix it — the recovery
is a manual edit of the ruleset (Settings → Rules) to the new context name. The plugin command
cannot do it: its preflight needs a PR whose head already reports both listed checks green.
Nothing in this repository guards against a plugin release that renames a job or adds a matrix
or trigger filter; the recovery is that manual edit.

An ordinary fork PR has no Claude OAuth secret and remains red for its missing review; a
maintainer moves it onto a repository branch to run the required review. Separately, the quality
checks execute PR-head code, so a fork can make them green; for such a PR the accepted
single-maintainer fallback is the maintainer's own review and merge decision.

With `strict: true` the "Update branch" button creates a new head SHA, so all required contexts re-run —
an expected extra minute, not a malfunction.
