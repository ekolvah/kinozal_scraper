---
status: "accepted"
date: 2026-08-12
decision-makers: ekolvah
---

# Export Claude Code metadata to Grafana Cloud with native OpenTelemetry

## Context and Problem Statement

The local transcript ledger measures branch-level token trends, but it cannot
explain a change through model, cache, request, tool, or session dimensions.
Claude Code already emits those signals through OpenTelemetry. The decision is
where to retain and inspect this development metadata without exporting prompt,
response, code, or tool content, and without adding another model-driven
analysis pipeline.

## Decision Drivers

* Preserve the existing branch ledger until external retention and attribution
  are proven equivalent.
* Use a standard exporter and backend rather than repository runtime code.
* Keep endpoint authentication and instance identifiers outside git.
* Make unavailable dimensions visible instead of treating them as zero.
* Measure storage and cardinality before defining interruption or notification
  thresholds.

## Considered Options

* Native Claude Code OpenTelemetry exported directly to Grafana Cloud
* A local collector or self-hosted observability stack
* Repository hooks that interrupt expensive sessions
* A scheduled model that reads telemetry and writes GitHub issues
* Keep only the existing local transcript ledger

## Decision Outcome

Choose **native Claude Code OpenTelemetry exported directly to Grafana Cloud**.
The user-scope exporter sends metrics and events over OTLP/HTTP with cumulative
metric temporality. Credentials remain in the operator's user environment. The
repository contains only a values-free setup template, a catalogue of signal
and attribute names observed at the destination, and an importable reporting
dashboard.

Content-bearing exporter flags remain absent. The bounded acceptance capture
confirmed that prompt and response fields were redacted and that tool input,
tool content, and raw API bodies were not received. Identity metadata such as
user email and session ID is still exported, so Grafana Cloud is a deliberate
external trust boundary rather than a content-free anonymous sink.

The dashboard reports; it does not interrupt Claude, invoke a model, schedule
analysis, create issues, or define thresholds. A threshold becomes a separate
decision only after the 14-day baseline shows an operator action and an
acceptable false-positive rate. `scripts/token_trend.py` remains unchanged
because Grafana Cloud Free retains only a rolling 14-day window and the observed
schema has no git-branch dimension.

### Consequences

* Good, because the measured token, cache, cost, tool, model, effort, and
  main/subagent-source dimensions are available without a custom collector.
* Good, because repository tests can guard asset structure and privacy defaults.
* Bad, because live delivery, backend mapping, retention, and dashboard import
  cannot be proven by offline CI.
* Bad, because session and identity dimensions increase storage cardinality and
  disclose development metadata to an external provider.
* Neutral, because estimated cost supports comparison but is not a billing
  source of truth.

### Confirmation

`tests/test_claude_otel_assets.py` verifies the values-free template, captured
signal references, dashboard structure, required decision panels, and absence
of repository automation. Live acceptance requires a real Claude session whose
first metrics and logs exports succeed, a destination query that returns both
signal types, and a successful dashboard import. The live boundary is recorded
as coverage gap **AN**.

## Pros and Cons of the Options

### Native OpenTelemetry and Grafana Cloud

* Good, because both interfaces are supported by their providers.
* Good, because the repository owns no long-running process.
* Bad, because direct export lacks collector-side retry, sampling, and redaction.
* Bad, because Free-plan data expires after 14 days.

### Local collector or self-hosted stack

* Good, because it provides local control over filtering and retention.
* Bad, because one developer would operate infrastructure for a small signal
  volume without a demonstrated need.

### Runtime interruption hooks

* Good, because they can stop a known runaway session immediately.
* Bad, because no measured threshold yet distinguishes waste from legitimately
  complex work, so interruption would encode an unverified guess.

### Scheduled model analysis and issue creation

* Good, because it could produce prose without manual dashboard review.
* Bad, because it spends more model tokens before recurring operator toil or an
  actionable decision has been demonstrated.

### Existing transcript ledger only

* Good, because it is local, branch-aware, and already operational.
* Bad, because it cannot explain changes through the request, cache, tool, or
  attribution dimensions available in native telemetry.
