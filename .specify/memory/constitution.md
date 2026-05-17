<!--
Sync Impact Report
==================
Version change: TEMPLATE → 1.0.0  (initial ratification)

Modified principles (all replaced from template placeholders):
  [PRINCIPLE_1_NAME] → I. Test-First (NON-NEGOTIABLE)
  [PRINCIPLE_2_NAME] → II. Protocol Boundaries with Dependency Injection
  [PRINCIPLE_3_NAME] → III. Write-Before-Notify Ordering
  [PRINCIPLE_4_NAME] → IV. Visibility Over Silence
  [PRINCIPLE_5_NAME] → V. Root Cause Before Fix
  (new)              VI. Fail-Fast Configuration

Added sections:
  Core Principles (6 declarative laws derived from CLAUDE.md + docs/architecture/*)
  Development Workflow (issue→branch→PR rules, ci_check gate, spec-driven flow)
  Quality Gates (no-mocks rule, write-before-notify proof, E2E smoke before merge)
  Governance (amendment & version policy)

Removed sections: none (template placeholders were never authored content).

Templates requiring updates:
  ✅ .specify/templates/tasks-template.md — "Tests are OPTIONAL" wording flipped to mandatory per Principle I (same PR)
  ⚠ .specify/templates/plan-template.md — "Constitution Check" gates left as `[Gates determined based on constitution file]`; /speckit-plan resolves at runtime, no static edit needed
  ⚠ .specify/templates/spec-template.md — no constitution-driven changes needed; spec format is orthogonal to our principles
  ⚠ CLAUDE.md / docs/architecture/* — kept as runtime guidance docs that reference this constitution; not duplicated here

Follow-up TODOs: none.
-->

# kinozal_scraper Constitution

## Core Principles

### I. Test-First (NON-NEGOTIABLE)

For every behavioural change a failing test exists **before** the implementation
commit. The Spec Kit workflow enforces this: `/speckit-tasks` lists test tasks
above implementation tasks, and `/speckit-implement` executes them in order so
the test goes red, then green.

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

### III. Write-Before-Notify Ordering

Persisting an item to storage MUST complete successfully before any Telegram
notification is sent for that item. `storage.append_rows()` then
`notifier.send_items()` — never the reverse, never concurrent.

If notification fails after storage succeeds, the item is deduped on the next
run and the user misses one Telegram message. If storage fails after
notification succeeds, the user receives the same message every day forever.
The first failure mode is recoverable noise; the second is a spam loop.

**Rationale:** duplicate notifications are worse than skipped ones; users
mute the bot, the channel becomes useless. Ordering is the entire guarantee.

### IV. Visibility Over Silence

A degraded item reaches the user as a **visible anomaly**, not a silent skip.
If the pipeline extracted a row but a field is unusable (empty URL, missing
image, broken trailer), the item is still emitted to Telegram with a clear
gap marker AND a WARNING log line.

Conversely, fatal extraction errors (zero rows, malformed source) MUST log
ERROR and the relevant run-script step MUST exit non-zero so GitHub Actions
surfaces the failure in the Actions UI.

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

These procedural rules supplement the principles above and are equally
binding:

1. **Branch creation** — new work happens on `codex-issue-N-<slug>` branches
   created **only** via `python scripts/new_branch.py <name>`. Direct
   `git checkout -b` is forbidden; the script guarantees the branch starts
   at `origin/main` HEAD, preventing squash-merge divergence (issue #66).
2. **No direct main pushes** — main is updated exclusively through PRs.
3. **No self-merge** — `gh pr merge` is forbidden for the AI assistant
   without explicit per-PR human approval. The human reviewer keeps the
   merge button.
4. **One PR, one logical unit** — docs-only PRs are separate from
   refactor/feature PRs (precedent: #39 → #40 had to be redone after mixing).
5. **Issues carry labels** — every `gh issue create` includes `--label
   bug|enhancement|documentation|testing|...`.
6. **Pre-commit gate** — `python scripts/ci_check.py` runs ruff format +
   lint + pytest + mypy + lockfile drift. The `.githooks/pre-push` hook runs
   it automatically; do not bypass with `--no-verify`.
7. **Dependency consistency** — when a `requirements*.in` changes,
   `pip-compile` regenerates the corresponding `.txt` in the same commit.
8. **Spec-driven flow** — substantive new features and bug fixes are
   authored via `/speckit-specify` → (`/speckit-clarify`) → `/speckit-plan` →
   (`/speckit-analyze`) → `/speckit-tasks` → `/speckit-implement`. The
   spec file lives at `.specify/specs/NNN-<slug>/spec.md` and is committed
   alongside the code. Trivial fixes (typos, single-line non-behavioural
   tweaks) may skip the workflow.

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

This constitution supersedes ad-hoc conventions in `CLAUDE.md` and the
`docs/architecture/*` notes; where they conflict, this document wins.
`CLAUDE.md` and `docs/architecture/*` remain as runtime guidance and
implementation-detail references — kept short, kept linked, never the source
of truth on principles.

**Amendments** are made via PR that modifies this file, runs
`/speckit-constitution` to regenerate the Sync Impact Report, and bumps the
version:

- **MAJOR** — a principle is removed, redefined, or a procedural rule is
  reversed (e.g. Test-First downgraded to optional). Requires explicit human
  approval in the PR description.
- **MINOR** — a new principle or section is added, or a principle's scope
  is materially expanded.
- **PATCH** — clarifications, wording improvements, typo fixes, references
  updated, no semantic shift in rules.

**Compliance review** — every PR description states which principles the
change interacts with. The reviewer (human + Claude review action) checks
that the change does not violate them; if it does, the violation MUST be
recorded in the plan's "Complexity Tracking" section with a justification.

**Version**: 1.0.0 | **Ratified**: 2026-05-17 | **Last Amended**: 2026-05-17
