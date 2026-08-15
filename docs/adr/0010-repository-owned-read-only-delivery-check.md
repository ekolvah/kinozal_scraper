---
status: "accepted"
date: 2026-08-15
decision-makers: maintainer
consulted: Claude architect-reviewer subagent (issue #542)
informed: repository contributors
---

# A repository-owned, read-only delivery check may hold live Grafana credentials

## Context and Problem Statement

[ADR-0006](0006-claude-code-telemetry-in-grafana-cloud.md) deliberately kept the repository's
share of the telemetry pipeline small: "a values-free setup template, a catalogue of signal and
attribute names, and an importable reporting dashboard", with the dashboard reporting only — it
"does not interrupt Claude, invoke a model, schedule analysis, create issues, or define
thresholds". Nothing in the repository authenticates to the stack.

That boundary had a blind spot. In #542 event delivery was broken from the first day and nobody
noticed for months: every offline test passed, and an empty Loki panel is indistinguishable from
"there was no spend". The accepted coverage boundary for delivery
([`coverage-gaps.md`](../architecture/coverage-gaps.md) §AN) was a manual live check in
`operations.md`, and a manual check that nobody runs is an intention, not a gate. So: may a
committed script authenticate to Grafana with live credentials and return a non-zero exit code?

## Decision Drivers

* §IV — a degraded signal must reach the operator as a visible anomaly, not as silence.
* Goal function — a deterministic step belongs in a script with an exit code, not in prose.
* ADR-0006's actual concern: no bespoke collector, daemon, scheduler, or alerting layer that
  duplicates what the platform already does, and no thresholds invented before a baseline.
* CI has no stack credentials, so this cannot become a CI gate.

## Considered Options

* Keep the manual check and restate it more firmly in `operations.md`.
* Configure a Grafana alert rule on the stack.
* A committed, read-only check script that the operator runs, holding credentials from `.env`.

## Decision Outcome

Chosen option: **a committed read-only check script**, because it is the only option that turns
the discrepancy into an exit code while leaving ADR-0006's real prohibitions intact.

The delta this ADR records against ADR-0006 is narrow and bounded:

* The repository may contain a script that authenticates to the Grafana stack **read-only** —
  `GET` through the datasource proxy — using credentials that live outside git, in `.env`.
* It stays **thresholdless**. Its finding is the observable fact "one signal present over the
  window and zero series of the other", in both directions, not a tuned lag limit. ADR-0006's
  deferral of thresholds until a measured baseline stands unchanged.
* It stays **operator-invoked**. No daemon, no scheduler, no hook, no CI wiring, no writes to the
  stack, no issue creation, no model invocation.
* The dashboard's prohibitions are untouched: no `alert` in the dashboard JSON, no bespoke
  automation inside `observability/claude-code/`, which
  `tests/test_claude_otel_assets.py::TestNoBespokeAutomation` still enforces.

Restating the manual check was rejected: the same words had already failed for four months, and
the goal function routes a deterministic step to a script rather than to a firmer instruction.
A Grafana alert rule was rejected because it would require lifting the dashboard's `alert` ban and
would move the logic into unversioned stack configuration, where a review cannot see it.

### Consequences

* Good, because a broken signal now fails loudly instead of rendering as an empty panel.
* Good, because the check is verifiable offline: its verdict logic is a pure function unit-tested
  against captured windows, with the network confined to a thin wrapper.
* Bad, because the repository now contains a maintainer tool that only works where stack
  credentials exist — a class of script it did not have before.
* Bad, because the raw Prometheus/Loki response shape the wrapper normalizes is not covered by any
  fixture, and a change in that shape would be caught only by running the check. Recorded in
  `coverage-gaps.md` §AN rather than left implicit.
* Neutral: an operator who never runs the check is no worse off than before, but the failure is
  now one command away rather than four manual steps away.

### Confirmation

`tests/test_otel_event_delivery.py` proves the verdict on the captured windows, including that an
unreadable stack and an empty window are each distinguishable from health.
`tests/test_claude_otel_assets.py::TestNoBespokeAutomation` continues to prove that no automation
leaked into the telemetry asset directory. The dashboard remains free of `alert`.

## More Information

The observation that motivated this decision is in issue #542: all twelve live `claude.exe`
processes spawned by the IDE were missing `OTEL_EXPORTER_OTLP_ENDPOINT` while the registry held
it, so events had nowhere to go while metrics — carried by a differently-inherited process — kept
arriving. Revisit this ADR if the check ever needs to write, schedule itself, or define a
threshold: each of those crosses back over ADR-0006 and is a new decision, not an extension of
this one.
