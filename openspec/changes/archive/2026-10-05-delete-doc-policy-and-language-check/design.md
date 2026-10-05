## Context

`information-architecture.md` mixes two kinds of content. Portable: the Claude Code carrier tiers,
"every fact has exactly one home" with its instances, and memory versus repository. Repository-
specific: the navigation graph (`CLAUDE.md` → `project-map.md`), the decision-rationale route
(coverage ledger / `ci-tooling-decisions.md` / ADR), the `docs/adr/` directory policy, the
always-load budget gate, and the doc-header, link and narrative guards. `principles.md`,
`mindset.md` and the `hooks.py` memory check are deleted by #612, so this change does not edit them.

## Goals / Non-Goals

**Goals:**

- No language check and no documentation-language section remain.
- `information-architecture.md` keeps only what is specific to this repository; every inbound
  anchor still resolves.
- The changed ADR-0005 decision is recorded append-only.

**Non-Goals:**

- Translating or removing Russian operator diagnostics in `scripts/` (string literals, never in
  scope).
- Editing `principles.md`, `.claude/rules/mindset.md`, `scripts/hooks.py` (#612).
- Rewording the kept guard sections beyond the link fixes below.

## Decisions

**D1. Delete the check without a replacement.** Upstream declined to carry it (`#341`); the
migration allow-list is empty and no recurrence was observed. Lost proofs and their catchers:

| Lost proof | Catcher after the change |
|---|---|
| Tracked Markdown prose and Python comments/docstrings contain no Cyrillic | None automated; the maintainer reviews before merging. The cloud `agent-review` prompt lives upstream and is not shown to reach this case (`coverage-gaps-quality-gates.md` entry W), so it is not counted. Accepted in ADR-0014 |
| Russian severity wording does not return to `.claude/agents/*.md` (`coverage-gaps-quality-gates.md:29-32`) | None. The ledger entry is rewritten from "kept out transitively" to a consciously accepted gap; the only local agent file is `discovery.md`, and #612 deletes `tests/test_agent_frontmatter.py` too |
| The mapped-Markdown question marker is the single English form | Unchanged: `tests/test_doc_headers.py::_MARKERS` |

Alternative rejected: keep the check locally as a repository-owned gate. It contradicts #614 and
pays a CI job per push for a migration that is finished.

**D2. Cut `information-architecture.md` by section.**

| Section | Action |
|---|---|
| Two graph layers | keep; rewrite the sentence at `:24-26` ("this file describes the tier for principles") — the tier table is gone, so the edge is `principles` → this file only |
| Knowledge-carrier tier model | delete the table and "Be honest about tokens"; keep the always-load budget and token-trend paragraphs under `### Always-load budget`; keep one sentence naming `docs/architecture/` (reference: how the code works) and `docs/adr/` (MADR rationale) |
| Canonical-home rule | delete the rule quote, the agent-procedure, procedural-rules, principles-wording, enforcement-facts, navigation-policy and `.claude/rules/` bullets and the "A human enforces" paragraph; keep the decision-rationale route and the `docs/adr/` directory policy under the new heading `### Decision records` |
| Documentation and commentary language policy | delete; its marker sentence moves to the head of the doc-header guard text, which becomes `### Documentation guards` |
| What documentation describes | keep; fix "§Canonical-home above" → `#decision-records`; rewrite the closing paragraph (`:234-238`) without the memory instance and without "see the end of the file" |
| Memory ↔ repository | delete |

Inbound links to fix: `ci-tooling-decisions.md:9` (currently points to a non-existent
`project-map.md` §Canonical-home) → `information-architecture.md#decision-records`;
`project-map.md:63` "(§Canonical-home)" → the same anchor; `CLAUDE.md:52` "holds tiers and
canonical homes" → "holds the documentation tiers, decision records and doc guards";
`tests/test_adr_records.py:13-14`, `:46` and the `:112` failure message name `project-map.md`
§Canonical-home as the home of the closed status set → `information-architecture.md`
§Decision records. These are prose pointers that `tests/test_doc_links.py` does not parse, so the
task greps the whole repository for `Canonical-home`. The anchor
`#what-documentation-describes-current-state-not-history-or-ideas` used by `project-map.md` and
ADR-0013 survives. `principles.md` links point at the file and stay valid.

Alternative rejected: delete the whole file and move the kept parts into `project-map.md`. That
breaks the containment rule `project-map.md` states (index, not content) and more inbound links.

**D3. ADR-0014 supersedes ADR-0005** (maintainer's choice, 2026-10-04). ADR-0014 keeps "English
for repository documentation and Python commentary" and the same boundary, and drops the gate:
nothing is automated, and the maintainer reviews before merging. It names the evidence (`#341`, empty allow-list) and the reopen condition:
a merged PR that adds Cyrillic documentation prose. ADR-0005 gets only `status: "superseded by
ADR-0014"` (the policy allows the status change; body untouched). `tests/test_doc_headers.py:52`
comment retargets to ADR-0014.

**D4. No RED test.** The change deletes a tooling check and documentation; no behaviour gains a
test. A guard asserting `"language" not in CHECKS` would pin an absence nobody reintroduces by
accident. Verification is the existing doc guards (`test_doc_links`, `test_doc_headers`,
`test_doc_narrative`, `test_adr_records`, `test_always_load_budget`) and `ci_check.py`.

## Risks / Trade-offs

- [Russian prose re-enters docs unnoticed] → accepted; ADR-0014 states the reopen condition.
- [A removed anchor is linked from a file not found by grep] → `tests/test_doc_links.py` fails the
  gate on any unresolved `file.md#anchor`.
- [Merge conflict with the open `audit-repo-owned-process-files` change, which edits
  §Memory ↔ repository] → that change is superseded by #616/#612 re-planning; whichever lands
  second drops the section edit.

Rollback: revert the PR; the check and sections return intact.
