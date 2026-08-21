---
status: "accepted"
date: 2026-08-15
decision-makers: ekolvah
---

# Discovery is a separate role, chained inside the planner run

## Context and Problem Statement

A `bug` issue must carry a completed observation of the external system before its plan
may be written: the `## Evidence` block with `capture:` / `path:` / `observed:` / `preserve:` /
`change:` / `boundaries:` / `collateral:` / `reuse:` / `paired-test:`, backed by a captured fixture.

No role produced it as a named carrier. The planner runbook did tell the planner to observe the live
system, and both planner adapters restated the duty — but `.agents/orchestration/roles.yaml` gives
`planner` the authority "May edit the issue body; may not create an implementation branch or change
production code", which permits neither running a capture route nor writing a fixture file into the
working tree. And the section carried no provenance line, so a real capture, an ad-hoc chat
observation, and no observation at all were indistinguishable in the issue body.

So the process mandated an artifact whose carrier was unnamed and whose authority was contradicted —
the same class of invisibility as self-review being indistinguishable from independent review,
or a role being covered by one provider and not another.

The architect review pushed back on the answer, not the diagnosis: the two concrete defects
are ~15 lines apart, and neither needs a new role. That objection is what this record exists to
answer.

## Decision Drivers

* **Authority must describe what the carrier actually does.** A role that runs capture scripts and
  writes a fixture has a wider authority than one that edits an issue body; merging them hands every
  planner run the wider one.
* **The artifact must be attributable.** The value of a provenance line is that a claim has an owner,
  and that owner must resolve against a carrier set — otherwise the line is decoration.
* **No extra human session per bug.** Most bugs end in one `n/a: <reason>` line. A separate
  human-launched entry point would charge a full session for that line.
* **The catalogue is the control plane's input.** `agent_orchestrator.py` reads roles from the
  catalogue; a duty that exists only in runbook prose is invisible to the router and to its budget.
* **Prose is not a gate.** Whatever is decided here has to end in an exit code, or it repeats the
  failure mode it is fixing.

## Considered Options

* A separate `discovery` role, chained inside the planner run
* Widen `planner.authority` and add the provenance line, with no new role
* A separate `discovery` role with its own human-launched `/discover #N` entry point
* Do nothing: keep the observation as a planner runbook step

## Decision Outcome

Chosen: **a separate `discovery` role, chained inside the planner run**.

`.agents/orchestration/roles.yaml` declares `discovery` first — first in the file because it is first
in the route — with two carriers (`Claude discovery subagent`, `Codex $plan-issue #N self-discovery`),
`carrier_selection: run_route`, and `max_runs: 2`. Its authority is read-only capture plus the
fixture write, and explicitly *not* editing the issue body. The runbook is
[§Discovery runbook](../architecture/agent-process.md#discovery-runbook); the observation bounds moved
there wholesale out of planner runbook step 2, which now only records the returned block verbatim.

The role does not publish its own artifact: it returns the `## Evidence` block and the planner writes
it, exactly the division `architect_reviewer` already uses for findings. The human's entry point is
unchanged — `/plan #N` invokes the discovery carrier as its first step — so the sequence the document
describes and the sequence `decide()` produces are the same sequence.

Two exit codes carry the decision. `validate_issue_sections.py <N> --evidence-only --body-file
<path>` judges the `## Evidence` block alone, so the stage terminates on a check rather than on
prose while the planner's other sections do not exist yet. It reads the candidate block from disk
because the role may not edit the issue: at completion the block exists only in the hand-off, and a
gate its owing role cannot reach would be decoration. And the section's first non-empty line must be
`discovery: <carrier>` with the carrier declared in the catalogue, resolved by the same helper that
already resolves the `reviewer:` marker.

### Consequences

* Good, because the authority the capture actually needs is declared on the role that needs it, and
  the planner does not inherit it.
* Good, because the observation now has a budget the router enforces (`max_runs: 2`, then a visible
  escalation to a human with a named action) instead of being an unbounded step inside another role.
* Good, because a `bug` issue's evidence is attributable: the carrier is named and resolvable, and a
  renamed or undeclared carrier reds the gate.
* Good, because the human cost is unchanged: no new session, no new command.
* Bad, because the `## Evidence` field set changed retroactively. Sections written before this rule
  carry no provenance line; the migration is stated in §Issue contract, and a carrier may attest to
  an already-present fixture rather than re-run a capture for one line.
* Bad, because a provenance line records who *claimed* the observation, not that it happened. The
  gate resolves the carrier and stops there; a fabricated record reads exactly like an honest one.
  This limit is written into the runbook rather than left implied (§IV).
* Neutral: the fixture hand-off stays an untracked working-tree file until the implementer `git add`s
  it in the RED commit, which is the pre-existing `evidence/` contract, not a new one.

### Confirmation

Guards: `tests/test_agent_orchestrator.py` verifies discovery before planning, budget escalation, and that
`discovery` is not reported completed without its evidence. The target project should add focused tests for its
own issue-validation adapter; the portable catalogue and runbook keep their shared bounds in
`_SHARED_GATE_DEFINITIONS`, so no adapter may re-decide them.

What the guards do not prove: that a capture recorded in a block was actually run. That is the
fabrication limit above, and it is not testable from inside the repository.

## Pros and Cons of the Options

### A separate `discovery` role, chained inside the planner run

* Good, because authority, budget, and carrier set are declared per role, which is what makes the
  catalogue useful to the control plane.
* Good, because the provenance line resolves against a carrier set distinct from the planner's, so
  "the planner wrote it" and "a discovery carrier observed it" are different statements.
* Bad, because it is the larger diff: a catalogue entry, a runbook, a validator flag, a router
  branch, three adapters, and this record.

### Widen `planner.authority` and add the provenance line, with no new role

* Good, because it is roughly fifteen lines and fixes both named defects.
* Bad, because it widens the planner's authority permanently and unconditionally: every planner run
  gains the right to execute capture routes and write files, including the runs that never observe
  anything.
* Bad, because the provenance line would resolve against the planner's own carrier set, so a planner
  attesting to its own observation is the defect re-created in a new section.
* Bad, because the observation keeps no budget of its own: a repeatedly failing capture consumes the
  planner's single run and escalates as a planning failure.

### A separate role with its own human-launched `/discover #N` entry point

* Good, because the stage would be observable from outside as its own session with its own transcript.
* Bad, because it charges a human session per bug, most often to produce one `n/a: <reason>` line.
* Bad, because it puts a second ordering into the world: the document's sequence and the router's
  sequence would only agree when the human ran the commands in the intended order.

### Do nothing

* Good, because it costs nothing and no issue body changes shape.
* Bad, because it leaves a mandated artifact with a contradicted authority and no way to tell a real
  observation from an absent one — the state this issue opened against.

## More Information

* The `## Evidence` contract's runbook, field set, and capture table are in
  [`agent-process.md`](../architecture/agent-process.md#issue-contract).
* Precedent for a second carrier of a required artifact and for the `carrier_selection` field:
  [ADR-0003](0003-second-carrier-for-the-required-review-gate.md).
* Naming: the catalogue key stays `discovery` rather than `discoverer`/`observer`, so it matches the
  section-family name the issue contract already uses; recorded as declined in `## Out of scope`.
* Revisit this record if a prior-art / build-vs-buy research role lands: the two share the shape
  "produce a section the planner records", and a common parent may then be worth extracting.
