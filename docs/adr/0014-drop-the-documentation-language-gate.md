---
status: "accepted"
date: 2026-10-04
decision-makers: ekolvah
---

# Keep English documentation without a language gate

## Context and Problem Statement

ADR-0005 made English the language of repository documentation and Python commentary and
enforced it with `scripts/check_language.py`, a `language` entry in the `ci_check.py` registry.
The migration it guarded is finished: its allow-list is empty. The agent-process plugin, which
now owns the process carriers (ADR-0013), declined to carry the check upstream
(ekolvah/agent-process-distribution#341), and #614 retires repository-owned copies of process
gates. Should this repository keep the gate locally, or keep the rule without it?

## Decision Drivers

* A repository-owned gate over a finished migration pays a CI job on every push while catching
  nothing observed since the allow-list emptied.
* ADR-0013 retires duplicated control-plane code that the plugin does not carry.
* A dropped proof must stay visible as an accepted gap, not disappear silently (§IV).

## Considered Options

* Keep `check_language.py` as a repository-owned gate
* Keep the English rule and drop the gate
* Drop both the rule and the gate

## Decision Outcome

Chosen option: "Keep the English rule and drop the gate", because the rule still serves the
reasons ADR-0005 gives (one vocabulary for wording guards, readable output on Windows), while the
gate's cost is now paid for a migration that no longer produces violations.

English remains the language of repository documentation and Python commentary, with ADR-0005's
boundary unchanged: string literals, operator diagnostics, prompts, fixtures and domain data are
outside it. Nothing enforces the rule automatically; the maintainer reviews it before merging.

### Consequences

* Good, because the registry loses a check, its script and its tests, and CI one job per push.
* Good, because the repository no longer duplicates a process gate the plugin chose not to own.
* Bad, because Cyrillic prose can re-enter Markdown or Python comments unnoticed until review,
  including the Russian severity wording in agent prompts that the gate used to keep out
  ([`coverage-gaps-quality-gates.md`](../architecture/coverage-gaps-quality-gates.md) entry W).
* Neutral, because the mapped-document question marker stays single-language through
  `tests/test_doc_headers.py`, which does not depend on the dropped gate.

### Confirmation

`ci_check.py --list-checks` no longer names `language`, and `scripts/check_language.py` is gone.
Compliance with the rule itself is confirmed only by maintainer review.

## More Information

Supersedes [ADR-0005](0005-english-repository-documentation.md). Reopen this decision when a merged
PR adds Cyrillic documentation prose: that is the observed recurrence the gate would have caught.
