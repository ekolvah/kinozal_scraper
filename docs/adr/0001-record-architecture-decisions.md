---
status: "accepted"
date: 2026-07-30
decision-makers: ekolvah
---

# Decision rationales live in MADR records in `docs/adr/`

## Context and Problem Statement

The ["What the documentation describes" rule](../architecture/project-map.md) prohibited narrative in
`docs/`: it describes the current implemented state, while change history lives in git/PRs.
The rule did not provide a home for rationales, instead sending them to issue/PR bodies outside the repository.
The need remained: without a statement of why a decision is still correct, the rule looks ritualistic and
the next “simplifier” will remove it. Narrative returned to state documentation.

Measurement at `a49f75a` across `docs/` + `.claude/`: 300 `#N` references in 14 files, of which **174**
appear outside a parenthetical pointer—meaning the task number is part of a sentence rather than a link;
12 section headings are named after the tasks that produced them.

The cause is mechanical, not disciplinary. `#N` addresses an **event** in an external tracker: it has
no body in the repository and no status. A reader cannot learn the content or verify that the decision
still applies, so the author retells it beside the link (“#412 added a live case…”, “as before #227”,
“#359 broke the upper half”). A stable ID for a record in the repository removes the need to retell it:
the link is sufficient.

Decision question: **where in the repository should a rationale live so a state document can link to it
rather than retell it?**

## Decision Drivers

* Minimize future bug fixing and support: a rejected decision must not be reopened as work-for-work, and
  an active one must not be removed through ignorance of its rationale.
* Tokens: rationales must not enter always-load context and must not be duplicated.
* Do not invent a format or add a dependency where a standard exists.
* The mechanism must have an **admission filter**, otherwise the new directory becomes a dump for the same narrative.

## Considered Options

* Keep the current approach—rationales in issue/PR bodies and the rule in prose
* A custom record format in the repository (such as the historically evolved `A`…`AB` ledger in `testing.md`)
* MADR 4.0.0 + a CLI tool (`pyadr` / `adr-tools-python` / `log4brains`)
* **MADR 4.0.0 without tooling**—create records by copying the template

## Decision Outcome

Chosen: **MADR 4.0.0 without tooling**. The format is the de facto standard under the ADR organization;
by design it needs no tooling (“records are created by copying the template”), and here an agent creates one
inside `/implement` with a single `Write`, not a command series that CLI wrappers exist to save. The template
is adjacent ([`template.md`](template.md)) and copied verbatim from upstream tag `4.0.0`: `main` has since
diverged from the tag, so the pin is used.

### Consequences

* Good, because the rationale gains an address with a body and status—a state document links rather than
  retells it, removing the mechanical cause of recurrence.
* Good, because a changed decision is expressed as a **new** record with status `superseded by` and a
  forward link: the old rationale remains readable rather than being rewritten after the fact.
* Good, because it adds no dependency: neither CLI nor Node linter.
* Bad, because it adds a third home for decisions beside two existing ledgers—the admission rule below
  offsets that cost, but requires review discipline.
* Bad, because an unmaintained directory becomes archaeology with false authority. The answer is a narrow
  admission filter, not scope expansion.

### Confirmation

`tests/test_adr_records.py` enforces structural properties: the `NNNN-slug.md` name, unique number,
status from a closed set, `superseded by` resolving to an existing record, and required MADR minimal
sections. The guard neither judges nor can judge semantics—whether a decision warrants a record and a
rationale remains current. That stays with a human reviewer (the same honest boundary as a header-presence guard).

## Pros and Cons of the Options

### Keep the current approach

* Good, because the implementation cost is zero.
* Bad, because that exact approach was measured not to work: 174 narrative references.

### Custom format

* Good, because it can fit the repository exactly.
* Bad, because it is the existing bespoke solution: a ledger `A`…`AB` with its own IDs, fields, and
  **no status policy**—records are edited in place rather than superseded with a new one.

### MADR + CLI tool

* Good, because numbering and statuses are automated.
* Bad, because there are no viable candidates: `pyadr`—0.19.0 (April 2022), *Pre-Alpha* on PyPI;
  `adr-tools-python`—1.0.3 (June 2019); `log4brains`—Node.js, whose author declared low maintenance.
* Bad, because the tool solves a problem absent here: it saves a **human** a series of commands.

## More Information

**The active directory policy—the “where a decision goes” route, closed status set, immutability of an
accepted record, and size guidance—lives in
[`project-map.md` §Canonical-home](../architecture/project-map.md), not here.** This is not a formal caveat:
policy changes while an accepted record is not rewritten, and putting a live rule in an append-only file would
mean recording every revision as `superseded by`, with four documents linking to the superseded record. This
record retains what should be immutable: **why** tooling-free MADR was selected and what was rejected.

The chronicle of “how we got here” belongs in the issue/PR body; it is intentionally absent from this record.
