# Consciously-accepted coverage gaps

**Question this document answers:** Where to find each consciously accepted coverage-gap record.

This is the stable-ID router for decisions not to add a test. Test strategy and taxonomy remain
in [testing.md](testing.md); each focused ledger below preserves the accepted record text and its
letter ID. Records carry stable letter IDs (`A` through `AU`) so a state document links to a
decision without retelling its rationale.

Every category in the [testing taxonomy](testing.md#bug-taxonomy) has test coverage today. The
ledger records only consciously rejected coverage, and test navigation remains a repository search
by module or feature rather than a hand-maintained category index.

**What belongs here:** a decision that a test does not cover a behavior. The linked records make
negative-ROI decisions visible, so they are not silently reopened as work-for-work.

**Rejected as negative-ROI (a test would only ever guard CI minutes, not correctness):**

- [Ingestion and retrieval](coverage-gaps-ingestion.md) — `A`, `C`, `K`, `L`, `M`, `M2`, `M3`.
- [Enrichment and selection](coverage-gaps-enrichment.md) — `N` through `U`.
- [Quality gates](coverage-gaps-quality-gates.md) — `V`, `X` through `AD`, `AR`, and `AT`. `W` is
  retired with the last local agent prompt it guarded (#626); do not reuse it.
- [Runtime behavior](coverage-gaps-runtime.md) — `AE` through `AI`.
- [Agent tooling and observability](coverage-gaps-agent-tooling.md) — `AJ`, `AN`, `AS`, `AU`, and `J`. `AK`, `AL`, `AO`, `AP`, and `AQ` are retired with the
  machinery they described (#600), and `AM` with its subject's move into the agent-process
  plugin (#612); do not reuse them.
- [Modules without dedicated tests](coverage-gaps-modules.md).
