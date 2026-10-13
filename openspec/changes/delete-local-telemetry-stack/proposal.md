## Why

#642 deletes this repository's copy of the development-telemetry stack, so that no second copy
drifts from the one the process uses (`agwhkq` already drifted, #611). The issue assumed the plugin
would take the assets over. It did not. What is on record:

- ekolvah/agent-process-distribution#308, closing comment: "Superseded. Per-task readings live on
  dashboard `aqxk4q` … The session dashboards `agwhkq` and `axwvz9` … are deleted from Grafana …
  Nothing is taken over from kinozal_scraper."
- #642, blocker update: #318 closed as narrowed, and the cost-per-task trend on `aqxk4q` is the
  accepted replacement for `scripts/token_trend.py`.
- Plugin 3.16.0 (installed by #644) ships `.agent-process/docs/telemetry-measurement-setup.md`
  (`gh api repos/ekolvah/agent-process-distribution/contents/.agent-process/docs/telemetry-measurement-setup.md?ref=v3.16.0`
  returns that path). It covers the user-scope transport, the project label, per-task readings on
  `aqxk4q`, and the diagnosis of an export that does not arrive. It ships no dashboard JSON, no
  signal catalogue and no delivery check.
- `aqxk4q` does not read this repository. All 7 of its panel targets (Grafana
  `$.panels[*].targets[*].url`) read
  `https://raw.githubusercontent.com/ekolvah/agent-process-distribution/task-readings/task-readings.csv`.
  The workflow `task-readings.yml` exists only in the plugin repository, and no installer file of
  3.16.0 references it. Here, `gh run list --workflow task-readings.yml` answers
  `HTTP 404: workflow task-readings.yml not found on the default branch`.

So the local stack serves dashboards that no longer exist. It repeats setup documentation the
plugin now owns, and its content-logging deny rule in `llm-security.md` contradicts the live setup
(plugin ADR 0039). The maintainer chose to delete the whole stack, the delivery check included
(decided in this propose run). Once it is gone, nothing reads this repository's exported series.
Coverage gap AU records that instead of letting it go silent. #642's "Done when" line, "the
plugin's dashboard … shows it", cannot hold until the plugin records readings for adopters.

## What Changes

- Delete the token-growth detector, the dashboard JSON, the signal catalogue, the setup template,
  the delivery check, and their tests, fixtures and `SessionStart` hook entry. The directory
  `observability/` goes away.
- Turn the telemetry docs into pointers to the plugin document: `operations.md` and
  `llm-security.md`.
- Retire coverage gaps AJ and AN, and widen AU to "no standing reader of this repository's exported
  telemetry".
- Set ADR-0006 and ADR-0010 to `deprecated`.
- Update the inbound references.

Kept: `env.OTEL_RESOURCE_ATTRIBUTES` in `.claude/settings.json`, `tests/test_otel_project_label.py`
and the `agent-telemetry` spec. These are a per-adopter setting; #641 separated them for this reason.
Only the test's docstring changes, because it names the deleted `test_claude_otel_assets.py`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The `agent-telemetry` spec covers only the project label, which stays, so the change sets
`skip_specs: true`.

## Impact

Removed:
- `scripts/token_trend.py`, `tests/test_token_trend.py`
- `scripts/check_otel_event_delivery.py`, `tests/test_otel_event_delivery.py`,
  `tests/fixtures/otel-delivery-both-signals.json`, `tests/fixtures/otel-delivery-empty.json`,
  `tests/fixtures/otel-delivery-gap.json`, `tests/fixtures/otel-delivery-metrics-gap.json`
- `tests/test_claude_otel_assets.py`
- `observability/agent-telemetry/dashboard.json`, `observability/claude-code/signal-catalogue.json`,
  `observability/claude-code/otel.env.example`

Edited:
- `.claude/settings.json`: the `token_trend` `SessionStart` group is removed.
- `docs/architecture/operations.md`, `docs/architecture/llm-security.md`
- `docs/architecture/coverage-gaps-agent-tooling.md`, `docs/architecture/coverage-gaps.md`
- `docs/architecture/project-map.md`, `docs/architecture/information-architecture.md`
- `tests/test_always_load_budget.py`, `tests/test_otel_project_label.py`: the docstring only.
- `docs/adr/0006-claude-code-telemetry-in-grafana-cloud.md`,
  `docs/adr/0010-repository-owned-read-only-delivery-check.md`: the status line only.

No dependency changes: `requests` is still used by `src/kinozal_scraper/`. No scraper code changes.
The doc edits ride along because they only remove references to the deleted files.
