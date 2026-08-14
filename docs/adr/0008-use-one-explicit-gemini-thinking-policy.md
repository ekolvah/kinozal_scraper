---
status: "accepted"
date: 2026-08-14
decision-makers: ekolvah
consulted: Codex planner self-review
---

# Use one explicit Gemini thinking policy

## Context and Problem Statement

Gemini thinking controls differ by request generation: 2.0 rejects a thinking
configuration, 2.5 accepts `thinking_budget=0`, and 3.x uses named thinking
levels. Within 3.x, some observed models accepted `minimal` while another
accepted only `low`.

Probing `minimal`, retrying `low`, and caching the result handles that observed
pair, but it cannot adapt if a future API replaces both values or the argument
itself. It also adds a second request, mutable runtime state, and error paths.

## Decision Drivers

* Keep request construction small and predictable.
* Make one generation request per caller attempt.
* Preserve the 2.5 and older request dialects.
* Prevent the observed 3.x truncation where an output cap is configured.
* Let an unknown future contract fail visibly instead of guessing.

## Considered Options

* Use `low` and an up-front output budget for every 3.x model.
* Probe `minimal`, retry `low`, and remember the result for the run.
* Maintain a per-model allow-list.
* Omit thinking configuration for 3.x.

## Decision Outcome

Choose **one explicit policy per request generation**:

* 3.x sends `thinking_level="low"`. If the caller sets `max_output_tokens`, the
  first request uses at least 1024, the smallest observed completing budget.
* 2.5 sends `thinking_budget=0`.
* Older models send no thinking configuration.

Callers create a fresh config and call `generate_content` directly. There is no
thinking-level probe, retry, allow-list, or capability cache. An incompatible
future request surfaces through the existing config-rejection path and requires
an explicit policy update based on the new API contract.

### Consequences

* Good, because every caller has one request path and no mutable capability state.
* Good, because a 3.x request never spends a round-trip probing `minimal`.
* Good, because unsupported future API changes remain visible.
* Bad, because models that support `minimal` spend more thinking tokens at `low`.
* Bad, because a configured 3.x output cap below 1024 is raised.
* Neutral, because the API-generation boundary remains an explicit version check.

### Confirmation

`TestThinkingPolicy` checks that both observed 3.x variants use `low` and the
safe budget on their first and only request. `TestThinkingConfigGate` preserves
the 2.5 and older dialects. `TestGeminiSummarizerRecovery` checks the shared
policy from the summarizer call path.

## Pros and Cons of the Options

### Uniform `low`

* Good, because it is accepted by the current 3.x models in scope.
* Good, because it needs no retry or runtime state.
* Bad, because it gives up the cheaper zero-thinking path on compatible models.

### Probe and remember

* Good, because compatible models retain the cheaper `minimal` request.
* Bad, because it handles only two known values and adds state plus a retry.

### Per-model allow-list

* Good, because known models need one call.
* Bad, because every new model requires another maintained entry.

### Omit thinking configuration

* Good, because it avoids naming a possibly unsupported level.
* Bad, because observed 3.x calls consumed the output budget on thinking and
  returned `MAX_TOKENS`.
