---
paths:
  - "tests/**"
---

# Testing — operational checklist

**Question this document answers:** which steps are mandatory when writing or
changing tests. It is an **operational checklist**, not a design document:
principle wording is canonical in
[`principles.md §I`](../../docs/architecture/principles.md) (Test-First) and
[`§II`](../../docs/architecture/principles.md) (Protocol Boundaries + DI).
**Do not paraphrase a principle here—link to it only** (path-scoped: loaded
only when working with `tests/**`).

1. **RED first** — write the failing test from the issue `## Test plan` before
   code (rule and exceptions:
   [`principles.md §I`](../../docs/architecture/principles.md)).
   `scripts/check_red.py` takes the result per test from the junit report. A
   suite that did not run does not count as RED; a test for a symbol that does
   not exist yet needs a signature stub so its failure occurs in the test body.
   The contract is
   [`agent-process.md`](../../docs/architecture/agent-process.md).
2. **No mocks of internal logic** —
   [`principles.md §II`](../../docs/architecture/principles.md) applies.
3. Choose the test level from the target project's testing strategy.
4. **Run** `python -m pytest` incrementally; before commit run
   `python scripts/ci_check.py`.
5. Record a consciously rejected coverage decision in the target project's
   accepted-gaps ledger, so it is not reopened as work-for-work.
