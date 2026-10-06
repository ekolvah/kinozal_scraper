## Why

Epic #616 (step 3) split the #612 audit into four PRs; this is **B** (#626): the local subagents
and the discovery layer. A (#625) is merged as #629/#630; C and D are #627 and #628.

Observations behind B (main @ `eef253d`, plugin 3.10.0):

- **The plugin will not carry a discovery role.** `gh issue view 307 -R
  ekolvah/agent-process-distribution` → `"state":"CLOSED","stateReason":"NOT_PLANNED"`, title "No
  discovery step: /opsx:propose has no pre-design extension point for live observation of
  external systems". Epic #616 lists the discovery layer for deletion without a replacement.
- **The model-pin policy has no subject left.** `gh issue view 317 -R
  ekolvah/agent-process-distribution` → `"stateReason":"COMPLETED"`, "subagents inherit the session
  model and effort instead of pinning". `.claude/agents/discovery.md` is the only local agent
  (Glob `.claude/agents/**`), so with it gone `ci-agent-review.md` §Model pinning and its guard
  `tests/test_agent_frontmatter.py` describe and test nothing. The rest of that document (caller,
  secret setup) is the plugin's managed workflow and its Install step.
- **The live-observation duty itself stays.** `principles.md` §V requires observing the live
  system when a design reads or classifies external data; that text is C's scope and is not
  removed here. What goes is the repository's carrier, trigger and record format for it.
- **The capture scripts are product tooling (#613), so their table moves, not goes.**
  `scripts/capture_*_fixture.py` and `check_fixture_ratchet.py` stay; the source → command table
  and its safety notes move from `agent-process.md` §Evidence block to `testing.md`.

## What Changes

- **Delete** `.claude/agents/discovery.md` and the discovery trigger in `.claude/rules/workflow.md`.
- **Delete** §Evidence block and §Discovery runbook from `docs/architecture/agent-process.md`;
  move the capture-route table, its route-safety notes and the fixture-placement rule into
  `docs/architecture/testing.md` under a new `### External-data capture routes`.
- **Delete** `docs/architecture/ci-agent-review.md` and `tests/test_agent_frontmatter.py`.
- **Repoint or trim** every inbound reference (design D4); fix the two broken anchor links in
  superseded ADR-0009 with permalinks to the last `agent-process.md` that had the sections.
- **Ledger:** retire `W` (no local agent prompt left to guard); rewrite `AR` to the remaining gap —
  §V's live observation has no carrier and no gate.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. `skip_specs: true`: the change deletes process tooling and edits documentation; no product
or spec-level behaviour changes (`openspec/specs/` has no requirement naming discovery or the
Evidence block).

## Impact

Removed:

- `.claude/agents/discovery.md`
- `docs/architecture/ci-agent-review.md`
- `tests/test_agent_frontmatter.py`

Edited:

- `.claude/rules/workflow.md` — discovery paragraph removed.
- `.claude/rules/mindset.md` — "(Evidence, discovery, governance)" → "(governance)".
- `CLAUDE.md` — §PR Workflow parenthetical trimmed likewise.
- `docs/architecture/agent-process.md` — §Evidence block and §Discovery runbook removed; intro
  names governance only.
- `docs/architecture/testing.md` — new `### External-data capture routes`; the ratchet bullet
  points there and to the proposal instead of `## Evidence`.
- `docs/architecture/principles.md` — §V link to the routing table and the governance pointer
  repointed (two lines; the rest of `principles.md` is C's).
- `docs/architecture/project-map.md` — rows for `discovery.md` and `ci-agent-review.md` removed;
  `agent-process.md`, `workflow.md`, `evidence/` and capture-script rows reworded.
- `docs/architecture/ci.md` — router line to `ci-agent-review.md` removed.
- `docs/architecture/information-architecture.md` — the two `.claude/agents/` clauses name
  `.claude/commands/` only.
- `docs/architecture/coverage-gaps-quality-gates.md` — `W` removed; `AR` rewritten.
- `docs/architecture/coverage-gaps.md` — router: quality gates `V`, `X` through `AD`, and `AR`;
  `W` listed as retired.
- `docs/adr/0009-discovery-is-a-separate-role-chained-inside-the-planner-run.md` — two anchor
  links → permalinks (broken-link fix, allowed by the ADR policy).
- `tests/test_doc_headers.py` — `ci-agent-review.md` out of the read-budget list; docstring
  references to `test_agent_frontmatter.py` / `.claude/agents/` removed.
- `tests/test_adr_records.py` — docstring reference to `test_agent_frontmatter.py` removed.
- `requirements-dev.in` — PyYAML comment no longer names `test_agent_frontmatter.py`
  (`pip-compile` run in the same commit; PyYAML stays for the other two readers).

Outside the repository: none. #626 already exists and is a sub-issue of #612.
