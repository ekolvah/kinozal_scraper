## 0. Delivery start

- [x] 0.1 Run `agent-process start_change doc-guards-openspec-scope --planner Claude --implementer Claude` (tracking issue 620), then `EnterWorktree` at `.claude/worktrees/doc-guards-openspec-scope`; verify the script exits 0 and prints the branch and worktree

## 1. RED

- [x] 1.1 Add `TestDocLinks::test_openspec_records_are_out_of_scope` to `tests/test_doc_links.py` (design D1, D4): tracked `.md` exist under `openspec/`, and none is in `_tracked_docs()`; run `agent-process check_red tests/test_doc_links.py::TestDocLinks::test_openspec_records_are_out_of_scope`; verify it reports RED; commit RED

## 2. Exclude openspec/ from test_doc_links (D1)

- [x] 2.1 `_tracked_docs` drops names under `openspec/`; `_tracked_paths` unchanged; add the scope sentence to the module docstring; verify `python -m pytest tests/test_doc_links.py -q` passes; commit group 2

## 3. Delete test_doc_narrative (D2, D3)

- [x] 3.1 Delete `tests/test_doc_narrative.py`; drop it from the `requirements-dev.in` comment; verify `git grep -n test_doc_narrative -- ':!docs/adr' ':!openspec'` lists only the doc lines 3.2 removes
- [x] 3.2 `information-architecture.md`: remove the `test_doc_narrative` sentence ("all three" → "both") and the **Link form** block; `ci-workflow.md` §Doc guards: remove the **reference form** bullet, "(plus one repository-wide branch, see below)" and the form/chronicle parts of the closing paragraph (D3); `coverage-gaps-quality-gates.md`: replace entry AC with the accepted-gap entry per D2, naming the guard by role; verify `git grep -n test_doc_narrative -- ':!docs/adr' ':!openspec'` and `git grep -n -i -e "reference form" -e sigil -e "parenthetical pointer" -e chronicle -- docs/architecture` print nothing, and `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py -q` passes; commit group 3

## 4. Verify

- [x] 4.1 Run `npx -y @fission-ai/openspec@1.13.0 validate --strict --all`; verify exit 0
- [x] 4.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`); verify exit 0

## 5. Deliver

- [x] 5.1 Run `agent-process archive_change doc-guards-openspec-scope`, then `gh pr create --title "test: doc-guards-openspec-scope" --body-file <report>` (report: the tracking issue as a plain reference, the scenario → test map, the D2 lost-proof table); verify the PR opens on the archive head
- [ ] 5.2 Run `agent-process wait_for_pr <PR>` after creation and after every corrective push; stop when a settled head has no open P0/P1 thread or at the three-round escalation

## Scenario → test map

n/a: `skip_specs: true` — no delta scenarios. Design D1 is held by `tests/test_doc_links.py::TestDocLinks::test_openspec_records_are_out_of_scope`.
