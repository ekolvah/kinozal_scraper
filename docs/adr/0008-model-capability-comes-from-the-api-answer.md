---
status: "accepted"
date: 2026-08-14
decision-makers: ekolvah
consulted: Claude architect-reviewer
---

# Derive model capability from the API answer

## Context and Problem Statement

Gemini thinking controls have changed repeatedly: 2.0 rejects any thinking
configuration, 2.5 accepts `thinking_budget=0`, and 3.x uses named thinking
levels. The 3.x family is itself non-uniform: a live capture showed 3.6 accepting
`minimal`, while 3.7 rejects it and accepts `low`. Version-based rules therefore
turn each newly discovered model into another production request failure.

The API's model resource exposes only a boolean thinking capability, not the
supported levels. The generate response is the narrowest available authority
for selecting a level without maintaining a stale per-model table.

## Decision Drivers

* Preserve the cheap zero-thinking path on models that accept `minimal`.
* Recover automatically when a future model rejects `minimal`.
* Bound extra API calls and make fallback cost visible.
* Preserve the existing config-rejection alert when no supported level works.
* Avoid global capability state and per-model name heuristics.

## Considered Options

* Use `low` uniformly for every Gemini 3.x model.
* Probe `minimal`, fall back to `low`, and remember the answer for the run.
* Maintain a per-model allow-list or version threshold.
* Omit `thinking_config` for Gemini 3.x.
* Match the text of Google's rejection message.

## Decision Outcome

Choose **probe `minimal`, fall back to `low`, and remember the answer for the
run**. Each live caller owns per-model state. The first `minimal`
`INVALID_ARGUMENT` causes one retry of the same request at `low`; later requests
for that model go directly to `low`. A configured output budget is copied and
raised to at least 1024 on the fallback, the smallest live-tested budget that
completed the production-shaped response.

If `low` is also rejected, the helper raises the original SDK exception. The
existing caller-side classifier therefore still records `ModelConfigRejected`,
fires the operator alert, and fails the job. No exception-message matching is
needed.

### Consequences

* Good, because models that accept `minimal` keep the same request and no extra
  round-trip.
* Good, because a future model with the same capability split self-recovers
  without a code change.
* Good, because positive and negative results limit fallback probing to one
  extra call per model per run.
* Bad, because a model that requires `low` spends more thinking tokens and may
  use a larger output budget.
* Neutral, because 2.5 and older version gates remain necessary for choosing
  the different configuration shape.

### Confirmation

`TestThinkingLevelFallback` reproduces the captured 3.6/3.7 matrix through the
enrichment rotation, checks both capability-memory outcomes, budget copying,
WARNING visibility, and preservation of `config_rejected_models`.
`TestGeminiSummarizerRecovery` checks the shared seam at the summarizer caller.

## Pros and Cons of the Options

### Uniform `low`

* Good, because it accepts the newly observed 3.7 model.
* Bad, because 3.6 at the existing 220-token budget changes from a complete
  zero-thinking response to a truncated response.

### Probe and remember

* Good, because it preserves the existing request until the API rejects it.
* Good, because it adapts to capability rather than predicting it from a name.
* Bad, because the first rejecting call costs one additional round-trip.

### Per-model table or version threshold

* Good, because known models make one call.
* Bad, because it repeats the stale heuristic that already failed three times
  and requires a release for every new exception.

### Omit thinking configuration

* Good, because it avoids unsupported control values.
* Bad, because captured 3.x calls consumed the output budget on thinking and
  returned `MAX_TOKENS`.

### Match the rejection text

* Good, because it could distinguish the precise parameter failure.
* Bad, because provider wording is not a stable API contract; the structured
  `INVALID_ARGUMENT` plus the bounded two-level probe is sufficient.
