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

- **AK. The second review-gate carrier has not been verified by a live run (#478).** Carrier 2 —
  Codex code review through the GitHub integration — is structurally covered (`TestFallbackCarrier`:
  step order, launch condition, verdict tied to head SHA, bounded wait, output name, producer
  attribution, red gate for a missing carrier) and behaviourally covered by its adapter
  (`tests/test_request_codex_review.py`: whose review counts as a verdict, selection by head SHA,
  state conversion to the outcome dictionary, round-trip payload through the enforcement script).
  Guards **do not** prove two things, both on the other side of the contract: (1) that Codex answers
  an `@codex review` posted by `github-actions[bot]`, not a human, at all; and (2) that it sets the
  review state requested by `AGENTS.md` § Code Review Rules — public documentation says only that it
  raises P0/P1 findings in GitHub, so its bar is already above our coverage-first policy. **The skip
  is conscious, not silent:** both failures look like "no verdict" → empty payload → red
  `agent-review` with an explicit `::warning::` identifying who did not answer. The unverified branch
  cannot weaken the gate; it can only not work, which appears as a red check rather than a green PR
  without review. It is the same class as **AD** (the network half of branch protection): testable
  only by a live run against an external service. **Closure trigger: the first run where Codex leaves
  a review on the head SHA**; a link to the run and the review goes into the `## Agent record` issue,
  and this entry is removed. Full decision:
  [ADR-0003](../adr/0003-second-carrier-for-the-required-review-gate.md).

- **AL. Guards do not prove that carrier-1 review actually runs under the workflow token (#483).**
  Only the input (`github_token: ${{ github.token }}`) and removal of the carve-out are structurally
  pinned; whether upstream then does not validate the workflow and whether the action has rights to
  its PR records under that token is the other side of the contract, testable by a live run. It is
  the same class as **AK** and **AD**. **The skip is not silent, but the signal is not a red check:**
  if former behaviour returns, an empty carrier-1 outcome gives `valid=false`, which launches carrier
  2 (#478), and its `clean` makes the check green. Regression is visible because
  `Classify review outcome` prints `valid=false` and `Codex review` **runs** (log and
  `## Agent record`); `agent-review` becomes red only if carrier 2 also does not answer. Missing
  permissions appear as a write error in the step log. **Closure trigger: a run on the PR that makes
  this change**; it is controller-shaped by construction, and its log (`valid=true`, executed
  `Enforce Claude review outcome`, summary with `Reviewed head SHA:`) enters `## Agent record`.
  Full decision: [ADR-0004](../adr/0004-controller-pr-review-runs-on-the-workflow-token.md).
- **AM. No guard on *which commands* the navigation policy covers (#485).**
  `scripts/navigation_policy.py` decides that a shell stage reads the filesystem and denies it
  with the replacement call named. Its **behaviour** is tested (`tests/test_navigation_policy.py`:
  file-operand forms denied, pipe stages allowed, `sh -c` unwrapped, unparseable input fails
  open), and so is its **wiring** — including the negative invariant that no `permissions.deny`
  entry shadows the hook, since a static rule matches first and would swallow the message.
  What is deliberately *not* pinned is the membership of `_RULES`: `awk` and `wc` are outside it
  (no tool replaces line counting; `awk` was never measured), and adding or dropping a command
  costs tokens and nothing else, which
  [the rule](testing.md#rule-when-a-test-is-not-worth-writing) routes to a forcing function
  rather than a guard test. Do not reopen the membership list as an anti-drift ratchet.

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

- **AO. Offline tests cannot prove Codex → Alloy → Grafana delivery or shared-dashboard import
  (#472).** `tests/test_codex_otel_assets.py` guards metrics-only Codex config, loopback receiver,
  Delta-to-Cumulative-before-batch routing, environment-only cloud credentials, captured signal
  references, no-data semantics, and missing attribution. It cannot authenticate to the
  maintainer's Grafana stack, prove Alloy's experimental processor against a future Codex payload,
  observe OTLP-to-Prometheus translation, or execute Grafana import/query. The accepted boundary is
  the credentialed live check in
  [`operations.md`](operations.md#verify-codex-delivery): Alloy ready, a completed fresh app-server
  turn, accepted and sent points with zero failed points, destination `codex_*` series, and a
  successful shared-dashboard query. **Revisit trigger:** Codex exposes compatible Cumulative
  export, Alloy changes the processor contract, delivery/import fails, or a captured name or
  attribute disappears. Missing issue, branch, tool-name, or tool-success dimensions remain
  unavailable rather than zero.

- **AP. Export-manifest completeness remains partially guarded.** The predicted failure occurred:
  the false claim that plugin `plugin.json` could carry `.claude/settings.json` permissions survived
  a full review-and-merge cycle. `tests/test_agent_process_template.py` now reads the Layer 0, Layer
  2, and Layer 1 Copier-channel rows from `docs/architecture/agent-process-export.md`, requires a
  template counterpart for each, renders both `claude_adapter_installed` branches, and rejects
  dangling rendered Markdown links. What remains uncovered is exhaustive source-tree classification:
  a new process file can still be omitted from every manifest table, and plugin-marketplace rows
  have no package consumer until that package exists. Revisit when the plugin package is built or a
  manifest-wide source inventory becomes cheap enough to keep honest.

- **AQ. Copier rendering is representative, not an exhaustive answer matrix.**
  `tests/test_agent_process_template.py` renders the default answers and representative alternate
  combinations,
  then checks the generated Python and Markdown rather than feeding raw Jinja templates to the
  repository's source checks. That covers both sides of every current conditional, citation and
  prose-hole guards, Markdown parenthesis balance, missing referenced tests, Python compilation,
  and Ruff formatting/linting. It deliberately does not enumerate the full Cartesian product of
  independent answers: those combinations do not introduce different template branches. The
  generated default also omits target-specific mypy, dependency-audit, requirements, and import
  contracts because the portable payload ships no application package, lockfile, or target import
  graph to check. A target should enable those checks after adding their prerequisites. Revisit
  when a new conditional creates an unrendered branch, or when the payload gains application code
  or dependency files that make one of those checks meaningful.

**Scope-skip (can't run without live credentials) — see [What does NOT get tested](testing.md#what-does-not-get-tested-in-this-repo):**

- **J. Concurrent state — true *parallel* execution is a non-target** (serial daily cron, no
  overlap → a crash/concurrency simulation would be work-for-work). Realistic failure modes
  *are* covered: rerun-after-crash idempotency (dedupe index re-read) and notify-then-store
  ordering (a failed-notify item isn't stored → retried next run, no silent loss).
  Cell-level partial `gspread` writes are scope-skip (live credentials).
