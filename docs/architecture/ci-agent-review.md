# Agent review workflow

**Question this document answers:** How agent review reaches a PR and how this repository pins the models of its own subagents.

## Agent review workflow (`agent-review.yml`)

`.github/workflows/agent-review.yml` is a managed caller rendered by the `agent-process` plugin
installer: it calls the plugin's reusable agent review on every `pull_request: opened/synchronize`
and reports the check run `agent-review / agent-review`, which the ruleset requires
([`ci.md`](ci.md)). The review prompt, its findings contract, its model pin and its outcome mapping
belong to the plugin and are documented upstream; this repository owns only the caller and the
secret it passes. The review does **not** approve or merge — the human reviewer keeps that.

### Model pinning and what a stale pin looks like

**Single home for the repository's model surface (#374 + #392).** The local subagents under
`.claude/agents/*.md` run on a Claude model. The policy is one — pin explicitly, never run on an
alias — and it lives here. The *canon* is the files themselves (each agent's frontmatter); there is
deliberately **no registry document listing which agent runs on which model**, because a copy of
the config is exactly the thing that drifts away from it. The plugin's own agents pin their models
upstream and are outside this policy.

Each agent's frontmatter carries a full model id and an explicit `effort`.

Three facts without which the pin is fixed incorrectly:

1. **`effort` defaults to inheriting the session level** — not `high`. Without a pin, the same
   subagent is stricter or looser depending on whose session starts it; the pin makes its strictness
   a repository decision.
2. **The guard rejects aliases and floating pointers** (`opus`/`sonnet`/`haiku`/`fable`, `default`,
   `latest`, `inherit`) — any full ID passes. There is no “a new model was released” notification:
   revision happens **because of a red run**, not by calendar. The pin is deliberately
   **family-level**: this generation has no dated snapshot ID, so a point release within a
   generation is accepted, while a generation change is not.
3. **Effort values are an upstream set** (`low|medium|high|xhigh|max`); an unrecognised value is
   ignored silently, so the guard checks membership rather than presence.

**A stale pin is loud, by design.** A removed or mistyped ID is a visible error
(`There's an issue with the selected model (…)` / `Agent terminated early due to an API error`);
Claude Code does **not** silently fall back to the session model. But model resolution is higher in
the stack, and frontmatter is not first there, so the pin does **not** protect against three things:
`CLAUDE_CODE_SUBAGENT_MODEL` in the operator's shell; the Agent tool's per-invocation `model`
argument — **the only one of the three reachable from inside the repository**, since nothing
prevents a caller from passing `model`/`effort` and silently defeating the pin; the organisational
`availableModels` allowlist — when it excludes the pinned value, Claude Code **silently** skips it
and takes the inherited model. This is recorded so “pinned” is not read as a stronger guarantee
than it is.

**One guard, one denylist.** `tests/test_agent_frontmatter.py` checks agent frontmatter against
its own `UNPINNED_MODEL_VALUES` set. It is a **denylist**: it can only reject too much, not let
something through.

### One-time setup

1. Locally: `claude setup-token` (requires Claude Pro/Max subscription) → copy the token.
2. Repo Settings → Secrets and variables → Actions → New repository secret:
   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: the token from step 1.
3. The caller passes it to the plugin's reusable workflow as the `claude_code_oauth_token` secret
   (separate from `anthropic_api_key`; OAuth tokens do not work as API keys).

No separate Anthropic API billing — usage counts against the Pro/Max subscription quota.
