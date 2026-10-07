# Coverage gaps: agent tooling and observability

**Question this document answers:** Which accepted test gaps concern agent tooling, telemetry, and delivery automation.

- **AJ. Token-consumption metric is fundamentally not gated in `ci_check`/CI (#464).**
  `ci_check.py` has one `CHECKS` registry for local runs and CI, but metric data are Claude Code
  transcripts on the maintainer machine, absent from CI. An entry in `CHECKS` would either make CI
  always red or skip for missing data — exactly the silence against which the metric exists. The
  `SessionStart` hook takes the gate role: it runs itself every session and prints **only** an anomaly;
  `tests/test_token_trend.py::TestHookRegistration` guards against losing hook registration (without
  it, the script would repeat eval's fate from #361 — a metric that nobody runs). Tests cover pure
  logic (parsing, aggregation, schema-1/2 ledger migration, interaction counters, detector), **both output formats**, and `main()` in both modes
  on a substitute directory; only `transcript_dir()` remains uncovered — an upstream slug rule
  testable only by actual run. Its failure is not silent: if `~/.claude/projects` exists but lacks
  our directory, the hook prints `transcripts_not_found` rather than remaining silent. The tests use
  inline JSONL, so they cannot establish that an installed Claude Code version still emits the
  request/tool-block shape; malformed `Read` input is visible as an anomaly and schemas 1/2
  deliberately render interaction metrics unavailable. Revisit if a
  shared development-telemetry carrier appears that CI can read.

- **AS. Nothing local detects drift from the agent-process plugin's session hooks (#625).**
  The navigation policy and memory checkpoint are the plugin's hooks, tested by the plugin's own
  CI. Two local copies of their facts are unguarded: the 28 000-byte budget constant in
  `tests/test_doc_headers.py` and the hook semantics described in `CLAUDE.md` §Environment. A later plugin release that changes them leaves these stale, and a
  deny entry that shadows a plugin hook only drops its "use this tool instead" message — both cost
  tokens, not correctness ([the rule](testing.md#rule-when-a-test-is-not-worth-writing)). The
  hooks are gated on the literal path `.github/workflows/agent-process.yml`: deleting it cannot
  merge (the ruleset requires `agent-process / quality`, which only that workflow reports), but
  a rename that keeps the workflow `name:` and job id still reports the check and silently turns
  every plugin hook off. Accepted because the file is installer-rendered and the plugin says to
  rerun the installer rather than edit it. **Revisit trigger:** a plugin release that changes the
  navigation hooks or their gate.

- **AN. Offline tests cannot prove Claude Code telemetry delivery or Grafana dashboard import
  (#471).** `tests/test_claude_otel_assets.py` guards the values-free setup template, captured
  signal references, dashboard JSON structure, required decision groups, and absence of bespoke
  automation. It cannot authenticate to the maintainer's Grafana stack, prove that Claude Code's
  bundled exporter still maps headers and metric temporality correctly, observe backend name
  translation, or execute Grafana's import/query path. Those are credentialed external contracts.
  The boundary has since moved: delivery itself is no longer a manual step (#542).
  `python scripts/check_otel_event_delivery.py` reads both signals over one window and exits
  non-zero when either half is missing while the other arrived, so the discrepancy now carries an
  exit code instead of an operator's intention to look. Its own verdict logic is
  unit-tested on the captured windows in `tests/fixtures/otel-delivery-*.json`
  (`tests/test_otel_event_delivery.py`); what stays offline-unprovable is the Prometheus/Loki
  response shape the thin I/O wrapper normalizes, which no fixture of a *raw* proxy answer covers.
  **Still manual:** dashboard import, and confirming that content fields remain redacted/absent —
  the latter is a different property from delivery, kept as its own Explore step in
  [`operations.md`](operations.md#verify-and-import) because the check never inspects line content.
  **Revisit trigger:** a provider changes the exporter or OTLP mapping, the dashboard import
  fails, a captured signal/attribute disappears, or the proxy response shape changes under the
  wrapper. The previous wording made the whole live check manual, it was never run, and event
  delivery stayed broken for months while every offline test passed — a trigger nobody executes
  is not a boundary. Update the values-free catalogue only from a new live capture; never make a
  missing dimension pass as zero.
  **Consciously rejected coverage (#549):** no guard test pins the events half of
  `capture.signal_provenance` to `status == "unreproduced"` in
  `observability/claude-code/signal-catalogue.json` — that value is expected to turn `verified`
  once the operator step restoring event delivery lands (#542), and a value-pinning test would
  then fail as the truth improved
  ([`testing.md`](testing.md#rule-when-a-test-is-not-worth-writing)). The structural invariant that
  *does* stay guarded: every half carries its own `status`/`observed`/`claude_code_versions`, a
  non-`verified` half carries `absent_on`, and the flat top-level verdict this replaced
  (`capture.status`/`captured_at`/`claude_code_version`) may not resurface
  (`test_each_signal_half_carries_its_own_observation`). One half's `verified` status is never
  inherited by the other — each is its own claim, evidenced by its own capture. **Revisit trigger:**
  `python scripts/check_otel_event_delivery.py` exits `0`; update the events half's provenance from
  that new live capture, not by hand-editing the JSON, or the `unreproduced` marker goes stale in
  the opposite direction and starts lying about a gap that has since closed.

**Scope-skip (can't run without live credentials) — see [What does NOT get tested](testing.md#what-does-not-get-tested-in-this-repo):**

- **J. Concurrent state — true *parallel* execution is a non-target** (serial daily cron, no
  overlap → a crash/concurrency simulation would be work-for-work). Realistic failure modes
  *are* covered: rerun-after-crash idempotency (dedupe index re-read) and notify-then-store
  ordering (a failed-notify item isn't stored → retried next run, no silent loss).
  Cell-level partial `gspread` writes are scope-skip (live credentials).
