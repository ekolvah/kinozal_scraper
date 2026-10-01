# Branch-protection status checks

**Question this document answers:** Which required GitHub status checks protect the main branch and how they are verified.

## Required status checks (branch protection)

Three contexts block a merge into `main`: **`quality`** (`ci.yml`), **`pr-link`**, and
**`agent-review`** (`agent-review-v1.yml`).
(`pr-link.yml` → `scripts/verify_pr_link.py`, a PR from an `issue-N` branch must close its
issue). The **machine-checked canon** of that set is `REQUIRED_CONTEXTS` in
`scripts/check_branch_protection.py` — this paragraph is prose that can rot, that constant is
compared against GitHub and against the workflow files.

The ordinary `agent-review` job is required because its deterministic final step reads the action's
schema-validated outcome directly: `clean` succeeds, `rework` succeeds **with a visible
`::warning::`**, `blocking` fails, and absent or malformed output is a readable
`review unavailable` failure.

**One context, two carriers (#478).** Required contexts are AND-ed, so a second required
context would make availability *worse* — both providers would need quota. The carriers
therefore sit inside this one job as an ordered failover: `Claude review` runs with
`continue-on-error`, `Classify review outcome` asks
`check_agent_review_outcome.py --classify` whether that produced a usable verdict, and
`Codex review` runs only when the answer is `false`. A `blocking` verdict is a result, so
it is never failed over and never overruled. Exactly one of the two enforcement steps
runs, each naming its producer, so a head never collects two verdicts.

Carrier 2 is **Codex code review through its GitHub integration**, not an action in this
runner: `openai/codex-action` authenticates by API key only, and a carrier switched on by
buying a key does not solve an availability problem. `scripts/request_codex_review.py` is
the whole adapter — it reads the existing reviews, posts `@codex review` once if none of
them answers for this head, then waits with a declared bound. Only a review by
`chatgpt-codex-connector[bot]` **on the current head SHA** counts, and its state is the
verdict: changes requested → `blocking`, a plain comment → `rework`, approved → `clean`.
That mapping is instructed, not guessed: `AGENTS.md` § Code Review Rules — the file Codex
reads for repository rules, and the second home of the review contract — tells the reviewer
to request changes only for a blocking finding. No answer within the bound leaves an empty
payload, and the enforcement step reds the check exactly as before. Rationale and rejected
options: [ADR 0003](../adr/0003-second-carrier-for-the-required-review-gate.md).

**Merge authority is narrower than report coverage (#458).** The prompt requires every finding to
be reported at every severity, so a should-fix finding is the normal outcome of a thorough review.
Reding the required check on it made a green result unreachable by construction: one delivery PR
went through ten review rounds, the last four of them cosmetic, two of those fixing wording
introduced by the previous round (#458). So only `blocking` blocks: bugs, security, a violated task contract, a
missing test for changed behaviour. `should-fix` findings stay visible in the PR and are the
maintainer's decision, not a condition for `clean`. What is *not* evidence — empty, malformed or
unknown outcome, unavailable live PR context — stays red: absence of evidence must never read as
success (§IV). A Claude comment is feedback for people,
not merge authority, so ordinary PRs neither poll GitHub comments nor start a second Claude invocation.
Transport or quota failure is therefore red and is re-run after the provider recovers; it is never
silently treated as `clean`.

Because that conclusion already separates blocking from non-blocking
deterministically, the agent-side loop reads it rather than the review body:
`python -m scripts.review_gate <PR>` turns the check's state on the current head
into an exit code, so «only `blocking` blocks» stops being a sentence an agent
can skip. Its verdicts are documented in
[agent-process.md](agent-process.md#review-gate-verdicts); the gate is read-only
and is not a CI job.

An ordinary fork PR has no Claude OAuth secret and remains red for its missing
outcome; a maintainer moves it onto a repository branch to run the required
review. Separately, no required context is trusted evidence on any fork: all
three execute PR-head code (`ci.yml`, `scripts/verify_pr_link.py`, and
`scripts/check_agent_review_outcome.py`), so a fork can make its own check
green. A controller-verifier fork therefore uses the accepted
single-maintainer fallback: the maintainer's IDE-agent review and merge
decision.

A PR changing the review controller itself is reviewed like any other (#483).
The `Claude review` step passes `github_token: ${{ github.token }}`, which the
action returns instead of exchanging OIDC for a GitHub App token — and the
App-token path is what refused to run whenever the head's workflow file differs
from `main`. Before that input, such a PR ended in `WorkflowValidationSkipError`:
a green `agent-review` with no model invocation at all, which is why an empty
outcome used to be excused there. The exception is gone with its cause; empty is
an unavailable review on every path. The trust model and what it costs are in
[ADR-0004](../adr/0004-controller-pr-review-runs-on-the-workflow-token.md).

**A required context blocks the merge when it does not report at all, not only when it is red.**
That happens when the head SHA never ran the job: a first-time contributor's fork PR awaiting
maintainer approval, disabled Actions, or a renamed workflow on the PR branch. `enforce_admins:
true` leaves no override. The cheap recovery is that `pr-link.yml` also triggers on `edited`, so
editing the PR title/description re-runs it; pushing a commit works too. The same lockout risk
that disqualifies `review` applies to `pr-link` and is **accepted** here: its trigger set covers
every PR event and it runs on `github.token` alone, so it has no secret to lose. Three ways to
manufacture that trap are guarded, because each one leaves a declared context permanently
"Expected" and locks out even the PR that would undo it: renaming the job (a required context is
the check-run name — a job's `name:`, else its key), putting a `strategy.matrix` on it (real
contexts become `job (value)`), and adding a `paths`/`paths-ignore`/`branches`/`branches-ignore`
filter to the workflow's `pull_request` trigger (the job then simply does not run on some PRs —
a docs-only PR against a `paths:`-filtered `ci.yml` is the realistic case).

A job that calls a reusable workflow (job-level `uses:`) never reports under its own name:
GitHub names each called job's check run `<caller> / <called job>`. The guard therefore keys such a
caller `<caller> / *`, which is why the managed `agent-process / *` and `agent-review / *` callers
sit in `NOT_REQUIRED` rather than in `REQUIRED_CONTEXTS`: a prefix key matches no declarable
context, so they become required through the plugin ruleset (#599), never through this list.

With `strict: true` the "Update branch" button creates a new head SHA, so all required contexts re-run —
an expected extra minute, not a malfunction.

**Drift detection.** `python scripts/check_branch_protection.py` prints the actual contexts and
exits `1` on drift, `2` when the tool itself fails (no `gh`, no admin rights, unparseable
response) — a tool failure must not read as "no drift". It runs **on demand**: since the
agent-process pre-commit hook replaced `.githooks/pre-push` (ADR-0013 step B, #598), no push runs
it, which is the loss ADR-0013 records. The probe assumes the caller holds admin rights on the
repository — true while this is a single-maintainer repo, and the first thing to revisit if that
changes. Why this is not a CI job — GitHub's `GITHUB_TOKEN` has no `administration` scope, so a
CI form needs a stored admin-scoped token whose rotation cost buys nothing here; the full
reasoning lives in the script's docstring.

A second loss comes with the prefix keying of reusable callers above: the matrix and trigger
filter checks cannot see a caller's called jobs, so they no longer guard `agent-process / *` or
`agent-review / *`. ADR-0013's risk that a plugin release renaming a job or adding a matrix or
trigger filter can lock every PR therefore applies from step B, not from C. Prefix matching is
deliberately not built: the guard leaves with step D (#600).

**Declaring an intentional drift.** `--allow-drift "<reason>"` exits `0` and prints the reason.
It existed so that the push hook never had to be bypassed with `--no-verify`, which also
swallows `ci_check` — a gate that regularly demands bypassing teaches bypassing, and the next
bypass eats a genuine red (#458).
