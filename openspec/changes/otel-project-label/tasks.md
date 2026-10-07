## 0. Delivery start

- [x] 0.1 Run `agent-process start_change otel-project-label --planner Claude --implementer Claude` (tracking issue 611); enter `.claude/worktrees/otel-project-label` with `EnterWorktree` and run every later task there. Verify: the script prints the branch and worktree and #611 shows In Progress.

## 1. RED

- [x] 1.1 Create `tests/test_otel_project_label.py` (module docstring names #611 and why the file is separate from `test_claude_otel_assets.py`, design D2) with `test_settings_carry_project_pairs`: read `.claude/settings.json`, split `env.OTEL_RESOURCE_ATTRIBUTES` on `,`, assert every entry holds exactly one `=`, assert the pairs contain both required ones (containment, no helper).
- [x] 1.2 Run `agent-process check_red tests/test_otel_project_label.py::test_settings_carry_project_pairs`. Verify: it fails in the test body (missing `env` key); commit RED with the Group 1 ticks.

## 2. Implementation

- [ ] 2.1 Add `"env": {"OTEL_RESOURCE_ATTRIBUTES": "vcs.repository.name=ekolvah/kinozal_scraper,vcs.repository.url.full=https://github.com/ekolvah/kinozal_scraper"}` to `.claude/settings.json` (design D1). Verify: `python -m pytest tests/test_otel_project_label.py tests/test_token_trend.py -q` passes; commit.
- [ ] 2.2 `docs/architecture/operations.md`: "Verify and import" step 4 says the JSON is a temporary copy pending #617 / agent-process-distribution#308, `agwhkq` is intentionally not re-synced, and a plain import creates another dashboard (design D4).
- [ ] 2.3 `docs/architecture/project-map.md`: add a row to "Repository-owned process files that stay" for `env.OTEL_RESOURCE_ATTRIBUTES` in `.claude/settings.json` and `tests/test_otel_project_label.py` — per-adopter project label, plugin ADR 0026/0029, #611 (design D4). Verify: `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py tests/test_repo_layout.py -q` passes; commit with the ticks of 2.2–2.3.

## 3. Live verification

- [ ] 3.1 Ask the person to run one short session from the worktree in their own terminal (`claude -p "reply ok"`; the Bash tool has no `OTEL_*`, design D3), wait at least 60 s plus ingest lag, then read `count by (vcs_repository_name) (last_over_time(claude_code_token_usage_tokens_total[1h]))` (not `increase()`, design D3) through `/api/datasources/proxy/uid/grafanacloud-prom/api/v1/query` with the credential loaders of `scripts/check_otel_event_delivery.py`. Verify: a series with `vcs_repository_name="ekolvah/kinozal_scraper"`; keep the output for the PR report.
- [ ] 3.2 Add entry `AU` to `docs/architecture/coverage-gaps-agent-tooling.md`: no standing check guards the live label (settings `env` application is not reproduced-as-failing, not impossible); the 3.1 evidence with date and Claude Code version; the 3.1 query as the re-check; revisit trigger = a Claude Code upgrade or this project reappearing under `{}`; accepted, no owner of an exit-code check (design D3). In `docs/architecture/coverage-gaps.md` the ID range reads "`A` through `AU`" and the agent-tooling line lists `AU`. Verify: `python -m pytest tests/test_doc_links.py tests/test_doc_headers.py -q` passes; commit with the ticks of 3.1–3.2.

## 4. Verify

- [ ] 4.1 Run `openspec validate --strict --all`. Verify: exit 0.
- [ ] 4.2 Run `python scripts/ci_check.py` (one foreground call, `timeout: 600000`). Verify: exit 0.

## 5. Deliver

- [ ] 5.1 Run `agent-process archive_change otel-project-label`, then `gh pr create --title "chore: otel-project-label" --body-file <report>`; the report names #611 and #617 as plain references, carries the scenario map, the 3.1 query output, and that `agwhkq` re-sync stays with agent-process-distribution#308.
- [ ] 5.2 Run `agent-process wait_for_pr <PR>` and handle review threads per the Delivery section, at most three rounds.

## Scenario → test map

| Scenario | Test |
|---|---|
| Project attribution / Settings carry the project pairs | `tests/test_otel_project_label.py::test_settings_carry_project_pairs` |
| Project attribution / A session exports under the project label | n/a: live external state; verified once by task 3.1 (output in the PR report), standing gap ledgered as `AU` by task 3.2 |
