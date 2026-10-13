## Context

The motivation and observations are in the proposal (Why). This change drops guards, so the
decisions below list what stops being proven and which catcher, if any, is reached.

## Goals / Non-Goals

**Goals:** no tracked file depends on the deleted assets, every doc guard stays green, and the
lost coverage is recorded as a gap rather than going silent.

**Non-Goals:**
- Making the plugin record per-task readings for adopters (a plugin change).
- Changing the project label or the `agent-telemetry` spec.
- Editing ADR bodies or archived changes.
- Adding a guard that pins plugin links to the installed tag.

## Decisions

### D1. Delete the whole stack, the delivery check included

The maintainer chose this in the propose run. The alternative was to keep
`check_otel_event_delivery.py` as the one live credentialed check. That would keep ADR-0010,
gap AN and Grafana credentials in `.env` alive for a check that is run by hand (§VII).

Two lost proofs have no catcher after the change:

| Lost proof | Former carrier | Catcher after this change |
| --- | --- | --- |
| Token use per branch or turn grew | `token_trend.py --hook`, every `SessionStart` | None: `aqxk4q` does not read this repository. Recorded in AU (D2). |
| Metrics and events both arrive | `check_otel_event_delivery.py`, run by hand | None automatic. The plugin document's extension-log diagnosis is manual. Recorded in AU (D2). |

The other dropped guards protected only deleted files, so they need no catcher:
`test_claude_otel_assets.py` (template, catalogue, dashboard, no-automation). The project label
stays guarded by `test_otel_project_label.py`.

### D2. Widen AU; retire AJ and AN

AJ and AN describe deleted files. Following `coverage-gaps.md`, their letters are retired and not
reused.

AU already covers this repository's exported series: it re-checks the project label with a Prometheus
query. After this change, its revisit trigger "sessions reappearing under `{}` on the dashboard"
can no longer fire, because no dashboard reads this repository. A separate gap for consumption and
delivery would cover the same series with the same query and the same reason ("nobody owns a
standing credentialed probe"), so AU is widened instead.

AU's new title is "No standing check reads this repository's exported telemetry". Its body covers
three things:
- the label, consumption and delivery are all unobserved;
- `aqxk4q` reads only the plugin repository's `task-readings.csv`;
- the re-check is the same query, run through the Grafana datasource proxy with the operator's own
  credentials, with no reference to the deleted script.

Its revisit triggers are a Claude Code upgrade, or the plugin recording `task-readings` for adopters
(or `aqxk4q` reading this repository), tracked as ekolvah/agent-process-distribution#415. This is
§IV applied to coverage: the gap stays visible.

The rejected alternative was to keep `token_trend.py` until the plugin covers adopters. #642's
blocker update already accepted removing it. Keeping it would also keep about 2,000 lines whose only
output is a stderr line at session start.

### D3. One pointer to the plugin, in `operations.md`

Everything under `## Claude Code development telemetry` except `### Rollback and rotation` becomes a
short pointer to
`https://github.com/ekolvah/agent-process-distribution/blob/main/.agent-process/docs/telemetry-measurement-setup.md`.
One sentence says what stays in this repository: `env.OTEL_RESOURCE_ATTRIBUTES` and its test.

The link uses `blob/main` because that is the form the other plugin links in `docs/` use
(`testing.md`, `coverage-gaps-quality-gates.md`). A tag-pinned link would go stale silently on the
next upgrade, as `ci-tooling-decisions.md`'s `v3.10.1` link already has.

`### Rollback and rotation` stays because `llm-security.md` links to it and its steps are still
true.

`llm-security.md` keeps the boundary class:
- telemetry is an external boundary that carries metadata and, by plugin decision, content;
- secrets stay outside git.

It drops the `OTEL_LOG_*` deny list, which contradicts the live setup, and points to the
`operations.md` section instead of copying plugin details.

### D4. ADR-0006 and ADR-0010 become `deprecated`, status line only

This follows ADR-0013's way of retiring records: change only the status and leave the body as
decided. The status value differs from ADR-0013's case. ADR-0013 itself replaced its siblings, so it
could write `superseded by ADR-0013`. Here, the replacing decisions are plugin ADRs (0029, 0037,
0039), and `tests/test_adr_records.py` resolves `superseded by` only to a local record. So the
status is `deprecated`, which is in `_STATIC_STATUSES`. ADR-0013 line 190 kept ADR-0006 "as is" because telemetry
was left out of the v2 migration (plugin ADR 0029). This change is that deferred step: once the
assets are gone, nothing in this repository enacts ADR-0006.

## Risks / Trade-offs

- [Token growth in this repository goes unnoticed] → AU names it, with a manual query and a revisit
  trigger. The maintainer accepted this risk by choosing D1.
- [A missed reference] → The repository-wide grep in task 3.4, plus `test_doc_links` and the full
  `ci_check`.

## Migration Plan

Deletions and doc edits only. After the merge, sessions no longer run the `token_trend` hook. To roll
back, revert the PR. The `token_trend` ledger under `~/.claude/projects/` stays on disk, so a revert
resumes it.
