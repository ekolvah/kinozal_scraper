---
paths:
  - "tests/**"
---

# Testing — operational checklist

**Question this document answers:** which steps are mandatory when writing or changing tests.
It is an **operational checklist**, not a design document: principle wording is canonical in
[`principles.md §I`](../../docs/architecture/principles.md) (Test-First) and
[`§II`](../../docs/architecture/principles.md) (Protocol Boundaries + DI); strategy
(levels, bug taxonomy, and what to mock) is in [`docs/architecture/testing.md`](../../docs/architecture/testing.md).
**Do not paraphrase a principle here—link to it only** (path-scoped: loaded only when working with `tests/**`).

1. **RED first** — write the failing test from the issue `## Test plan` before code
   (rule and exceptions: [`principles.md §I`](../../docs/architecture/principles.md)).
   `scripts/check_red.py` takes the result **per test** from the junit report, so `unittest.subTest`
   does not break it and a parameterized test need not be split for the gate (#400). One
   RED commit shape requirement remains: a suite that **did not run** (failed to collect or failed
   in a fixture) does not count as RED—it proves only that the file cannot be imported (#402). Therefore,
   a test for a symbol that does not exist yet includes a **signature stub** (`raise NotImplementedError`),
   so the failure occurs in the test body; the contract is [`agent-process.md`](../../docs/architecture/agent-process.md).
   First ask **whether the test should be written at all**: does a regression break correctness/security
   (→ test), or only consume CI-minute/token resources (→ forcing function, not guard test)? The canonical rule
   is in [`testing.md`](../../docs/architecture/testing.md#rule-when-a-test-is-not-worth-writing).
2. **No mocks of internal logic** — [`principles.md §II`](../../docs/architecture/principles.md) applies;
   the repository-specific form (external boundaries and pattern) is in [`testing.md`](../../docs/architecture/testing.md#rule-no-mocks-of-internal-functions).
3. Choose the **test level** using the [bug taxonomy](../../docs/architecture/testing.md#bug-taxonomy)
   (integration-first → unit for pure functions → e2e smoke before merge for structure drift).
4. **Run** `python -m pytest` incrementally; before commit run `python scripts/ci_check.py`.
5. If you **consciously reject coverage** (a new scope/cost skip, live E2E judged negative ROI,
   parallel mode non-target), record the decision in the
   [`coverage-gaps.md`](../../docs/architecture/coverage-gaps.md) ledger,
   so it is not reopened as work-for-work. There is no inventory of “which test catches which bug”—
   navigate tests with `grep` by module, not a manual table.
