# kinozal_scraper architectural principles

Source of truth on principles, workflow rules, and quality gates for this
repo. `CLAUDE.md` and the other `docs/architecture/*.md` files are runtime
guidance and reference implementation detail — where they conflict with
this document, this document wins.

Originally authored as a Spec Kit constitution (v1.0.0, ratified
2026-05-17). Migrated to `docs/architecture/principles.md` on 2026-05-21
when Spec Kit was removed; references to `/speckit-*` commands replaced
with the project's local `/plan` → `/implement` workflow (see #114).

## Core Principles

### I. Test-First (NON-NEGOTIABLE)

For every behavioural change a failing test exists **before** the implementation
commit. The `/implement #N` workflow enforces this: write tests from the
issue's `## Test plan` section, run pytest to confirm RED, then write code
to make them GREEN (see #114).

Exceptions, all narrow: (a) rename / move of an already-tested symbol, (b)
documentation-only PRs, (c) one-line non-behavioural fixes (typos, comments).
Anything that changes what the code *does* needs a test that would have caught
the prior behaviour.

A test that pins a known production bug (`TestConfigValidationKnownGaps` style)
is allowed only when an issue tracks the fix; when fixed, the test MUST be
inverted to assert the correct behaviour and the test-coverage row promoted
from ⚠ documents-current-bug to ✅.

**Rationale:** historical inversion (code → tests post-hoc) hid drifts like
issue #57 for months. Writing the test first makes the contract explicit and
the divergence audible.

### II. Protocol Boundaries with Dependency Injection

Every external service is hidden behind a `typing.Protocol` and injected into
callers. Production wires real clients in `if __name__ == "__main__":` blocks;
tests wire in-memory doubles via the same constructor signature.

Hard rules:

- `Storage`, `Notifier`, `Enricher` are the three current boundaries (see
  `docs/architecture/runtime.md`). Any new external dependency MUST get its
  own Protocol.
- Implementations receive **ready clients**, not credentials. `SheetsStorage`
  takes a constructed `gspread.Client`, not a service-account dict. Auth lives
  in the caller, never in the storage/notifier/enricher layer.
- Tests use in-memory doubles (`InMemoryStorage`, `InMemoryNotifier`,
  `NullEnricher`). **No mocks of internal functions**: `run_*_pipeline` and the
  `_extract_*` helpers are always called directly from tests.

**Rationale:** mocks of internal logic let production drift undetected (the
test passes against its own duplicate copy of the logic). Protocol doubles
exercise the real code path with deterministic external state.

### III. Delivery Truthfulness

The system MUST NOT silently lose user-visible data. If a pipeline finds new
items or channel text but cannot deliver the corresponding Telegram message,
the run MUST surface that as an explicit operational failure: log ERROR, mark
the `PipelineResult` not-ok where that type is used, and exit non-zero from
the run-script step. A best-effort technical alert should be sent when the
failure is not the Telegram transport itself.

Persisted dedupe state MUST reflect confirmed delivery. Pipelines that can
partition send results MUST store only successfully delivered items. A
pipeline may persist before notifying only when it also turns any delivery
failure into a visible operational failure; green silent skips are forbidden.

If Telegram delivery itself is unavailable, the script cannot reliably notify
the user through the same channel. In that case the required behaviour is a
red GitHub Actions run with enough logs to identify the missed source and
items.

**Rationale:** users must be able to distinguish "there was no news" from
"the system failed to deliver news." Duplicate notifications are annoying,
but a silent green run that neither delivers data nor reports failure destroys
trust and creates support load.

### IV. Visibility Over Silence

A degraded item reaches the user as a **visible anomaly**, not a silent skip.
If the pipeline extracted a row but a field is unusable (empty URL, missing
image, broken trailer), the item is still emitted to Telegram with a clear
gap marker AND a WARNING log line.

Conversely, fatal extraction errors (zero rows, malformed source) MUST log
ERROR and the relevant run-script step MUST exit non-zero so GitHub Actions
surfaces the failure in the Actions UI.

The same rule applies after extraction: failed notification delivery,
summarization failures for channels that contained messages, and failed
technical alerts MUST NOT be collapsed into "no new items/messages."

Forbidden: `try: ... except Exception: pass`, swallowing parse errors with
default empty values, "fail open" branches that hide drift.

**Rationale:** silent degradation is the worst kind of bug — users assume
the system is working when it isn't, so nobody investigates. A visible gap
or a red CI badge is a forcing function for someone to look.

### V. Root Cause Before Fix

Before changing code in response to a bug or unexpected behaviour, the failure
mode MUST be reproduced and located. Instrumenting (extra logging, printing
inputs, narrowing the failing call) precedes the patch. No workarounds, shims,
retries, broader try/except, or `--no-verify` flags are accepted as fixes
when the underlying mechanism is not understood.

If the immediate fix proves too large for the current PR, the PR may ship a
**documented mitigation** (e.g. raise-and-skip with a linked issue) but the
mitigation itself must be a deliberate choice, not a guess.

**Rationale:** every shim that hides a root cause becomes load-bearing in
six months. Cheap-now is expensive-later.

### VI. Fail-Fast Configuration

`sources.json` and any future declarative config is validated at load time.
Errors that can be caught at startup MUST NOT surface mid-run: bad CSS
selectors, unresolved `{{macros}}`, non-positive limits, missing required
fields, mismatched sheet schemas.

`pipeline_config.validate_sources_config()` is the central gate. When a new
class of config error becomes possible, the validator MUST grow a check for
it in the same PR.

**Rationale:** every config-validation gap shows up at 04:00 UTC in cron
logs, days after the typo was introduced. Catching it on `python
pipeline_config.py` (or in ci_check) keeps the feedback loop human-scale.

## Development Workflow

The procedural workflow rules (branch creation, PR discipline, labels,
plan→implement flow, pre-commit gate, dependency consistency, architect-review
gate) are an **operational procedure**, delegated to their canonical home
[`.claude/rules/workflow.md`](../../.claude/rules/workflow.md) (an always-loaded
operational tier — see [`project-map.md`](project-map.md) IA-policy). They
supplement the principles above and are **equally binding**. This file does not
restate them — edit them there.

## Quality Gates

A PR MAY merge only when:

- All CI checks are green: `ci.yml` (format, lint, tests, mypy, pip-audit).
- The change has tests matching its behaviour (Principle I). New extraction
  logic gets an integration test against a saved HTML/JSON fixture; new
  config rules get a unit test; new pipeline orchestration gets a
  Protocol-doubles test.
- `docs/architecture/test-coverage.md` is updated when test structure
  changes (new file, new class, or category status flips).
- The `Claude code review` workflow has commented on the PR (status sticky
  comment present); a hard block on its verdict is not enforced, but
  unaddressed concerns must be answered in PR comments before merge.
- For PRs that touch HTML extraction or external API contracts, an E2E
  smoke test (real HTTP) has been run at least once on the branch — the
  daily cron run on `run-script.yml` counts.

## Governance

This document supersedes ad-hoc conventions in `CLAUDE.md` and the other
`docs/architecture/*` notes; where they conflict, this document wins.
`CLAUDE.md` and the other architecture docs remain as runtime guidance and
implementation-detail references — kept short, kept linked, never the source
of truth on principles.

**Delegation of operational procedures.** This constitution retains the
**principles §I–VI**, the **Quality Gates**, and this **Governance** section as
its canon. The *operational procedural rules* (the former §Development Workflow)
are delegated to [`.claude/rules/workflow.md`](../../.claude/rules/workflow.md) —
the always-loaded operational tier in Claude Code's knowledge-carrier hierarchy
(see [`project-map.md`](project-map.md)). Delegation does **not** weaken their
authority: those rules bind equally and `.claude/rules/workflow.md` is their
single source of truth (other mentions are links only). Amending them happens
in that file; amending the *delegation itself* (what is canon vs. delegated) is
a Governance change made here.

Amendments are made via PR that modifies this file. Version policy:

- **MAJOR** — a principle is removed, redefined, or a procedural rule is
  reversed (e.g. Test-First downgraded to optional). Requires explicit human
  approval in the PR description.
- **MINOR** — a new principle or section is added, or a principle's scope
  is materially expanded.
- **PATCH** — clarifications, wording improvements, typo fixes, references
  updated, no semantic shift in rules.

Every PR description states which principles the change interacts with. The
reviewer (human + Claude review action) checks that the change does not
violate them; if it does, the violation MUST be recorded in the PR body
with a justification.

**Version**: 2.2.0 | **Ratified**: 2026-05-17 | **Migrated**: 2026-05-21 | **Amended**: 2026-06-13
