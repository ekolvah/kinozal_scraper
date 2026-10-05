## 0. Delivery start

- [x] 0.1 Run `agent-process start_change delete-doc-policy-and-language-check --planner Claude --implementer Claude` (tracking issue 615), then `EnterWorktree` at `.claude/worktrees/delete-doc-policy-and-language-check`; verify the script exits 0 and prints the branch and worktree

## 1. RED

- [x] 1.1 no RED: tooling-check and documentation deletion under `skip_specs` (design D4)

## 2. Delete the language check (D1)

- [x] 2.1 Delete `scripts/check_language.py` and `tests/test_language_policy.py`; remove `check_language()` and the `"language"` entry of `CHECKS` from `scripts/ci_check.py`; verify `python scripts/ci_check.py --list-checks` omits `language` and `python -m pytest tests/test_ci_check.py -q` passes
- [x] 2.2 `docs/architecture/ci-local.md`: drop `language →` from the check order and the `language`-check sentences (lines 23-26); replace the `check_language.py` exit-code example (lines 90-92) with one from a remaining gate or drop the example sentence; verify `git grep -n check_language -- docs/architecture/ci-local.md` prints nothing
- [x] 2.3 `docs/architecture/coverage-gaps-quality-gates.md:29-32`: rewrite the entry so the Russian return path of the `.claude/agents/*.md` denylist is a consciously accepted gap (D1 table, row 2), not "kept out transitively"; verify by reading the edited paragraph; commit group 2

## 3. Cut information-architecture.md (D2)

- [x] 3.1 Apply the D2 section table: delete the tier table and "Be honest about tokens", the generic Canonical-home bullets and "A human enforces" paragraph, the language section and §Memory ↔ repository; add `### Always-load budget`, `### Decision records`, `### Documentation guards` (marker sentence first); fix the in-file cross-references and rewrite the sentences at `:24-26` and `:234-238` per D2; verify `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py tests/test_doc_narrative.py -q` passes
- [x] 3.2 Fix inbound links: `ci-tooling-decisions.md:9` and `project-map.md:63` → `information-architecture.md#decision-records`; `CLAUDE.md:52` wording per D2; `tests/test_adr_records.py:13-14`, `:46`, `:112` → `information-architecture.md` §Decision records; verify `python -m pytest tests/test_doc_links.py tests/test_always_load_budget.py tests/test_adr_records.py -q` passes, and both `git grep -n "Canonical-home" -- ':!docs/adr' ':!openspec'` and `git grep -n check_language -- ':!docs/adr' ':!openspec'` print nothing; commit group 3

## 4. ADR-0014 (D3)

- [ ] 4.1 Write `docs/adr/0014-drop-the-documentation-language-gate.md` from `docs/adr/template.md` (status `accepted`, date 2026-10-04): English stays, the gate is dropped, evidence `ekolvah/agent-process-distribution#341` and the empty allow-list, reopen condition per D3; set ADR-0005 `status: "superseded by ADR-0014"`; retarget the `tests/test_doc_headers.py:52` comment to ADR-0014; verify `python -m pytest tests/test_adr_records.py tests/test_doc_links.py -q` passes; commit group 4

## 5. Verify

- [ ] 5.1 Run `npx -y @fission-ai/openspec@1.13.0 validate --strict --all`; verify exit 0
- [ ] 5.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`); verify exit 0

## 6. Deliver

- [ ] 6.1 Run `agent-process archive_change delete-doc-policy-and-language-check`, then `gh pr create --title "chore: delete-doc-policy-and-language-check" --body-file <report>` (report: `Refs #615` as a plain reference, the scenario → test map, the D1 lost-proof table); verify the PR opens on the archive head
- [ ] 6.2 Run `agent-process wait_for_pr <PR>` after creation and after every corrective push; stop when a settled head has no open P0/P1 thread or at the three-round escalation

## Scenario → test map

n/a: `skip_specs: true` — the change has no delta scenarios; link and record integrity is held by `tests/test_doc_links.py`, `tests/test_doc_headers.py`, `tests/test_doc_narrative.py`, `tests/test_adr_records.py`.
