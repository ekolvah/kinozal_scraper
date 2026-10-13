## Why

Claude Code sessions in this repository export telemetry without a project label, so their
tokens cannot be told apart from any other unlabelled session. Observed 2026-10-01 (#611):
`claude_code_token_usage_tokens_total` over 7 days carries `vcs_repository_name` =
`ekolvah/agent-process-distribution` (≈ 1.04B tokens) or nothing at all (`{}`, ≈ 114M), and this
repository's sessions land in `{}`.

Root cause: `.claude/settings.json` has no `env.OTEL_RESOURCE_ATTRIBUTES` (re-read on `main`
at 15942e3, 2026-10-07). The plugin repository sets it in its own settings, which is why
attribution "worked in the plugin". The plugin's ADR 0026 defines the label, and its
`.agent-process/docs/telemetry-measurement-setup.md` (3.10.1) makes it per adopter: "Each
adoption carries its own value, never this repository's. An adopter sets the pairs by hand".
Neither the process environment nor `HKCU\Environment` sets the variable on this machine
(checked 2026-10-02; on 2026-10-07 the Bash tool's environment carries only
`CLAUDE_CODE_ENABLE_TELEMETRY`), so the settings value replaces nothing.

Scope (comment on #611, 2026-10-02, and epic #617): the dashboard, signal catalogue, delivery
check and `tests/test_claude_otel_assets.py` move to the plugin (agent-process-distribution#308)
after the backend decision (#103), and #617 then deletes the local copies. Re-syncing `agwhkq`
(acceptance 2) moves with them. The label is not part of that move: it is per adopter whatever
backend #103 picks, so it stays here, and its guard must not live in a file #617 deletes.

Re-planned 2026-10-07 because main moved since the first plan: `project-map.md` now records
that `observability/*` and the telemetry scripts are owned by #614 track 2, and #617 lists the
test file the first plan extended among the copies to delete.

## What Changes

- `.claude/settings.json` gains `env.OTEL_RESOURCE_ATTRIBUTES =
  vcs.repository.name=ekolvah/kinozal_scraper,vcs.repository.url.full=https://github.com/ekolvah/kinozal_scraper`.
- New `tests/test_otel_project_label.py`: one structural test that fails when the value lacks
  either pair or holds an entry that is not one `key=value` (the SDK discards a malformed value
  whole).
- `docs/architecture/coverage-gaps-agent-tooling.md` gains entry `AU`: no standing check guards
  the live label, with the probe evidence, the query that checks it and its revisit trigger;
  `coverage-gaps.md` lists it.
- `docs/architecture/operations.md`: step 4 of "Verify and import" says the JSON is a temporary
  copy and `agwhkq` is intentionally not re-synced.
- `docs/architecture/project-map.md`: the label and its test join "Repository-owned process
  files that stay".

## Capabilities

### New Capabilities
- `agent-telemetry`: project attribution of this repository's Claude Code telemetry.

### Modified Capabilities

## Impact

- Edited: `.claude/settings.json`, `docs/architecture/operations.md`,
  `docs/architecture/project-map.md`, `docs/architecture/coverage-gaps-agent-tooling.md`,
  `docs/architecture/coverage-gaps.md`.
- Added: `tests/test_otel_project_label.py`; `openspec/changes/otel-project-label/` (proposal,
  design, tasks, delta spec, architect review).
- Removed: none.
- External: every Claude Code session started in this repository after merge exports with the
  label; no Grafana write.
