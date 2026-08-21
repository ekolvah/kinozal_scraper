---
status: "accepted"
date: 2026-08-09
decision-makers: ekolvah
---

# Repository documentation and Python commentary use English

## Context and Problem Statement

The repository accumulated two documentation languages. Guards that compare
canonical wording with provider-specific adapters consequently covered only the
language they happened to encode, and every new presence or deny-list check had
to duplicate Russian and English markers. Cyrillic diagnostics also repeatedly
crossed Windows ANSI boundaries and became unreadable or disappeared.

The repository still serves Russian-language users and processes Russian source
data. The decision therefore needs a precise boundary: which text is maintained
as repository documentation, and which text remains domain data or user-facing
product output?

## Decision Drivers

* Anti-drift checks must compare one vocabulary rather than silently cover only
  half of a bilingual contract.
* Documentation failures must remain readable on Windows and in CI logs.
* The policy must be executable over tracked files and fail visibly if its scope
  cannot be established.
* Russian runtime messages, prompts, fixtures, and source data must retain their
  product meaning.
* The migration must preserve accepted architectural decisions and their Git
  history rather than reinterpret them.

## Considered Options

* Keep bilingual documentation and maintain bilingual guards
* Use English for repository documentation and Python commentary
* Use Russian for all repository documentation and Python commentary
* Allow each file to choose its own language while adding no language gate

## Decision Outcome

Choose **English for repository documentation and Python commentary**.
All prose in tracked Markdown files and all comments plus true module, class,
and function docstrings in tracked Python files use English. Markdown code spans
and fenced blocks, Python string literals, Telegram and log messages, Gemini
prompts, fixtures, scraped titles, and other domain data are outside this
documentation-language boundary. So are comments in non-Markdown, non-Python
carriers — workflow YAML, `.githooks/`, `.gitattributes`: the gate sees `.md`
and `.py`, and extending it to a third syntax is a separate unit of work, not a
silent part of this one. Naming the exclusion here is the point; an unstated
scope is what made the bilingual cost invisible in the first place.

Operator-facing diagnostics in `scripts/` are Python string literals and so stay
Russian under this boundary. This decision therefore does **not** remove the
Windows ANSI failure source from them — it removes it from documentation and
commentary only. `check_language.py` guards its own output with `_console_text`;
the other scripts have no such escape hatch, and nothing stops a new Russian
`print()`. Extending the boundary to diagnostic output is a candidate follow-up,
deliberately not decided here.

The executable gate derives its Markdown and Python scope from
`git ls-files -z`, verifies that every expected repository area contributes
files, and distinguishes policy violations from unavailable evidence. The
target project documents its operational rule, scope, and CI mechanics in its
own architecture documentation.

ADR-0001 through ADR-0004 are translated once as a representation-only part of
this migration. Their statuses, decisions, alternatives, and consequences do
not change. This is an explicit exception to the normal rule that accepted ADR
text changes only for typo and broken-link repairs; the original text remains
available in Git history.

### Consequences

* Good, because wording-based guards cover one canonical vocabulary.
* Good, because future documentation and commentary avoids the recurring
  Windows ANSI failure boundary; operator-facing diagnostics do not, because
  they are string literals outside the boundary.
* Good, because a new tracked documentation file joins the policy without a
  manually maintained path list.
* Bad, because the one-time migration produces a large review diff and a
  translation can accidentally change nuance or break a heading anchor.
* Neutral, because the product remains Russian where its users and source data
  require Russian; this decision changes repository-maintained explanation,
  not runtime language.

### Confirmation

`scripts/check_language.py` rejects Cyrillic Markdown prose and Cyrillic Python
comments/docstrings, while its tests pin the code/data exclusions, tracked-file
scope, non-empty expected areas, and evidence-failure behavior. Existing
document-link, header, narrative, ADR-structure, and agent-adapter guards detect
navigation or contract damage introduced by the migration. The final migration
allow-list is empty.

## Pros and Cons of the Options

### Keep bilingual documentation and maintain bilingual guards

* Good, because existing text would require no migration.
* Bad, because every wording-based guard would retain two vocabularies and a
  missed translation would remain indistinguishable from full coverage.
* Bad, because Cyrillic documentation and commentary would continue crossing
  fragile Windows encoding boundaries.

### Use English for repository documentation and Python commentary

* Good, because development tools, upstream documentation, identifiers, and CI
  terminology already use English.
* Good, because one vocabulary makes deterministic anti-drift checks complete.
* Bad, because accepted documentation requires a one-time, review-heavy
  representation migration.

### Use Russian for all repository documentation and Python commentary

* Good, because it matches the primary product language.
* Bad, because upstream technical vocabulary and provider-neutral contracts are
  English and would need translation or bilingual markers.
* Bad, because it preserves the Windows encoding failure source across the whole
  documentation surface instead of only the diagnostics left outside the boundary.

### Allow each file to choose its own language without a gate

* Good, because authors would pay no immediate migration cost.
* Bad, because it preserves the measured guard gaps and makes future drift
  invisible.
