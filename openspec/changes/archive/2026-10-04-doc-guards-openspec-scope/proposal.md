# Proposal

## Why

The first change planned through the agent-process plugin here (`delete-doc-policy-and-language-check`,
tracking issue 615) cannot pass the quality gate: its own planning artifacts fail two repository doc
guards once `start_change` commits them. Observed on branch `delete-doc-policy-and-language-check`
@ `7c2d07b`, `python -m pytest tests/test_doc_links.py tests/test_doc_narrative.py -q`:

- `test_doc_links::test_every_internal_link_resolves` fails on
  `openspec/changes/delete-doc-policy-and-language-check/design.md -> information-architecture.md#decision-records`
  as a missing file (and the same in `tasks.md`), plus the illustrative `file.md#anchor` in `design.md`.
  Planning artifacts name files by repository path, not relative to their own folder.
- `test_doc_narrative::test_no_narrative_issue_ref` fails on seven lines of `proposal.md`/`design.md`,
  for example `proposal.md:5: Epic #614 makes the agent-process plugin the only owner …`.

Until now `openspec/` tracked no Markdown (`git ls-files openspec` on `main` @ `a3b5653`: only
`.gitkeep` files and `config.yaml`), so the conflict never showed. Every future change hits it, and an
archived change stays tracked: a plan that deletes a file keeps an anchor into it forever.

Maintainer decisions (2026-10-04): exclude `openspec/` from `test_doc_links` scope; delete
`test_doc_narrative` outright — it gates writing style (the form of an issue reference), not
correctness, and its own docstring says it checks form, not genre.

## What Changes

- `tests/test_doc_links.py`: tracked `.md` under `openspec/` leave the scanned set (they stay valid link
  targets); a test pins the carve-out as non-degenerate, as `test_doc_narrative` did for `docs/adr/`.
- Delete `tests/test_doc_narrative.py` with no replacement.
- Remove its rule text: `information-architecture.md` (the guard sentence and the **Link form** block),
  `ci-workflow.md` §Doc guards (**reference form** bullet, the repository-wide branch mention and the
  "form" part of the closing paragraph),
  `coverage-gaps-quality-gates.md` entry AC (a branch of the deleted guard) replaced by the accepted gap,
  and the `requirements-dev.in` comment.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. No spec exists under `openspec/specs/`; the change edits developer tooling and documentation
only, so `.openspec.yaml` sets `skip_specs: true`.

## Impact

- Removed: `tests/test_doc_narrative.py`.
- Edited: `tests/test_doc_links.py`, `docs/architecture/information-architecture.md`,
  `docs/architecture/ci-workflow.md`, `docs/architecture/coverage-gaps-quality-gates.md`,
  `requirements-dev.in` (comment only; `markdown-it-py` stays, `test_doc_links` and
  `test_adr_records` import it).
- Not touched: ADR-0001, ADR-0005 and ADR-0011 mention the guard; accepted records are not rewritten.
  `openspec/specs/` leaves the link check together with the change artifacts (design D1).
- CI: the guard ran inside `pytest`, which has no per-test job, so no workflow file changes.
- Conflicts: change `delete-doc-policy-and-language-check` (issue 615) edits the same
  `information-architecture.md` sections; it rebases after this change merges.
