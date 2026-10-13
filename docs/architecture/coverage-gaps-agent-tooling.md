# Coverage gaps: agent tooling and observability

**Question this document answers:** Which accepted test gaps concern agent tooling, telemetry, and delivery automation.

- **AS. Nothing local detects drift from the agent-process plugin's session hooks (#625).**
  The navigation policy, memory checkpoint and git guard are the plugin's hooks, tested by the
  plugin's own CI; the git guard is the only local denial of risky git/`gh` commands
  (the ruleset still guards `main` server-side). Two local copies of the navigation facts are
  unguarded: the 28 000-byte budget constant in `tests/test_doc_headers.py` and the hook semantics
  described in `CLAUDE.md` §Environment. A later plugin release that changes them leaves these
  stale, which costs tokens, not correctness
  ([the rule](testing.md#rule-when-a-test-is-not-worth-writing)). A plugin that is not loaded
  takes every hook with it, which the `SessionStart` check reports as `agent-process skill not
  loaded`. The hooks are gated on the literal path `.github/workflows/agent-process.yml`: deleting
  it cannot merge (the ruleset requires `agent-process / quality`, which only that workflow
  reports), but a rename that keeps the workflow `name:` and job id still reports the check and
  silently turns every plugin hook off, the git guard included. Accepted because the file is
  installer-rendered and the plugin says to rerun the installer rather than edit it. **Revisit
  trigger:** a plugin release that changes the navigation hooks, the git guard or their gate.

- **AU. No standing check reads this repository's exported telemetry (#642).**
  `tests/test_otel_project_label.py` proves only that `.claude/settings.json` carries
  `env.OTEL_RESOURCE_ATTRIBUTES` with both `vcs.repository.*` pairs. Three live properties stay
  unobserved: that Claude Code applies a project-settings `env` value to its exporter (not
  reproduced as failing, not impossible: the settings reference lists the `env` variables Claude
  Code ignores, and `OTEL_RESOURCE_ATTRIBUTES` is not among them); token consumption per task in
  this repository; and that metrics and events both arrive. The plugin's per-task dashboard
  `aqxk4q` reads only the plugin repository's `task-readings.csv`, so it shows none of this
  repository's sessions. **Re-check:** `count by (vcs_repository_name) (last_over_time(claude_code_token_usage_tokens_total[1h]))`
  through `/api/datasources/proxy/uid/grafanacloud-prom/api/v1/query`, with the operator's own
  Grafana credentials; this project's sessions must not land under `{}`.
  Accepted without an exit-code check: nobody owns a standing credentialed probe for this
  repository. **Revisit trigger:** a Claude Code upgrade, or the plugin recording per-task readings
  for adopters (ekolvah/agent-process-distribution#415).

**Scope-skip (can't run without live credentials) — see [What does NOT get tested](testing.md#what-does-not-get-tested-in-this-repo):**

- **J. Concurrent state — true *parallel* execution is a non-target** (serial daily cron, no
  overlap → a crash/concurrency simulation would be work-for-work). Realistic failure modes
  *are* covered: rerun-after-crash idempotency (dedupe index re-read) and notify-then-store
  ordering (a failed-notify item isn't stored → retried next run, no silent loss).
  Cell-level partial `gspread` writes are scope-skip (live credentials).
