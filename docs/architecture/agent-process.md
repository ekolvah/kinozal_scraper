# Agent development process

**Question this document answers:** Which parts of the development process this repository
owns on top of the agent-process plugin.

The process is the installed agent-process plugin's `agent-process` skill
([ADR-0013](../adr/0013-adopt-agent-process-plugin-v2.md)): `/opsx:propose` writes an OpenSpec
change and obtains the plugin's architect review, `agent-process start_change <change>` creates
the linked branch, `/opsx:apply` implements the change test-first (`agent-process check_red`),
`agent-process archive_change <change>` archives it, `gh pr create` opens the PR, and
`agent-process wait_for_pr <PR>` waits for its checks and review threads. Its steps, gates, and
review contract are the plugin's and are not restated here. The merge gate is the repository
ruleset ([`ci-branch-protection.md`](ci-branch-protection.md)).

This document holds only what the plugin does not: the Evidence block a bug change records and
the role that produces it, and the repository's governance conventions.

## Evidence block

A bug change whose design reads, parses, or classifies data from an external system records a
completed observation of that system in the change's `proposal.md`, under `## Evidence`:

```md
discovery: <carrier that ran the observation, e.g. Claude discovery subagent>
capture: `<source-specific reproducible command that writes the path below>`
path: `<repository-relative path>`
observed: <the source fact that explains the reported failure>
preserve: <the exact valid record from the same captured response that must keep working>
change: <the exact invalid record from that captured response whose behaviour must change>
boundaries: <candidate fix boundaries compared, from broad to narrow>
collateral: <whether each candidate preserves or loses that exact valid record>
reuse: <current production path traced to the existing input/fetch usable by the narrow boundary>
paired-test: <the same captured input through one pipeline run keeps the valid record and rejects the invalid record>
```

The first non-empty line is the provenance marker; only that line counts, because the fields
below routinely quote it. What it proves is stated in §Discovery runbook.

The command is specific to the external source and must include the exact path named on the
next line. That path is under `evidence/<change>/`; the captured file is working-tree-only
planning evidence, ignored by Git and kept locally only until merge. The reviewer receives a
verified, safe, compressed observation record in the proposal, not the full payload.

The remaining fields turn the capture into a reviewable design decision: compare at least the
reported invalid record with an exact valid record from the same captured response. A sibling
feed, query, category, or alternative source does not count as preservation of that record. A
candidate that loses the preserved record is a blocking review finding unless the proposal
records an explicit product decision authorizing that loss. Choose the narrowest boundary
supported by the observation, trace the current production path before claiming that it needs
another fetch, and expose collateral loss instead of silently accepting it. Use the narrowest
read-only route below; never run a full pipeline that writes Sheets rows or sends Telegram
notifications merely to collect evidence:

| Source | Capture route |
| --- | --- |
| Kinozal | `python scripts/capture_kinozal_fixture.py <url> <path>` |
| GitHub REST | `python scripts/capture_external_fixture.py github <endpoint> <path> --confirm-repository-safe` |
| Telegram channel input | `python scripts/capture_external_fixture.py telegram <channel-url> <path> --confirm-repository-safe` |
| Gemini summarization | `python scripts/capture_external_fixture.py gemini <saved-input> <path> <--broadcast|--chat> --confirm-repository-safe` |
| Existing Sheets worksheet | `python scripts/capture_external_fixture.py sheets <spreadsheet-url> <worksheet> <path> --confirm-repository-safe` |
| Another source with a read-only CLI | `<read-only command> | python scripts/capture_external_fixture.py stdin <path> --confirm-repository-safe` |

The safety flag is an explicit claim, not a sanitizer: inspect the payload and never commit
credentials, private messages, or other sensitive data. The Telegram route calls
`TelethonReader` without Gemini or a notifier; the Gemini route replays an already saved input
without Telegram delivery; the Sheets route only reads an existing worksheet; and the GitHub
route permits one `gh api` GET rather than arbitrary subprocess arguments. The `stdin` route
persists output but does not execute the upstream tool, so it adds no generic
process-execution capability.

If no safe read-only route exists, do not improvise with a side-effecting production entry
point. A failed capture records `status: failed` plus a non-empty fenced block after `output:`
containing the attempted command's output; an unsupported claim that the source is unavailable
is still a gap. This makes the access failure reviewable but does not prove source behaviour:
a design that depends on the missing fact stays blocked until a capture succeeds.

Captured bytes belong in `tests/fixtures/` only when a production-behaviour regression test
reads them in the same commit. Full transcripts and
planning history are not test fixtures; removing the local `evidence/` copy after merge does
not remove the proposal's durable record.

For a bug with no external-system behaviour to observe, the fields are replaced by
`n/a: <reason>` naming why live capture does not apply. The provenance line still comes first:
the `n/a` branch is a discovery verdict, so it carries the same carrier the fields would have.

Nothing checks this shape mechanically: the v1 validator that did is gone, and the plugin has
no equivalent. The block is held by this prose and by the architect review; the loss is
recorded as `AR` in [`coverage-gaps-quality-gates.md`](coverage-gaps-quality-gates.md).

## Discovery runbook

The `discovery` subagent (`.claude/agents/discovery.md`) runs during `/opsx:propose` on a bug
change, **before** the design is written: a design that describes how to read, parse, or
classify external data needs the observation as input, not as a note appended afterwards. The
plugin's `/opsx:propose` does not invoke it; the agent running the propose step does, because
this document and [`.claude/rules/workflow.md`](../../.claude/rules/workflow.md) say so.

**Authority.** Discovery may run the read-only capture routes in §Evidence block and write the
captured fixture into the working tree. It may not edit the change's artifacts, create a
branch, or change production code. It returns the `## Evidence` block; the propose run records
it in `proposal.md` unchanged, so what reaches the proposal stays attributable to whoever
actually did the work.

**Bounds of the observation.** Observe the live system before the design is written. Record one
invalid record and one exact valid record from the same response, compare candidate fix
boundaries, and state whether each boundary loses that preserved record. Inspect the current
call path before deciding whether a narrower classification needs a new fetch, and name one
paired test that sends the same captured input through one pipeline run and proves that the
valid record remains while the invalid one changes. Otherwise record `n/a: <reason>`; this is
discovery, not an E2E test or a substitute for a human product decision.

**Route.** Use the narrowest read-only route in the §Evidence block capture table. Two runs are
budgeted, because a failed capture is normally retried once after access is fixed; a third
attempt is a standing external obstacle and goes to a human.

**Fixture hand-off.** The captured file stays untracked at discovery time. `/opsx:apply`
`git add`s it in the RED commit only when a production-behaviour regression test reads those
bytes in the same commit. A fixture that is missing when the implementation needs it means
discovery runs again — never that the implementer writes the bytes by hand.

**What the provenance line does not prove.** `discovery: <carrier>` records who claimed the
observation, not that the observation happened; a fabricated record reads exactly like an
honest one (§IV). What the line buys is attribution: an anonymous claim cannot be questioned,
and a named one can.

## Governance conventions

1. Keep one PR to one logical unit. A temporary CI unblock for an unrelated failure may
   accompany the blocked change only when it has a tracked follow-up for the root cause.
2. Assign exactly one type label when creating an issue: `bug` for broken behaviour; then
   `perf` / `security` / `enhancement` for user-visible work; otherwise `refactor`, `testing`,
   `ci`, `documentation`, or `chore` by the changed area. Non-type labels are outside this
   taxonomy.
3. Ask the user for issue priority, then set the GitHub Project field with
   `python scripts/set_issue_priority.py <N> <High|Medium|Low>`. Propose High for user-facing
   bugs and development-process work, Medium for agentic capability work outside the process,
   and Low otherwise; name the rule used.
4. If a `requirements*.in` file changes, run `pip-compile` for its matching lockfile in the
   same commit.
5. Do not push to `main`, force-push, bypass hooks, or merge your own PR; the maintainer merges.
   Local agent hooks are defense in depth; the ruleset is authoritative.
6. Trivial non-behavioural one-line changes may skip the change workflow only with the explicit
   rationale recorded in the issue or PR.
