---
status: "accepted"
date: 2026-08-08
decision-makers: ekolvah
---

# Review of a PR changing the review controller uses the workflow token, not a GitHub App token

## Context and Problem Statement

A PR changing `.github/workflows/claude-review.yml` received no review from carrier 1 **at all**:
`anthropics/claude-code-action` completed successfully after printing
`Skipping action due to workflow validation`, while the required `claude-review` context
turned green. There was no model invocation and no comment; the outcome classifier reproduced this:
`Classify review outcome` printed `valid=false`, while enforcement released the PR under the carve-out.

The cause is OIDC exchange for the Anthropic GitHub App token. A token is issued only to a run whose workflow
file matches the version on the default branch; on a PR that edits that file, exchange fails
(`workflow_not_found_on_default_branch`) and the action treats that failure as a reason to silently skip work
(`WorkflowValidationSkipError`).

The compensation was a **policy**: for controller PRs an empty outcome was not a red check but
`::warning::`, and the maintainer’s manual IDE review guaranteed the complete diff before merge. That policy
covered the symptom at the cost of a hole in the required gate: the only class of PR that changes the review
mechanism was not reviewed. Question: how can this PR class return to the normal gate?

## Decision Drivers

* **The gate is mandatory precisely where it matters most.** A controller change changes what checks all other
  changes; an exception there is the worst possible location for an exception.
* **The absence of review must be visible (§IV).** A green check without one model invocation is a pure silent
  skip and reached the maintainer only as a log line.
* **Manual policy is not machine-verifiable.** “The maintainer viewed the diff in the IDE” leaves no head-SHA
  trace and is indistinguishable from “did not view it.”
* **Model authorization must not change.** Review operates on a subscription (`claude_code_oauth_token`); any
  solution requiring a paid API key does not solve the task (the same argument as [ADR-0003](0003-second-carrier-for-the-required-review-gate.md)).
* **Job permissions are part of the trust model.** The action executes code from the PR head; its granted scope
  is what that code possesses.

## Considered Options

* Set `github_token: ${{ github.token }}` as action input
* Keep the carve-out and manual IDE review (the status quo)
* A separate “bootstrap” workflow from the default branch that reviews controller PRs
* A proprietary GitHub App with a key in repository secrets

## Decision Outcome

Chosen: **an explicit `github_token`**. Upstream puts the input value in `OVERRIDE_GITHUB_TOKEN`, and
`setupGitHubToken()` returns it **before** OIDC exchange—so “workflow file matches the default branch” validation
does not run at all rather than being bypassed. The controller PR receives a normal verdict, and the carve-out
is removed with its cause: `scripts/check_agent_review_outcome.py` no longer knows paths,
`scripts/review_gate.py` no longer knows controller classification, and the manual IDE-review policy in
`agent-process.md` is **repealed** ([ADR-0003](0003-second-carrier-for-the-required-review-gate.md) is unaffected:
there are still two carriers and the same failover).

`id-token: write` is removed with the exchange: with its own token the action does not request an App token,
and retaining the permission would be a second trust model invisible in the check. `pull-requests: write`
covers tracking, summary, and inline comments; the action makes no commits here. Model authorization is unchanged—
it is a second, independent credential.

### Consequences

* Good, because the PR class that changes the gate again passes the gate: an empty outcome is now red on every
  path, and “there was no review” is no longer treated differently anywhere.
* Good, because a whole policy branch is removed: path classification, its CLI options (`--repo`/`--pr`), its
  `escalate` verdict in `review_gate.py`, and the process paragraph. Less code means less that can diverge from reality.
* Good, because a controller PR becomes its own compatibility check: review executes the workflow version at head,
  so a broken controller turns itself red rather than the next unrelated PR.
* Bad, because a controller PR verifies itself: review runs code from head. This is a **residual trust assumption**,
  not eliminated risk—under one maintainer and a private repository, the same person writes head and merges it.
  The compensation is structural, not mechanical: enforcement scripts always check out from the default branch
  (a broken head can turn the check red but cannot turn no review green), and the controller PR remains narrowly
  scoped. This assumption is false on a fork, where the verifier fork remains governed by the general rule that
  “no required context on a fork is evidence.”
* Neutral: workflow and App tokens differ in comment author and scope. Review comments now publish as `github-actions[bot]`.

### Confirmation

Guards: `tests/test_review_gate.py::TestEvidence::test_controller_paths_are_not_special_in_the_verdict`
verifies the portable gate rule. The workflow-token configuration and its invocation are target-authored, so the
target project must test that its own workflow passes the token and enforcement output without a controller-path
exception.

What guards do not prove is that a live run actually passes: that is an external side of the contract. Verify
it on the PR making the change (it is inherently a controller PR): the log lacks `Skipping action due to workflow validation`,
`Classify review outcome` prints `valid=true`, `Enforce Claude review outcome` runs, `Codex review` is skipped,
and a PR summary with `Reviewed head SHA:` appears. If an operation in GitHub fails under the workflow token,
add the missing scope in the same PR.

## Pros and Cons of the Options

### Explicit `github_token`

* Good, because this is a documented action input, not a validation bypass: validation simply does not run
  when a token already exists.
* Good, because the change is one input line plus a removed permission, and it is reversible.
* Bad, because the isolation of “review runs under an external, non-repository token” is lost.

### Status quo: carve-out and manual IDE review

* Good, because it costs nothing and already works.
* Bad, because it leaves a hole in the required gate precisely for the PR class that changes the gate, and
  replaces machine evidence with a human promise.

### Separate bootstrap workflow from the default branch

* Good, because the reviewer is not taken from head—self-verification genuinely disappears.
* Bad, because it requires `pull_request_target` checking out foreign code or a second required context; the first
  is a known vulnerability class, while the second is ANDed with the existing one and reduces availability (the same
  argument as ADR-0003).
* Bad, because it creates a second copy of the prompt and a second home for the review contract.

### Proprietary GitHub App

* Good, because it provides exact permission scope and a stable comment-author identity.
* Bad, because it solves the wrong task: “workflow matches main” validation is a property of upstream exchange,
  and a proprietary App does not remove it, only exchanges one token for another at the cost of a private key in
  secrets and its rotation.

## More Information

* This record repeals the controller-PR manual IDE-review policy.
* The mechanism was checked against `anthropics/claude-code-action@v1` source, not inferred from a trial:
  `action.yml` passes the `github_token` input to the environment as `OVERRIDE_GITHUB_TOKEN`;
  `src/github/token.ts` has `setupGitHubToken()` return the supplied token before OIDC exchange, while
  `isWorkflowValidationError()` recognizes exchange failure and converts it to `WorkflowValidationSkipError`,
  meaning successful completion without work.
* Defect observation is recorded in the source repository's issue history.
* State-document consequences: the target project's branch-protection documentation
  and [`agent-process.md`](../architecture/agent-process.md#review-outcome-enforcement).
* Revisit the record if the repository gains a second maintainer or external contributors with permission to push
  repository branches: then the residual self-verification assumption will become unacceptable and require a reviewer
  not taken from head.
