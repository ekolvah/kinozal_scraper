# Agent review workflow

**Question this document answers:** How the required agent-review workflow produces and validates review evidence.

## Agent review workflow (`agent-review.yml`)

Triggers: every `pull_request: opened/synchronize`. Uses
`anthropics/claude-code-action@v1` to run an automated code review on every PR push: inline
comments at relevant lines plus a top-level verdict. Does **not** approve or merge — the human
reviewer keeps that.

Visibility is guaranteed by two independent layers:

- `track_progress: true` — the action itself posts a tracking comment at start and updates it
  as the run proceeds. Whatever Claude does or doesn't do, this is at least one visible signal
  that the review ran.
- The prompt instructs Claude to (a) post per-issue inline comments via
  `mcp__github_inline_comment__create_inline_comment` and (b) finish with a top-level summary
  via `update_claude_comment`. Controlling comment *format* is not enough: a run that finds
  no issues and invokes no publishing tool leaves the PR silent.

The job first checks out the default-branch verifier source, so the deterministic step remains
importable if the live PR API read fails. The primary invocation returns a schema-validated `clean`,
`rework`, or `blocking` outcome. The following shell step maps it directly to the job result; no
marker, polling, or repair invocation is in the ordinary path. Before that invocation, the workflow
obtains the current PR number, body and head SHA through the GitHub API. A re-run keeps its original
event payload, so this explicit read is
what keeps a re-run from reviewing an old PR description or SHA. The body is passed only as fenced,
untrusted data in an action input — never interpolated into a shell command — and the requested
summary names the live head SHA. If that API read fails, the deterministic step reports `live PR
context is unavailable` and stays red; it does not spend quota on a second model call. A controller
change is now its own compatibility check, because the review runs on the PR head (#483): a red
`Claude review` step reporting schema validation means the reviewer is unavailable, so fix or
revert the controller change on that branch rather than weakening the gate.

**Deliberately temporary:** `show_full_output: true` (the full SDK transcript in Actions logs) is
enabled while review behaviour stabilizes; it is noisy and can expose internal model chatter.
**Removal trigger:** the review loop no longer requires transcript analysis — that is, when the
transcript was last needed for diagnosis, not “someday”.

### Coverage-first prompt: no filtering at the search stage

**The defect mechanism is why the contract exists, not archaeology.** The model follows a filter
instruction (`Skip nitpicks — ruff handles formatting/lint`) **literally**: it makes a finding,
rates it below the stated threshold, and silently does not report it — while a filtered finding is
indistinguishable from its absence (§IV). The same defect has a second form at **output**: the
instruction “post exactly "✅ Review complete — no blocking issues found."” prohibited adding
anything else, so a run with three should-fix and zero blocking had to print one line. Removing the
input filter while retaining the output filter fixes half the problem. The same mechanism defines
`.claude/agents/architect-reviewer.md`.

The contract is **grade, never drop**:

- every finding is reported with `severity` (blocking / should-fix / nice-to-have) and `confidence`
  (high / medium / low) — a human filters, not the model;
- `blocking` is a concrete bar (wrong behaviour, a failing or missing test for changed behaviour, a
  misleading result, a leaked secret, or a `CLAUDE.md` convention violation), not the qualitative
  word “nitpick”;
- anything already caught by a deterministic gate (ruff / mypy in `ci_check.py`) is graded
  `nice-to-have, duplicate of ci_check` — ranked last because another executor covers it, not
  withheld;
- **only blocking / should-fix go inline** so the inline channel does not drown; the rest go in the
  summary, which lists every finding by severity. A fixed one-line summary applies only to “nothing
  found at any severity”.

`tests/test_agent_review_workflow.py` guards **form**: no suppression imperative at the start of a
prompt line, `severity` + `confidence` present, and no `no blocking issues` gag line. It does not
check semantics — a filter reworded as “be selective” passes; the qualitative half (that the
blocking bar remains concrete and the ruff exception non-imperative) is upheld by this prose and
review, not an exit code.

### Model pinning and what a stale pin looks like

**Single home for the whole model surface (#374 + #392).** Two review surfaces run on a Claude
model: this cloud workflow and the local plan-stage `architect-reviewer` (`.claude/agents/*.md`).
The policy is one — pin explicitly, never run on an alias — and it lives here. The *canon* is the
files themselves (the workflow's `claude_args`, the agent's frontmatter); there is deliberately
**no registry document listing which agent runs on which model**, because a copy of the config is
exactly the thing that drifts away from it.

`agent-review.yml` contains `claude_args: |` / `--model claude-opus-5`; the agent frontmatter has
`model: claude-opus-5` + `effort: high`.

Four facts without which the pin is fixed incorrectly:

1. **The action has no `model` input.** `claude_args` is the documented Claude CLI passthrough
   (`action.yml`: "Additional arguments to pass to Claude CLI"), and this matters more than it
   appears: GitHub Actions **silently ignores an unknown `with:`**, so a typo in the input name
   would leave review unpinned with every static check green.
2. **`effort` defaults to inheriting the session level** — not `high`. Without a pin, the same
   plan-stage review is stricter or looser depending on whose session starts it; the pin makes gate
   strictness a repository decision.
3. **A PR changing `agent-review.yml` itself checks outcome only when it has one.** An empty
   outcome produces a visible warning; `clean` and `rework` pass, while `blocking` turns red.
   Prompt contract and permitted model are checked on the next unrelated PR.
4. **The guard rejects only short aliases** (`opus`/`sonnet`/`haiku`/`fable`) — any full ID passes.
   There is no “a new model was released” notification: revision happens **because of a red job**,
   not by calendar. The pin is deliberately **family-level**: this generation has no dated snapshot
   ID, so a point release within Opus 5 is accepted, while a generation change is not.

**A stale pin is loud, by design.** A removed or mistyped ID is a visible error on every PR
(`There's an issue with the selected model (…)` / `Agent terminated early due to an API error`);
Claude Code does **not** silently fall back to the session model. But model resolution is higher in
the stack, and frontmatter is not first there, so the pin does **not** protect against three things:
`CLAUDE_CODE_SUBAGENT_MODEL` in the operator's shell; the Agent tool's per-invocation `model`
argument — **the only one of the three reachable from inside the repository**,
`.claude/commands/plan.md` starts `architect-reviewer` exactly this way, and nothing prevents
passing `model`/`effort` and silently defeating the pin; the organisational `availableModels`
allowlist — when it excludes the pinned value, Claude Code **silently** skips it and takes the
inherited model. This is recorded so “pinned” is not read as a stronger guarantee than it is.

**Two guards, one denylist.** `tests/test_agent_review_workflow.py` checks the workflow,
`tests/test_agent_frontmatter.py` checks agent frontmatter; both import the shared set from
`tests/_model_pin_policy.py`. These are **denylists**, so their union is strictly more conservative:
it can only reject too much, not let something through.

### One-time setup

1. Locally: `claude setup-token` (requires Claude Pro/Max subscription) → copy the token.
2. Repo Settings → Secrets and variables → Actions → New repository secret:
   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: the token from step 1.
3. The workflow consumes it via `${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}` passed as the action's `claude_code_oauth_token` input (separate from `anthropic_api_key`; OAuth tokens do not work as API keys).

The workflow also needs `id-token: write` in `permissions:` — `anthropics/claude-code-action@v1` uses OIDC for GitHub App auth, and without that scope every run fails with "Could not fetch an OIDC token".

No separate Anthropic API billing — usage counts against the Pro/Max subscription quota.
