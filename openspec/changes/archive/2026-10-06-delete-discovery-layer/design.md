## Context

See proposal.md — Why for the split of #616 step 3 and the observations. Constraints:

- `docs/adr/` is append-only; the IA policy allows fixing broken links in a record
  (`information-architecture.md` §Decision records).
- `tests/test_doc_links.py` checks every internal `.md` link and anchor-bearing code span under
  `docs/architecture`, `docs/adr`, `.claude/rules`, `.claude/commands`.
- `principles.md` and the governance conventions are C's (#627); this change edits only the two
  `principles.md` lines that point into deleted sections.

## Goals / Non-Goals

**Goals:** no local subagent, discovery trigger or Evidence record format remains; the capture
table survives in the testing docs; no dangling reference.

**Non-Goals:** `principles.md` §V's observation duty (C); capture scripts and the fixture ratchet
(product tooling, #613); `.gitignore` `evidence/` (§V still names `evidence/<change>/`).

## Decisions

### D1 — Delete the discovery carrier and its trigger

`.claude/agents/discovery.md` and the `workflow.md` paragraph that tells `/opsx:propose` to
invoke it go. Upstream #307 is closed as not planned, so no plugin release will replace them;
#616 decides deletion over a local copy.

What stops: the instruction that chains a live observation into a bug's propose run. What is
actually reached afterwards: the plugin's `architect-reviewer` subagent, invoked on every propose
run, reviews against the plugin's own `skills/agent-process/principles.md` (its
`agents/architect-reviewer.md` reads that copy; this repository has no `skills/agent-process/`),
whose §V requires the live observation for a design about external data. The check therefore does
not depend on this repository's `principles.md` (C's scope), but a plugin release that drops that
§V text removes it — recorded in `AR`'s revisit trigger (D6). This was already the only check
since #600: the trigger was prose, not an exit code.

Alternative "keep `discovery.md` as a repository-owned role" — rejected: it contradicts #616's
goal (process files are the plugin's or deleted) and upstream declined the role.

### D2 — Delete the Evidence record format; keep its content where it already lives

§Evidence block (provenance line, field list, failed-capture format, `n/a` branch) and §Discovery
runbook go. The substance stays in force elsewhere: §V keeps the duty to preserve the capture
under `evidence/<change>/`, record a compressed observation in the proposal, compare an invalid
and an exact valid record and pair them in one test; `testing.md` keeps the paired-test rule;
the plugin skill's Proposal section requires the reproduction under **Why**.

Lost: the fixed field schema, the `discovery: <carrier>` provenance line and the
`status: failed` + `output:` record shape. Nothing checked any of them mechanically since #600;
the catcher stays the plugin's architect review over the plugin's §V (D1, `AR`). The two capture
rules that are not record format move to `testing.md` (D3).

### D3 — Move the capture table to `testing.md`

New `### External-data capture routes` in `testing.md`, after the fixture-ratchet bullets, holds
verbatim: the six-row source → command table, the route-safety paragraph (what each route does
and does not call; the safety flag is a claim, not a sanitizer), "never run a full pipeline that
writes Sheets rows or sends Telegram notifications to collect a capture", "no safe read-only route
→ do not improvise with a side-effecting entry point", the fixture-placement rule (captured
bytes enter `tests/fixtures/` only with a regression test reading them in the same commit), and
two rules nothing else holds:

- "an unsupported claim that the source is unavailable is still a gap … a design that depends on
  the missing fact stays blocked until a capture succeeds" (`agent-process.md` lines 73-75);
- "a fixture that is missing when the implementation needs it means the capture runs again —
  never that the implementer writes the bytes by hand" (lines 118-119; "discovery runs again"
  becomes "the capture runs again", the only wording change).

The ratchet bullet that pointed to `agent-process.md` points to this heading and says "record that
command plus its fixture path in the change's proposal". `principles.md` §V and the
`project-map.md` capture rows link here.

Not moved: the `status: failed` + `output:` record shape — it is Evidence-block format (D2).

### D4 — Delete `ci-agent-review.md` and `tests/test_agent_frontmatter.py`

`ci-agent-review.md` holds two things. §Model pinning is policy for `.claude/agents/*.md`, which
D1 empties; upstream #317 made subagents inherit the session model, so no local agent is expected
to need a pin. The workflow and one-time-setup part restates the plugin's managed caller and the
plugin skill's Install step 3 (`claude setup-token`, `gh secret set CLAUDE_CODE_OAUTH_TOKEN`).

`tests/test_agent_frontmatter.py` asserts a non-empty `.claude/agents/` glob, so it would fail by
design once `discovery.md` is gone; it goes with the last agent. Lost proofs and their catchers:

- Full model id and valid `effort` in agent frontmatter → no local agent remains; the plugin's
  agents are pinned (or inherit) upstream and tested by the plugin's CI on its release head.
- Verbatim severity-wording denylist over agent prompts (`W`) → no local prompt remains; reviewer
  prompts are the plugin's. A future local agent would arrive unguarded; accepted, since #616 puts
  agents in the plugin (`W` retired, D6).

Inbound: `ci.md` router line, `project-map.md` row, `tests/test_doc_headers.py` read-budget list
entry and docstring mentions, `tests/test_adr_records.py` docstring mention.

### D5 — Inbound references, ADR-0009, no new ADR

Every reference listed in proposal.md — Impact is removed or repointed. ADR-0009 links to
`agent-process.md#discovery-runbook` and `#evidence-block`; both anchors disappear and
`test_doc_links.py` would fail. They become permalinks to
`https://github.com/ekolvah/kinozal_scraper/blob/eef253d/docs/architecture/agent-process.md#…`,
the last head carrying the sections — a broken-link fix, not a rewrite. ADR-0013's migration row
"`discovery` subagent and Evidence capture — gap → kept" stays as decided (append-only); the
reversal is recorded by #616/#626 and by `AR`. No new ADR: deleting documentation and one persona
file is reverted with `git revert`, so it fails the IA cost-of-change entry filter — the same call
#629 made for the hooks ADR-0013 had kept.

### D6 — Ledger

- `W` retired: its "what stays local" half (the denylist over `.claude/agents/*.md`) has no
  subject, and its other half (reviewer prompts are upstream) is not a repository gap. Entry
  removed; `coverage-gaps.md` lists `W` as retired with #626; the quality-gates range reads
  `V`, `X` through `AD`, and `AR`. ADR-0014's mention of "entry W" stays (append-only); the router
  says where it went.
- `AR` rewritten in place, ID kept because the gap persists in a narrower form: "§V's live
  observation has no carrier and no gate (#600, #626)". Held by the plugin's architect review
  over the plugin's `skills/agent-process/principles.md` §V (D1). Revisit trigger: a design about
  external data ships without an observation, or a plugin release drops the §V live-observation
  text.

### D7 — Catching dangling references

`tests/test_doc_links.py` catches a link or anchor-bearing code span into a deleted file or
heading in the scoped directories, `docs/adr` included. It does not see bare mentions, `CLAUDE.md`
or Python docstrings; task 4.4's `git grep` over the removed names covers those. No import of a
deleted module exists, so test collection is not a catcher here.

## Risks / Trade-offs

- [A bug change about external data skips the live observation] → only the plugin's architect
  review over the plugin's §V catches it, as since #600; recorded in `AR`, whose trigger also
  names a plugin release dropping that text.
- [A future local agent ships without a model or wording guard] → accepted (D4); #616 routes
  agents to the plugin.
- [Moved table drifts from the scripts' CLI] → unchanged risk; the table moved verbatim.

Rollback: revert the PR; nothing outside the repository changes.
