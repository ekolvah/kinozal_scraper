## Context

See proposal.md — Why. The OTLP transport (`CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_EXPORTER_OTLP_*`)
lives in `HKCU\Environment` and is not committed; `OTEL_RESOURCE_ATTRIBUTES` is set nowhere
(`reg query HKCU\Environment` and the process environment, 2026-10-02). The plugin documents the
label it expects (`telemetry-measurement-setup.md`, "Which label carries the project") and
observed that the dotted keys arrive as `vcs_repository_name` / `vcs_repository_url_full`
labels on every `type` of the token series, and as Loki structured metadata.

## Goals / Non-Goals

**Goals:** this repository's sessions export under the project label; a structural test that
outlives the telemetry move keeps the value present and parseable; the operator who reads the
import procedure learns that `agwhkq` is stale on purpose.

**Non-Goals:** re-syncing `agwhkq`, a project variable on the dashboard, deleting `axwvz9`,
moving or deleting the telemetry assets — all in agent-process-distribution#308 and #617. No
backfill of the unlabelled history: it ages out of the 14-day retention. No plugin-side check of
adopters' labels: ADR 0029 takes telemetry out of the v2 migration.

## Decisions

### D1. The label lives in the committed project settings `env`

Alternatives: the user environment (machine-wide, so every project gets the same value, and the
plugin makes it per adopter); `.claude/settings.local.json` (not committed, so a fresh clone or
worktree exports unlabelled). Committed `.claude/settings.json` is the carrier the plugin
documents and uses itself. Observed precedence (plugin doc, "Precedence is observed"): a
settings `env` value replaces a process-environment value of the same variable; nothing sets it
on this machine, so nothing is lost. The values are the public repository name and URL.

### D2. One structural test in a new `tests/test_otel_project_label.py`

Problem it closes: the attribution regresses silently — an `env` block dropped by a later
settings rewrite (the plugin's `init` writes this file) or a malformed value makes the SDK
discard the variable, and the only catcher today is the person noticing `{}` on a query (how
#611 was found). Standard for the job: the OpenTelemetry SDK's own parser. Claude Code runs the
Node SDK, which a Python test cannot call, and `opentelemetry-sdk` is not a dependency here;
adding it for one assertion costs a pinned dev dependency under `pip-audit`.

Placement: not `tests/test_claude_otel_assets.py`. That file is in the Scope of
agent-process-distribution#308, and #617 deletes it once the plugin release lands; the label
stays, so its guard would be deleted with it. A file of its own is the only placement that
survives #617 without a later move. No other settings test is left to host it:
`tests/test_settings_deny.py` was deleted by #640.

The test asserts only the invariant production depends on, with no helper: split on `,`, every
entry holds exactly one `=` (the spec: "a list of key value pairs, represented as
`key1=value1,key2=value2`"; "In case of any error … the entire environment variable value SHOULD
be discarded", opentelemetry.io/docs/specs/otel/resource/sdk), and the pairs *contain* the two
required ones. Containment, not string equality, so a later per-task attribute appended to the
same variable (plugin doc: "The carrier is a list from the first commit on purpose") does not
break it. Percent-decoding, empty and repeated keys are not checked: the committed literal holds
no `%`. Prior art: the plugin's `tests/agent_process/test_delivery_gate_wiring.py` checks its own
value the same way.

### D3. Live verification is one read-only query, no new script; the standing gap is ledgered as `AU`

`check_otel_event_delivery.py` proves delivery, not attribution, and #617 deletes it, so
extending it with a label dimension would be thrown away. The gap is real, not one-time: if a
later Claude Code version stops applying the settings `env` block, sessions export unlabelled
and every test stays green. It is recorded as a new entry `AU` in
`coverage-gaps-agent-tooling.md`, not inside §AN, because §AN describes the assets #617 removes
and the label outlives them. `AU` carries the task-3.1 evidence, the query below as the way to
re-check, and its revisit trigger — a Claude Code upgrade, or this project reappearing under
`{}`. It is an accepted gap, not a deferral: no repository owns an exit-code attribution check
(agent-process-distribution#308's Scope holds none, and ADR 0029 rules out a plugin-side one).

The Bash tool's environment carries no `OTEL_*` variable (observed 2026-10-02 and again
2026-10-07, Claude Code 2.1.283: only `CLAUDE_CODE_ENABLE_TELEMETRY`), so the agent cannot launch
an exporting probe: the person runs one short session from the change's worktree, then the agent
reads `count by (vcs_repository_name) (last_over_time(claude_code_token_usage_tokens_total[1h]))`
through the existing datasource proxy (`grafanacloud-prom`, the URL and token loaders of
`check_otel_event_delivery.py`). The output goes into the PR report. Not `increase()`: a short
`claude -p` session exports once, at exit, and `increase()` needs two samples. Observed
2026-10-07 by the architect review on the #611 probe (`probe/direct-export`): 1 sample on each
of its 8 token series, `increase(...[14d])` 0 series, `last_over_time(...[14d])` 8 series.

### D4. Docs: one operator note, one project-map row

`operations.md` "Verify and import" step 4 is where an operator would re-import the stale JSON
and create a third dashboard, so the note sits there: the JSON is a temporary copy pending #617
/ agent-process-distribution#308, `agwhkq` intentionally not re-synced. The note is meant to
leave with the stack. No "Project label" paragraph in `operations.md`: its telemetry section is
in #308's Scope and #617 deletes it, so a label description there would die while the label
lives. The record that outlives the stack is the project-map row below plus `AU` (with its
check query); the how-to is the plugin's `telemetry-measurement-setup.md`.

`project-map.md` already says `observability/*` and the telemetry scripts are owned by #614 track
2, so the first plan's edit of the `observability/agent-telemetry/` row is dropped. Instead the
label and `tests/test_otel_project_label.py` get a row in "Repository-owned process files that
stay" (reason: per adopter, plugin ADR 0026/0029), so #617 does not delete them with the stack.
No ADR here: the home decision is the plugin's and is recorded in #308 and #617.

## Risks / Trade-offs

- [Settings `env` not applied on relaunch or in an untrusted folder] → both were "not
  reproduced" by the plugin on 2.1.265/2.1.268; the D3 query on a fresh session is the catcher
  on this machine's version (2.1.283).
- [The repository is renamed] → the test pins the literal values; a rename fails it, which is
  the intended prompt to update the label.
- [Unlabelled history stays in `{}`] → accepted; it leaves the 14-day window.
- [#103 picks Langfuse] → the label still applies: it is an OTel resource attribute on every
  exported signal, not a Grafana feature.

## Migration Plan

Merge applies the label to sessions started from `main` afterwards; worktrees branched after
merge inherit it. Rollback: remove the `env` key and the test file in one commit; series already
exported keep their label.
