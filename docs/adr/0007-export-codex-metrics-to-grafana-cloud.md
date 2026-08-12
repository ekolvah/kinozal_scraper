---
status: "accepted"
date: 2026-08-12
decision-makers: ekolvah
---

# Bridge Codex metrics to Grafana Cloud through local Alloy

## Context and Problem Statement

The VS Code Codex `app-server` emits useful native OpenTelemetry metrics, but
the installed client fixes aggregation temporality to Delta. A bounded live
turn reached Grafana Cloud and was rejected with HTTP 400 for an invalid
temporality/type combination. Unlike Claude Code, Codex has no effective user
setting that switches this programmatic exporter to Cumulative.

The decision is whether to leave Codex absent from the shared agent dashboard,
parse local content-bearing transcripts, or operate a small compatibility
boundary on the maintainer workstation.

## Decision Drivers

* Preserve a metrics-only, counters-not-content privacy boundary.
* Resolve the reproduced temporality mismatch before creating dashboard queries.
* Keep Grafana credentials outside Codex and git.
* Make failed delivery and unavailable attribution visible.
* Give experimental infrastructure a pinned version and removal condition.

## Considered Options

* Direct Codex OTLP/HTTP export to Grafana Cloud
* Local Grafana Alloy Delta-to-Cumulative bridge
* Parse Codex rollout/transcript files
* Wait without collecting Codex metrics

## Decision Outcome

Choose **local Grafana Alloy Delta-to-Cumulative bridge**. Codex sends only
metrics to an OTLP/HTTP receiver bound to `127.0.0.1`. Alloy 1.18.1 runs with
the experimental stability level and routes metrics through
`otelcol.processor.deltatocumulative`, then `otelcol.processor.batch`, then an
authenticated Grafana Cloud OTLP/HTTP exporter.

Cloud endpoint and ingest credentials stay in the Windows user environment.
Codex knows only the loopback endpoint. Logs, traces, and prompt logging remain
disabled because native Codex log events can contain tool-result output.

The bridge is removed when a verified Codex/Grafana combination accepts direct
metrics with correct temporality. Removal requires the same live turn,
destination query, and dashboard check; an upstream claim alone is not enough.

### Consequences

* Good, because the root-cause payload is converted instead of retried unchanged.
* Good, because Codex holds no cloud credential and the receiver is not exposed
  to the network.
* Good, because the shared dashboard uses only names observed after successful
  ingestion.
* Bad, because one pinned user-scope process must start, be observed, and be
  upgraded deliberately.
* Bad, because the required converter is experimental in Alloy 1.18.1.
* Neutral, because native metrics still cannot attribute a session to a GitHub
  issue or branch and do not expose tool name/success in the observed tool-call
  family.

### Confirmation

`tests/test_codex_otel_assets.py` and `scripts/check_codex_otel_config.py` guard
the repository and user-config contracts. Alloy's `validate` command guards
River syntax. Live confirmation requires a fresh app-server turn, Alloy
accepted/sent counters without failed points, a Grafana `codex_*` query, and a
successful import/query of `observability/agent-telemetry/dashboard.json`.
Coverage gap **AO** records why CI cannot prove that credentialed boundary.

## Pros and Cons of the Options

### Direct Codex export

* Good, because it owns no local daemon.
* Bad, because the measured client/backend pair rejects the payload before
  storage, and the standard temporality preference does not override Codex.

### Local Alloy bridge

* Good, because it is a standard OpenTelemetry compatibility pipeline with
  batching and explicit failure counters.
* Bad, because its converter currently requires experimental stability.

### Local rollout parsing

* Good, because it needs no cloud ingestion path.
* Bad, because it creates custom parsing, content/privacy exposure, schema-drift
  support, and still lacks trustworthy issue correlation.

### Wait without telemetry

* Good, because it adds no infrastructure.
* Bad, because implementer usage stays absent from the operator's existing view.
