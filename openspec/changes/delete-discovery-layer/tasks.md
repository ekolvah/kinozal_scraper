## 0. Delivery start

- [x] 0.1 Run `agent-process start_change delete-discovery-layer --planner Claude --implementer Claude` (tracking issue 626); enter `.claude/worktrees/delete-discovery-layer` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and #626 shows In Progress.

## 1. RED

- [x] 1.1 no RED: `skip_specs` deletion-and-docs change with no delta scenario. Dangling links are caught by `tests/test_doc_links.py`, bare mentions by the 4.4 grep (design D7).

## 2. Discovery carrier, Evidence format, capture table (design D1–D3)

- [x] 2.1 `git rm .claude/agents/discovery.md`; remove the discovery paragraph (lines "On a bug change whose design reads…" through "…its only trigger.") from `.claude/rules/workflow.md`. Verify: Glob `.claude/agents/**` is empty.
- [x] 2.2 In `docs/architecture/testing.md` add `### External-data capture routes` after the fixture-ratchet bullets, moving verbatim from `agent-process.md` §Evidence block: the six-row capture table, the route-safety paragraph, the "never run a full pipeline … to collect" sentence, the "no safe read-only route → do not improvise" sentence, the fixture-placement rule, the "unsupported unavailability claim is still a gap; a dependent design stays blocked until a capture succeeds" rule and the "missing fixture → capture again, never hand-written bytes" rule (design D3). Rewrite the ratchet bullet to link `#external-data-capture-routes` and say "record that command plus its fixture path in the change's proposal".
- [x] 2.3 In `docs/architecture/agent-process.md` delete §Evidence block and §Discovery runbook; the intro's "This document holds only…" sentence names the governance conventions only.
- [x] 2.4 In `docs/architecture/principles.md` repoint the §V routing-table link to `testing.md#external-data-capture-routes` and drop "Evidence block, discovery," from the governance pointer; trim the same parenthetical in `CLAUDE.md` §PR Workflow and `.claude/rules/mindset.md`. Verify: `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py -q` passes; commit.

## 3. Agent review doc and frontmatter guard (design D4)

- [ ] 3.1 `git rm docs/architecture/ci-agent-review.md tests/test_agent_frontmatter.py`; remove the `ci-agent-review.md` line from `docs/architecture/ci.md` and from `_READ_BUDGET_DOCUMENTS` in `tests/test_doc_headers.py`.
- [ ] 3.2 Drop the `test_agent_frontmatter.py` / `.claude/agents/*.md` mentions from the docstrings of `tests/test_doc_headers.py` (genre sentence, scope paragraph, glob paragraph, the "precedent is `test_agent_frontmatter.py`" sentence near line 140) and `tests/test_adr_records.py` (empty-catalogue sentence); in `docs/architecture/information-architecture.md` make the two `.claude/agents/` clauses name `.claude/commands/` only.
- [ ] 3.3 Remove `tests/test_agent_frontmatter.py` from the PyYAML comment in `requirements-dev.in` (ASCII only), run `pip-compile` for `requirements-dev.txt` in the same commit. Verify: `git diff --stat requirements-dev.txt` shows no dependency change; `python -m pytest tests/test_doc_headers.py tests/test_adr_records.py tests/test_doc_links.py -q` passes; commit.

## 4. Project map, ledger, ADR links (design D5, D6)

- [ ] 4.1 In `docs/architecture/project-map.md` remove the `discovery.md` and `ci-agent-review.md` rows; reword the `agent-process.md` row (governance conventions only), the `workflow.md` row (no discovery trigger), and the `evidence/` and capture-script rows to link `principles.md#v-root-cause-before-fix` and `testing.md#external-data-capture-routes`.
- [ ] 4.2 In `docs/architecture/coverage-gaps-quality-gates.md` remove `W` and rewrite `AR` per design D6; in `docs/architecture/coverage-gaps.md` set the quality-gates line to `V`, `X` through `AD`, and `AR`, and list `W` as retired with #626.
- [ ] 4.3 In `docs/adr/0009-discovery-is-a-separate-role-chained-inside-the-planner-run.md` replace the `#discovery-runbook` and `#evidence-block` links with permalinks to `https://github.com/ekolvah/kinozal_scraper/blob/eef253d/docs/architecture/agent-process.md#discovery-runbook` / `#evidence-block`.
- [ ] 4.4 Verify: `git grep -n -e 'agents/discovery' -e 'discovery runbook' -e 'Discovery runbook' -e 'evidence-block' -e 'Evidence block' -e ci-agent-review -e test_agent_frontmatter -e '\.claude/agents' -- ':!openspec' ':!docs/adr'` prints nothing, and `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py tests/test_adr_records.py tests/test_always_load_budget.py -q` passes; commit.

## 5. Verify

- [ ] 5.1 Run `openspec validate --strict --all`. Verify: exit 0.
- [ ] 5.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 6. Deliver

- [ ] 6.1 Run `agent-process archive_change delete-discovery-layer`, then `gh pr create --title "chore: delete-discovery-layer" --body-file <report>`; the report names #626, #612 and #616 as plain references and carries the scenario map.
- [ ] 6.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
| --- | --- |
| — | n/a: `skip_specs: true`, no delta scenarios; dangling links are caught by `tests/test_doc_links.py`, bare mentions by the 4.4 grep (design D7) |
