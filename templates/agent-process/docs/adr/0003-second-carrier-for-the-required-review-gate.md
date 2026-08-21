---
status: "accepted"
date: 2026-08-07
decision-makers: ekolvah
---

# Two carriers hold the required review gate behind one context; the second is failover

## Context and Problem Statement

`pr_reviewer` is the only delivery-flow role with one carrier: code review is performed by
`claude-review.yml`, and that job is a required context in branch protection
(`{"contexts":["quality","pr-link","claude-review"]}`, `enforce_admins: true`). Exhausted
Claude subscription quota exhaustion therefore locks **all** PRs: the gate is mandatory, even the owner
cannot bypass it, and quota recovery can take hours.

The other roles survive carrier failure trivially: the planner and implementer are selected by a launch
route (`adapter_routes`)—a person simply opens another chat. The reviewer has no route: CI, not a
person, starts it.

Question: how can the role receive a second carrier without weakening the gate or paying for what a
subscription performs on a normal day?

## Decision Drivers

* **The gate must not weaken.** The second carrier obtains a verdict when the first did not provide one;
  it does not obtain a *different* verdict.
* **Required contexts in branch protection are ANDed.** Anything added as a second required context
  reduces availability rather than improving it.
* **No quota API exists**—neither Claude nor OpenAI provides a “how much remains” endpoint. The owner’s
  formulation, “the CI job should select the agent with quota,” is implementable only as a reaction to
  an actual failure, not as advance selection.
* **The second carrier must use the owner’s subscription.** There is no separate paid API key; a carrier
  enabled by buying a key does not solve availability, it only postpones it. `openai/codex-action` is such
  a carrier: it authenticates only with an API key (in its `action.yml`, every functional step is gated on
  `openai-api-key`). Subscription-based Codex code review exists, but not as a runner action—as Codex’s
  GitHub integration.
* **The validity rule must not gain a second home.** `scripts/check_agent_review_outcome.py` already knows
  what counts as a valid verdict; an expression in YAML would be a second, untested description of the policy.

## Considered Options

* One context, two carriers inside the job, ordered failover
* Carrier 2 as `openai/codex-action` with a paid API key
* A second required context with a separate workflow for the second carrier
* One context, select the carrier before invocation (by remaining quota)
* Owner manually bypasses the gate when quota is exhausted
* Do nothing: wait for quota recovery

## Decision Outcome

Chosen: **one context with two carriers and ordered failover**. `agent-review.yml` asks the first carrier
(`Claude review`, `continue-on-error: true`), then `Classify review outcome` invokes
`check_agent_review_outcome.py --classify` and publishes `valid=true|false`. The second carrier runs **only**
when `valid == 'false'`—that is, when the first left no valid structured verdict. Exactly one of the two
enforcement steps emits the result, and both invoke the same script with `--producer`, so the log always
shows whose verdict it is.

Carrier 2 is **Codex code review through the GitHub integration**, using the owner’s ChatGPT subscription.
It reviews outside this runner, so `Codex review` is not a model step but the adapter
`scripts/request_codex_review.py`: it reads existing reviews, posts `@codex review` once when the current
head has no response, and waits to a declared boundary. A verdict is **only** a review from
`chatgpt-codex-connector[bot]` **on the current head SHA**—a previous push’s review describes a different
diff from the one being merged. Review state is the verdict: changes requested → `blocking`, an ordinary
comment → `rework`, approved → `clean`. This mapping is prescribed, not guessed: carrier 2’s review contract
lives in `AGENTS.md` § Code Review Rules (the file from which Codex obtains repository rules) and requires
requesting changes only for a blocking finding. If it does not reply in time, the payload is empty and
`Enforce Codex review outcome` fails the check. That is the literal present behavior: no verdict means a red gate.

The role catalog gained `carrier_selection` (`run_route` | `ci_failover` | `sole`).
`pr_reviewer` declares `ci_failover` and `adapter_routes: null`: its carrier does not depend on the
launch route. The field is declared, not inferred from the number of adapters—otherwise a role with
two carriers could silently attribute a run to the wrong agent.

### Consequences

* Good, because exhausted subscription quota no longer locks merging: the gate remains singular and mandatory,
  but a verdict is obtainable in two ways.
* Good, because failover activates on the *absence* of a verdict, not its content: `blocking` from the first
  carrier is a result and cannot be overridden by the second.
* Good, because the second carrier costs no money: it uses the same subscription as other repository work and
  is enabled by configuring Codex code review, not issuing a key.
* Bad, because the review contract is duplicated in two copies: carrier 1’s workflow prompt and carrier 2’s
  `AGENTS.md` § Code Review Rules. They cannot be combined into one file—`claude-code-action` has no
  `prompt-file` input, GitHub Actions has no YAML anchors, and Codex reads only its own file. The compensation
  is target-owned prompt guards parameterized for both carriers, so divergent copies make that target's
  test red.
* Bad, because carriers respond in different formats and to different bars: carrier 1 writes inline comments
  and a structured outcome; carrier 2 leaves a normal GitHub review and GitHub publicly documents it as
  surfacing P0/P1 findings, narrower than this coverage-first contract. Therefore a green check from carrier 2
  is weaker than the same check from carrier 1; a target project records any
  accepted limitation in its own coverage-gap ledger.
* Bad, because carrier 2’s verdict arrives asynchronously: the job waits in a bounded loop
  (`--timeout-seconds`), so `agent-review` takes minutes rather than seconds on the failover branch. The cost
  is accepted: that branch is reachable only when carrier 1 already did not reply.
* Neutral: `check_agent_review_outcome.py` serves both carriers without a provider name.

### Confirmation

Guards: `tests/test_agent_orchestrator.py::TestCarrierSelection` verifies the portable carrier-selection
mechanism. The workflow that invokes either carrier is deliberately target-authored; its target project must
test the step order, head-SHA verdict, output hand-off, and provider-specific review-state mapping there.

What the guards do not prove: that Codex responds to `@codex review` from the bot and sets the review state
as `AGENTS.md` requests. Both sides of that contract are external, verified by one live run; until then the
record has unverified execution
(the target project should record any accepted limitation in its own ledger).

## Pros and Cons of the Options

### One context, two carriers inside the job, ordered failover

* Good, because branch protection is unchanged: the required-context set stays the same.
* Good, because carrier order is explicit and declared—the catalog `adapter` is asked first.
* Bad, because the job becomes longer and contains two nearly identical prompt blocks.

### Carrier 2 as `openai/codex-action` with a paid API key

* Good, because review stays entirely inside one job: a synchronous step, structured output in the same
  shape as carrier 1, and no waiting for an external service.
* Bad, because it does not solve the task: the action accepts only an API key, the owner has no separate key,
  and a carrier enabled by purchase does not unlock the gate when subscription quota is exhausted. The community
  workaround (store `~/.codex/auth.json` in a secret and inject it through `codex-home`) is rejected: it is an
  undocumented contract on an expiring token.

### A second required context with a separate workflow

* Good, because the second carrier is isolated, with its own file and log.
* Bad, because required contexts are ANDed: the PR now waits for **both** gates, and either carrier’s failure
  locks merging. The option does exactly the opposite of the goal.

### Select the carrier before invocation, by remaining quota

* Good, because it does not spend an attempt on a known-exhausted carrier.
* Bad, because it is unimplementable: neither provider has a remaining-quota endpoint. Any heuristic
  (“the last run failed → quota is gone”) is the same failover, but with inter-run state and a window in which it errs.

### Owner manually bypasses the gate

* Good, because it requires no code.
* Bad, because it requires removing `enforce_admins`—permanently weakening branch protection for a rare event;
  and a bypass leaves no review trail on the head SHA.

### Do nothing

* Good, because it has zero cost and zero risk.
* Bad, because failure costs an indefinitely blocked delivery, and it recurs: quota is exhausted more often
  as development becomes more active.

## More Information

* Gate mechanics and step order belong in the target project's branch-protection
  documentation; the `carrier_selection` field in the role catalog —
  [`agent-process.md`](../architecture/agent-process.md#roles-and-hand-offs).
* Provider-neutral names (`check_agent_review_outcome.py`, `agent-review` context)
  are process vocabulary; renaming a required context requires a PATCH
  migration of branch protection and is therefore outside this record.
* Codex code review’s subscription basis was checked against OpenAI documentation, not inferred from a trial:
  [pricing](https://learn.chatgpt.com/docs/pricing) (“ChatGPT Work and Codex are included in your ChatGPT … plan”; code review is billed only when Codex reviews through GitHub) and
  [GitHub integration](https://learn.chatgpt.com/docs/third-party/github) (`@codex review` triggers and automatic
  repository review, configured through `AGENTS.md` § Code Review Rules). The bot login was checked against the
  live API: `gh api apps/chatgpt-codex-connector` → owner `openai`.
* Enabling carrier 2 is a one-time configuration outside the repository: Codex cloud is connected to the repository
  and **Code review** is enabled. Without it, the step runs as “no verdict”—a red check with `::warning::`, not a silent green.
* Revisit this record if a remaining-quota API appears (then failover can be replaced by advance selection), or
  carrier 2 gains structured output (then its bar will no longer differ from carrier 1).
