## Context

`tests/test_doc_links.py` scans every tracked `.md` (`_tracked_docs`, `git ls-files -z`) and resolves
links relative to the scanned file. `tests/test_doc_narrative.py` scans tracked `.md` minus `docs/adr/`
for `#N` form, plus a repository-wide sigil branch over all tracked files. OpenSpec change artifacts are
written by the plugin's propose run with repository-root paths and issue numbers in running prose, and
`start_change` commits them; `archive_change` keeps them tracked under `openspec/changes/archive/`
(plugin 3.8.1 `skills/agent-process/scripts/archive_change.py:107-108` runs `git add -A openspec`, then
commits).

## Goals / Non-Goals

**Goals:**

- A committed or archived OpenSpec change cannot fail `test_doc_links` or a `#N` form guard. The
  language check (`scripts/check_language.py`) also scans `openspec/`; change
  `delete-doc-policy-and-language-check` owns its removal.
- No guard on `#N` reference form remains.

**Non-Goals:**

- Excluding `docs/adr/` from `test_doc_links` (accepted records may fix broken links, so the guard still
  applies there).
- Rewriting ADR-0001, ADR-0005 or ADR-0011, which mention the deleted guard.
- Changing `test_doc_headers.py` or `test_adr_records.py` (their scopes do not include `openspec/`).

## Decisions

**D1. `test_doc_links` skips `openspec/` as a source, not as a target.** `_tracked_docs` drops names
under `openspec/`; `_tracked_paths` is unchanged, so a document may still link into a change. Reason:
change artifacts are dated records written against the repository root, and a later change routinely
deletes what an earlier plan points at; the only fix would be rewriting the archive. A new test
`test_openspec_records_are_out_of_scope` asserts that tracked `.md` exist under `openspec/` and none is
in `_tracked_docs()`, so the carve-out is not vacuous (the shape of the deleted
`test_doc_narrative::test_adr_records_are_out_of_scope`). The docstring §Scope gains one sentence.

Lost proofs, both accepted with no catcher:

- Links inside OpenSpec change artifacts resolve. Accepted: the archive is history, not navigation.
- Links inside `openspec/specs/` resolve. These are current specs, not dated records, so the reason above
  does not cover them; accepted because `openspec/specs/` holds no `.md` today (`git ls-files openspec`
  on `main` @ `a3b5653`: `openspec/specs/.gitkeep` only) and specs carry requirements and scenarios, not
  navigation.

Alternative rejected: rewrite each plan to satisfy the guard. Every future change pays it, and an
archived plan still breaks when a later change deletes its target.

**D2. Delete `test_doc_narrative.py` without replacement** (maintainer decision). It gates writing form,
and its docstring concedes that a chronicle in parentheses passes. Lost proofs:

| Lost proof | Catcher after the change |
|---|---|
| `#N` in body prose is a parenthetical pointer | None; accepted — D3 deletes the rule with the guard |
| `#N` absent from section headings | Partial: `test_doc_links` fails once such a heading is renamed and an inbound anchor dangles (the effect, not the cause) |
| `#` sigil used only for issue/PR references, in all tracked files | None; accepted — the convention is deleted too |

Ledger entry AC in `coverage-gaps-quality-gates.md` records a branch not taken of the deleted guard; it is
replaced by one entry recording the dropped link-form guard (named by role, not path) as an accepted gap,
so "why no link-form guard?" is not reopened.

**D3. Rule text goes with the guard.** `information-architecture.md`: drop the `test_doc_narrative`
sentence from the guards paragraph ("all three" → "both") and the **Link form** block. `ci-workflow.md`
§Doc guards: drop the **reference form** bullet, "(plus one repository-wide branch, see below)", and the
"/ form" and "like a chronicle carefully put in parentheses" parts of the closing paragraph.
`requirements-dev.in`: drop the file from the `markdown-it-py` comment.

**D4. RED.** D1 changes guard behaviour, so `test_openspec_records_are_out_of_scope` is written first and
fails on the plan commit, where this change's own artifacts are tracked under `openspec/`.

## Risks / Trade-offs

- [Narrative `#N` returns to docs] → accepted; no catcher (D2).
- [A broken link is written inside a plan] → accepted; D1.
- [Merge conflict with `delete-doc-policy-and-language-check`, which edits the same
  `information-architecture.md` sections] → that change rebases after this one merges.

Rollback: revert the PR.
