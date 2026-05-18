# Feature Specification: GitHub Trending source for late-bloomer repositories

**Feature Branch**: `codex-issue-60-github-trending`

**Created**: 2026-05-18

**Status**: Draft

**Input**: User description: "Add a second GitHub source — github.com/trending?since=daily — so the morning digest also catches repositories that go viral today regardless of their age. The existing GitHub source only looks at repos younger than 30 days that crossed 1000 stars, which misses 'late-bloomer' projects (real example: ruvnet/RuView, created June 2025, sat at <300 stars for 6 months, then jumped to 1000+ stars on December 25 2025 and never appeared in the digest)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Catch a late-bloomer repository on the day it goes viral (Priority: P1)

As the digest reader, when an older repository suddenly trends on GitHub today, I want to see it in tomorrow morning's Telegram digest, so that I don't miss viral projects just because they are not "new".

**Why this priority**: This is the entire motivation for the feature. Without it, the late-bloomer class of viral repos stays invisible — which is the documented gap (`ruvnet/RuView` and similar). Delivering only this story already removes the gap.

**Independent Test**: On any day where the daily GitHub trending page contains at least one repository older than 30 days, the next morning's digest run includes at least one such repository in the Telegram output, with a working URL and a non-empty title.

**Acceptance Scenarios**:

1. **Given** the daily trending page lists a repository created 200 days ago, **When** the scheduled digest run completes, **Then** that repository appears in the user's Telegram channel with its title, URL, and a star indicator.
2. **Given** the daily trending page lists a repository that has fewer than 1000 total stars, **When** the scheduled digest run completes, **Then** that repository still appears (the new source does not apply the 1000-star floor that the existing GitHub source uses).
3. **Given** the trending page contains the maximum number of listed repositories the source is configured to read, **When** the run completes, **Then** the digest does not exceed that configured maximum for this source on a single day.

---

### User Story 2 - Don't spam the same repository on consecutive trending days (Priority: P2)

As the digest reader, when a repository stays on the daily trending page for several days in a row, I want to receive the notification only once, so the channel does not turn into a duplicate stream.

**Why this priority**: P1 covers the value; P2 protects the value from immediately degrading the channel. Without dedupe, a sticky viral repo would generate one identical notification per day for a week.

**Independent Test**: Run the digest two days in a row against a stable trending page (same repository visible both days). The second run produces no Telegram message for that repository.

**Acceptance Scenarios**:

1. **Given** repository X was notified from the trending source yesterday, **When** today's run sees X on the trending page again, **Then** no notification is sent for X and X is not re-recorded in the dedupe store.
2. **Given** repository X was notified from the *other* GitHub source (the existing "new and popular" one) on any previous day, **When** today's run sees X on the trending page, **Then** **no** notification is sent for X — the two GitHub sources share a single dedupe scope.
3. **Given** repository X is visible **today** in both the existing GitHub source and the new trending source within the same run, **When** the run executes, **Then** exactly one notification is sent for X, formatted by whichever of the two sources runs first in the configured source order.
4. **Given** the dedupe store is empty, **When** the first run executes, **Then** every visible repository from both GitHub sources is recorded and notified exactly once.

---

### User Story 3 - Visible failure when GitHub changes the trending page layout (Priority: P3)

As the digest operator, when GitHub changes the HTML structure of the trending page so that the extractor reads zero rows, I want the failure to be loud (Telegram anomaly message or a red CI run), so that I can fix the selectors before the gap stretches into weeks.

**Why this priority**: Layout drift is a near-certain event over the lifetime of the source. The first two stories already produce value; this one keeps that value from quietly evaporating. It is the constitutional *Visibility Over Silence* principle applied to this source.

**Independent Test**: Synthetically point the source at a URL whose response does not contain the expected structure. The run either delivers a visible anomaly message to the operator's Telegram channel or exits non-zero in the scheduled CI, so the GitHub Actions dashboard turns red.

**Acceptance Scenarios**:

1. **Given** the trending page response no longer contains any rows matching the configured layout, **When** the run executes, **Then** an error is logged AND the run-script step exits non-zero so the Actions dashboard surfaces the failure.
2. **Given** a single row in the trending page is missing one expected field (e.g. star count), **When** the run executes, **Then** the repository is still emitted to Telegram with a clear gap marker for that field and a WARNING log line — it is not silently dropped.
3. **Given** the source configuration is malformed at startup (missing required field, unresolvable macro), **When** the program loads configuration, **Then** it exits with an error before any HTTP call — the failure does not happen mid-run.

---

### Edge Cases

- **Layout drift**: GitHub changes the trending page HTML so existing selectors miss rows → covered by US3 #1 (visible failure).
- **Partial row**: A trending entry has no description or no star indicator → covered by US3 #2 (emit with gap marker, do not skip).
- **Network failure / 5xx from github.com**: Treated as a transient extraction error — same handling as other HTTP-based sources in this project (logged, non-zero exit, retried next scheduled run).
- **Empty trending page** (unlikely but possible during a GitHub incident): Logged as a warning, no notifications sent, run completes green; not treated as a failure.
- **Cross-source overlap**: A repository visible on both the existing GitHub source and the new trending source → exactly one notification is sent across both sources, formatted by whichever source runs first in the configured source order (see US2 #2 and US2 #3, FR-005, FR-005a).
- **Dedupe store wraparound**: The trending source's dedupe store grows unboundedly over time → out of scope for v1; same retention behaviour as other sources in this project.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST read the daily GitHub trending listing (the "today" window) at each scheduled run.
- **FR-002**: For each listed repository, the system MUST extract the repository's display name (owner/name), its URL, and a star indicator representing the current day's star activity.
- **FR-003**: The system MUST notify the user via the existing Telegram channel of each repository that has not been previously notified from this source.
- **FR-004**: The system MUST persist each notified repository to a dedupe store **before** sending the notification (write-before-notify ordering, per Constitution Principle III).
- **FR-005**: The dedupe store for the trending source MUST be **shared** with the existing GitHub "new and popular" source, so any given repository produces at most one notification across both GitHub sources over the lifetime of the dedupe store.
- **FR-005a**: When a repository is visible in **both** GitHub sources within the same run, the system MUST emit exactly one notification, formatted by whichever of the two sources is processed first in the configured source order.
- **FR-006**: The source configuration MUST be validated at program startup; an invalid configuration (missing required field, unresolvable macro, non-positive limit) MUST prevent the run from starting (per Constitution Principle VI).
- **FR-007**: Extraction failures that leave the source unable to read any rows MUST be surfaced visibly: either via a Telegram anomaly message or a non-zero exit code that turns the scheduled CI run red (per Constitution Principle IV). The system MUST NOT silently produce a zero-row digest entry for this source.
- **FR-008**: For a partial row (some fields missing), the system MUST still emit the repository to Telegram with a clear gap marker on the missing field and a WARNING log line — it MUST NOT silently drop the row (per Constitution Principle IV).
- **FR-009**: The notification text for this source MUST be in Russian and follow the visual style of notifications from the existing GitHub source so the channel reads consistently.
- **FR-010**: The system MUST cap the number of repositories pulled per run for this source at a configured maximum (default: the full daily trending page, currently 25 entries).

### Key Entities *(include if feature involves data)*

- **Trending repository entry**: A repository appearing on the daily trending page. Attributes used by the feature: display name (owner/name), URL, and a current-day star indicator. The system does not track the repository's age or total star count for this source — that is the whole point of the source.
- **Notification record**: A row persisted to the shared GitHub dedupe store containing at minimum the repository URL (or a derived dedupe key), an identifier of which GitHub source first observed it, and the notification timestamp. The dedupe key is what makes the scope **shared** across both GitHub sources; the source identifier is informational only.
- **Source configuration entry**: A declarative entry that describes how to read and parse this source. Subject to startup-time validation (FR-006); the concrete schema is shared with other declarative sources in the project.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within the first 14 days of running this source, the digest delivers at least three repositories whose creation date is more than 60 days before the day they were notified — i.e. the source proves it catches "late bloomers" that the existing GitHub source structurally cannot.
- **SC-002**: Across any 14-day window, no repository URL appears in the Telegram channel more than once **across both GitHub sources combined** — the shared dedupe scope holds.
- **SC-003**: Any extraction-level failure of the source becomes visible to the operator within one scheduled run — measured by: a failing run either posts an anomaly message to the Telegram channel OR shows as a red workflow on the GitHub Actions dashboard within 24 hours of the failure starting.
- **SC-004**: On a day with no GitHub-side incident, the source delivers at least 5 distinct repository notifications. (Lower bound; the trending page typically shows ~25.)
- **SC-005**: A configuration-only typo (e.g. a removed required field) causes the program to exit before any HTTP request is issued, verified by a startup-time validation error log entry.

## Assumptions

- **Brownfield reuse**: The feature is implemented as a new declarative source entry inside the project's existing source-configuration mechanism. No new orchestrator, no new pipeline branch, no new external dependency. (The constitution's Protocol Boundaries principle applies — the new source reuses the existing `Storage`/`Notifier` boundaries.)
- **Dedupe storage**: Both GitHub sources (existing "new and popular" + new trending) share a single logical dedupe scope inside the same storage backend (Google Sheets) already used by other sources. Concretely this means one shared sheet tab is read and written by both sources. No new storage system is introduced, but the existing GitHub source's dedupe-scope identifier may need to be widened to cover both — that re-identification is captured at the plan stage.
- **Schedule**: The source is read by the same daily 04:00 UTC GitHub Actions cron that drives the other sources. No separate workflow.
- **HTML extraction**: The page is served as standard HTML and is parseable without a headless browser. (To be verified in implementation; the implementation plan must include a live HTML check before fixing selectors.)
- **Selector verification**: The CSS selectors and field transforms suggested in the source issue (`article.Box-row`, `h2 a` etc.) are a starting hypothesis only. They MUST be re-verified against the live page during the planning/implementation phase; if GitHub has changed the layout, the actual selectors take precedence.
- **Cross-source policy**: A repository qualifying for both the existing GitHub source and the new trending source produces exactly **one** notification across both sources (shared dedupe — FR-005). When the overlap is intra-run, the source running first in the configured source order wins the format (FR-005a). This is a clarified product decision from the spec author after the initial draft.
- **Stars metric**: The "star indicator" shown in the trending page row (today's star delta) is what the notification surfaces; the system does NOT separately query the API for the repository's total star count.
- **Operator-visible failure channel**: The "anomaly message to Telegram" mentioned in FR-007 and SC-003 reuses whatever visibility mechanism the project already exposes for other sources; if no such mechanism exists yet, the red CI run is the sole visibility channel for v1.
- **Branch creation pre-condition**: This specification was authored on a branch created via the project-mandated `python scripts/new_branch.py codex-issue-60-github-trending`, not via the Spec Kit `before_specify` git hook — the project's branch script enforces the additional constraint of starting from a fresh `origin/main` (constitution rule 1) which the generic hook does not.
