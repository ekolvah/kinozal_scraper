## 0. Delivery start

- [x] 0.1 Run `agent-process start_change delete-local-telemetry-stack --planner Claude --implementer Claude` (tracking issue 642); enter `.claude/worktrees/delete-local-telemetry-stack` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and #642 shows In Progress.

## 1. RED

- [ ] 1.1 no RED: `skip_specs` deletion of development tooling and its docs; no delta scenario, no scraper behaviour changes. The kept project label stays guarded by `tests/test_otel_project_label.py`.

## 2. Delete the stack (design D1)

- [ ] 2.1 `git rm` every path under proposal Impact → Removed. Verify: `git ls-files observability scripts/token_trend.py scripts/check_otel_event_delivery.py 'tests/fixtures/otel-delivery-*'` prints nothing.
- [ ] 2.2 In `.claude/settings.json` delete the `SessionStart` group whose command runs `scripts/token_trend.py`; every other key unchanged. Verify: `python -c "import json;h=json.load(open('.claude/settings.json',encoding='utf-8'))['hooks']['SessionStart'];print([x['command'] for g in h for x in g['hooks']])"` prints only the `agent-process-check.py` command.

## 3. Docs and ADRs (design D2, D3, D4)

- [ ] 3.1 `operations.md` and `llm-security.md` per design D3. Verify: `git grep -n OTEL_LOG_ -- docs/architecture/llm-security.md` prints nothing and the `#rollback-and-rotation` anchor still exists.
- [ ] 3.2 `coverage-gaps-agent-tooling.md`: delete AJ and AN, widen AU per design D2. `coverage-gaps.md`: the tooling row lists `AS`, `AU`, `J`, and adds `AJ`, `AN` to the retired letters with "(#642)". Verify: `git grep -n -e '\*\*AJ\.' -e '\*\*AN\.' -- docs/architecture` prints nothing.
- [ ] 3.3 `project-map.md`: delete the rows for `scripts/token_trend.py`, `observability/claude-code/`, `observability/agent-telemetry/`, `scripts/check_otel_event_delivery.py` and the "Why they stay" row `SessionStart token_trend hook …`; in the `llm-security.md` row drop "content-logging deny flags" (design D3 removes them). `tests/test_otel_project_label.py` docstring: drop the reference to the deleted `test_claude_otel_assets.py`, keeping why the file stays (per-adopter setting). `information-architecture.md` "Declared charge ≠ paid charge" and the `tests/test_always_load_budget.py` docstring: replace the `token_trend.py` sentence with "paid charge is not measured for this repository (coverage gap AU)". ADR-0006 and ADR-0010: front-matter `status: "deprecated"`, bodies unchanged (design D4).
- [ ] 3.4 Verify the whole set: `git grep -nE "token_trend|check_otel_event_delivery|signal-catalogue|otel\.env\.example|observability/|test_claude_otel_assets|otel-delivery" -- ':!openspec/changes' ':!docs/adr'` prints nothing; `python -m pytest tests/test_otel_project_label.py tests/test_always_load_budget.py tests/test_doc_links.py tests/test_doc_headers.py tests/test_adr_records.py -q` passes. Commit with the ticks of 2.1–3.4.

## 4. Verify

- [ ] 4.1 Run `/simplify` without an argument and commit its edits, if any.
- [ ] 4.2 Run `openspec validate --strict --all`. Verify: exit 0.
- [ ] 4.3 Run the declared `test`, `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 5. Deliver

- [ ] 5.1 Run `agent-process archive_change delete-local-telemetry-stack`, then `gh pr create --title "chore: delete-local-telemetry-stack" --body-file <report>`; the report names #642 as a plain reference, carries the scenario map, and links the archived `design.md` (D1 lost proofs, D2 widened AU) for why #642's "plugin's dashboard shows it" line is recorded as gap AU rather than met.
- [ ] 5.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
| --- | --- |
| — | n/a: `skip_specs: true`, no delta scenarios |
