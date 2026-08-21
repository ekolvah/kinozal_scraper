# Project map — which file answers which question

**Question this document answers:** Which project file answers which question.

This is the complete navigation index. Do not add content that does not answer that navigation
question. The policy that decides where knowledge belongs is
[Information architecture](information-architecture.md); this file links to it instead of
repeating it.

This is an index, not content: keep one line per file and do not copy a file's contents here. The
only exception is `docs/adr/`, which is indexed by directory because it grows one record per
decision; a per-record map would diverge on the next record.

## File map

### `.claude/` and root instructions

| File | Question answered | Single-responsibility? |
|---|---|---|
| `~/.claude/CLAUDE.md` (global, outside the repository) | Cross-project material (generic mindset for non-repository projects). Repository mirror of the operational mindset = `.claude/rules/mindset.md` | ✅ |
| `CLAUDE.md` (project) | Mix: what the app does + Windows pitfalls + PR-workflow summary + architecture-document index | ❌ kitchen-sink |
| `docs/architecture/agent-process.md` | Agent-neutral roles, issue hand-off, deterministic gates, and adapter contract | ✅ |
| `.claude/rules/testing.md` | Operational test-writing checklist (RED-first/doubles/level/ci_check) — path-scoped `tests/**`, links to §I/§II | ✅ |
| `.claude/rules/mindset.md` | Claude-harness token tactics in the main session plus pointers to the objective function/principles/process (holds no canon) — always-load | ✅ |
| `.claude/commands/plan.md` | Claude planner adapter: interface to [`agent-process.md` §Planner runbook](agent-process.md#planner-runbook) (does not duplicate steps) | ✅ |
| `.claude/commands/implement.md` | Claude implementer/fixer adapter, invoked as `/implement N`: interface to [`agent-process.md` §Deterministic delivery flow](agent-process.md#deterministic-delivery-flow) and [§Review-gate verdicts](agent-process.md#review-gate-verdicts). Carries **only** harness specifics (foreground invocation of long commands, `Edit`/`Write` instead of heredoc); does not duplicate steps, exit codes, or git prohibitions — its former fat version was removed precisely for duplication, so `_SHARED_GATE_DEFINITIONS` guards the shim shape (#444, #473) | ✅ |
| `.agents/skills/plan-issue/` | Codex planner adapter, invoked as `$plan-issue #N`; same runbook, performs the discovery capture and the architect review itself | ✅ |
| `.agents/skills/implement-issue/` | Codex implementer/fixer adapter, invoked as `$implement-issue #N` | ✅ |
| `.claude/agents/architect-reviewer.md` | Plan-reviewer persona; reads its contract (what to check; coverage-first finding format: grade, do not filter, #392) and objective function **from the canon** [`agent-process.md` §Architect review contract](agent-process.md#architect-review-contract) (the subagent does not load always-load rules; it reads them itself and retains no copy). Model/`effort` are pinned; policy and pin boundaries are in [`ci-agent-review.md` §Model pinning](ci-agent-review.md#model-pinning-and-what-a-stale-pin-looks-like), with guard `tests/test_agent_frontmatter.py` | ✅ |
| `.claude/agents/discovery.md` | Claude `discovery` carrier persona; reads the observation bounds, capture route, and completion check **from the canon** [`agent-process.md` §Discovery runbook](agent-process.md#discovery-runbook) and retains no copy. Carries only the adapter interface: the `discovery: Claude discovery subagent` provenance line, and the rule that the planner — not this role — writes the returned block into the issue (#517) | ✅ |
| `.claude/settings.json`, `.codex/hooks.json`, `scripts/agent_policy.py` | Local deny policy for Claude and Codex; branch protection remains final | ✅ |
| `.claude/settings.local.json` (gitignored) | Personal mode + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, personal) |

### `docs/architecture/`

| File | Question answered | Single-responsibility? |
|---|---|---|
| `principles.md` | Mix: §I–VII principles (partly RUNTIME: §III Delivery, §IV Visibility) + Quality Gates + Governance (workflow delegated to `agent-process.md`) | ❌ runtime principles + development process together |
| `information-architecture.md` | Where repository knowledge belongs and how documentation navigation is organized | ✅ |
| `project-map.md` (this file) | Which project file answers which question | ✅ |
| `runtime.md` | What exists at runtime and how it connects: available pipelines, Protocol boundaries, generic data flow, and modules that consciously bypass the generic pattern (Telethon-direct). Breadth, not depth | ✅ |
| `pipeline.md` | How **one** run is structured and behaves: extraction layers, `extract_from_*` → `NormalizedItem` contracts, "a new source = configuration, not code", error policy, notification templates, macros, trailers, **and fetch behaviour** (HTML source configuration, Kinozal mirror fallback — #418) | ✅ |
| `storage.md` | Storage Protocol + implementations, DI, EAFP sheet creation and schema validation, dedupe-key lookup, row schema, column invariants, write order | ✅ |
| `testing.md` | How quality is guaranteed: test levels, bug taxonomy, what to mock (links to `principles.md §II`, does not duplicate it). Strategy, not exceptions | ✅ |
| `coverage-gaps.md` | Stable-ID router for consciously accepted test gaps | ✅ |
| `coverage-gaps-ingestion.md` | Accepted gaps in source ingestion, retrieval, and transport | ✅ |
| `coverage-gaps-enrichment.md` | Accepted gaps in enrichment and content selection | ✅ |
| `coverage-gaps-quality-gates.md` | Accepted gaps in repository quality gates | ✅ |
| `coverage-gaps-runtime.md` | Accepted gaps in runtime behavior | ✅ |
| `coverage-gaps-agent-tooling.md` | Accepted gaps in agent tooling and observability | ✅ |
| `coverage-gaps-modules.md` | Modules without dedicated tests and their accepted rationale | ✅ |
| `ci.md` | Router for CI and quality-gate documentation | ✅ |
| `ci-local.md` | Local pre-commit quality gate | ✅ |
| `ci-workflow.md` | `ci.yml` checks, lint ratchets, and document guards | ✅ |
| `ci-branch-protection.md` | Required GitHub status checks | ✅ |
| `ci-agent-review.md` | Agent-review workflow and model-pin policy | ✅ |
| `ci-production.md` | Scheduled production workflow | ✅ |
| `ci-tooling-decisions.md` | Consciously rejected CI tooling | ✅ |
| `operations.md` | How the production run and maintainer-operated services are run: schedule and step order, environment variables and secrets, failure isolation (#245) and alerting (#310), operator runbooks (`TELETHON_SESSION` rotation), patient Soldout retries, Claude Code direct OTel, and the Codex → Alloy → Grafana setup, verification, rollback, and baseline review (#471, #472). Took the runtime half of `ci.md` (#418) | ✅ |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts / call observability (token+latency `llm_call` log + Phoenix development recipe, #145) | ✅ |
| `llm-security.md` | Enricher LLM threats (OWASP LLM Top 10 → safeguards/residual) plus Claude/Codex development-telemetry trust boundaries: prompt-injection fence, output escaping, honest blast radius, content-logging deny flags, loopback Alloy, and external metadata exposure (#308, #471, #472) | ✅ |
| `agent-process-export.md` | Which files of the agentic-process contract can move to a new project as-is, templated, or not at all; the `#N`-citation and exported-payload-size decisions. Canonical entry point, together with [ADR-0011](../adr/0011-agentic-process-distribution-mechanism.md), for how the agentic process is shared between projects | ❌ manifest + 2 decisions |

### `docs/adr/`

| File | Question answered | Single-responsibility? |
|---|---|---|
| `docs/adr/` (whole directory) | Why this decision was made and what was rejected: MADR 4.0.0 records with stable `NNNN` IDs, append-only (a changed decision = a new record with `superseded by`). Entry is the cost-of-change filter (§Canonical-home). `template.md` is the verbatim upstream template; `tests/test_adr_records.py` is the guard | ✅ |

### Process scripts and templates

| File | Question answered |
|---|---|
| `evidence/` (Git-ignored) | Working-tree-only planning captures retained until merge; the durable compressed record and fixture boundary are canonical in [`agent-process.md` §Issue contract](agent-process.md#issue-contract) |
| `scripts/validate_issue_sections.py` + `scripts/check_orphan_scope.py` + `.agents/orchestration/change-classes.yaml` | Verifies the section set **resolved from the issue's one type label** (base `REQUIRED_SECTIONS` ± the class row) plus the `Agent handoff`, `Architect review`, and discovery-section field contracts — `Evidence` for `bug`, `Prior art` with its reuse/build verdict for every other class — and fails when an issue carries zero or several type labels; on a passing issue, prints the resolved class with its derived RED obligation and surfaces the non-blocking reminder for an explicit `Out of scope` follow-up without `#N` or `wontfix`/`YAGNI` (#368). Gate for planner and implementer adapters; the reminder itself never changes the exit code. The planner-only `--mark-planned` moves the issue's Project 1 Status to `Planned` on a passing validation and warns without changing the exit code when the board write fails; the unflagged implementer call stays read-only (#519). The discovery-only `--evidence-only` judges the `Evidence` block alone and ignores the sections the planner has not written yet, so the `discovery` role terminates on an exit code; `--body-file` points it at the candidate block on disk, because the role may not edit the issue and the block is not in the body when it finishes. Its `discovery: <carrier>` provenance line resolves against the same role catalogue as the `reviewer:` marker (#517) |
| `scripts/capture_kinozal_fixture.py` + `scripts/capture_external_fixture.py` + `scripts/check_fixture_ratchet.py` | Reproducible Evidence capture through the Kinozal production fetcher or narrow read-only GitHub, Telegram, Gemini, Sheets, and stdin routes; the ratchet rejects new external-HTML parser tests that construct their input inline (#509). Canonical routing and repository-safety contract: [`agent-process.md` §Issue contract](agent-process.md#issue-contract) |
| `scripts/agent_orchestrator.py` + `.agents/orchestration/roles.yaml` | Read-only control plane: a single role catalogue, evidence-based next action, and bounded escalation; does not invoke providers or replace required gates |
| `scripts/review_gate.py` | Whether the PR review/fix cycle continues: reads required contexts at the current head and the number of already reviewed heads, then returns an exit-code verdict (`0` ready-for-human, `10` fix-blocking, `20` escalate, `30` review-pending; `2` is `gh`/capture failure, not a verdict). It changes and posts nothing. The rule "fix only blocking; `should-fix` is the maintainer's decision" was twice ignored as prose (#458, #465), so it now has an exit code rather than a list item ([principles.md §Scripts over instructions](principles.md#scripts-over-instructions)). Severity comes from the already calculated `agent-review` check run, not parsed review body; budget is `fixer.max_runs` from the role catalogue. Prose home: [`agent-process.md` §Review-gate verdicts](agent-process.md#review-gate-verdicts) (#467) |
| `scripts/issue_branch.py` / `scripts/new_branch.py` | Create an `issue-N-*` branch from fresh origin/main, then move the issue's Project 1 Status to `In Progress` — after the checkout, because the branch is what "in progress" means; a failed board write warns and does not fail branch creation (#519) |
| `scripts/set_issue_priority.py` | Set issue priority (the Priority field in GitHub Project 1) through `gh project item-add`+`item-edit` with embedded Project/field/option IDs; read-only `--check` verifies the exact issue URL and non-empty High/Medium/Low before branch creation. The agent invokes it under the `agent-process.md` contract (asked for priority → setter; implementer preflight → checker). The mechanism moved memory→repository (#351) |
| `scripts/set_issue_status.py` | Move an issue's Status field in Project 1 to `Planned` or `In Progress` through the same `gh project item-add`+`item-edit` pair and embedded Project/field/option IDs as the priority script; `Todo`/`Done` raise before any `gh` call, because the built-in Project automations own them. `set_status` raises instead of exiting — its two callers (`validate_issue_sections.py --mark-planned`, `issue_branch.py`) keep their own exit codes and downgrade a board failure to a stderr warning. It imports nothing from the repository, so both documented CLIs still work with only `scripts/` on `sys.path`; the duplicated Project constants are guarded by `tests/test_set_issue_status.py` (#519) |
| `scripts/check_red.py` | Whether tests are RED before GREEN (the TDD-step contract) |
| `scripts/open_pr.py` | Create a PR with guaranteed `Closes #N` in its body + post-verification of `closingIssuesReferences` (otherwise exit 1, §IV), so the PR reliably auto-closes the issue on squash merge (#320, precedent #319→#140). Preflight makes the correct path cheap; enforcement is `verify_pr_link.py` |
| `scripts/update_pr_body.py` | Explicitly replace the report of an existing delivery PR: derives the issue from the `issue-N-*` head branch, retains exactly one canonical `Closes #N` line, passes arbitrary UTF-8 Markdown through `--body-file`, and verifies `closingIssuesReferences` after edit. An ordinary `open_pr.py` rerun does not replace the body (#456) |
| `scripts/verify_pr_link.py` | CI gate (workflow `pr-link.yml`): a PR from an `issue-N` branch must close its issue, otherwise the job is red → required check blocks merge. A separate workflow (not `ci.yml`) because it also triggers on `edited` (a body edit removes `Closes #N` → recheck), without running heavy `quality` for a description edit. Agent-neutral backstop for `open_pr.py` (reuses its pure functions); enforcement through a gate, not prose (#320) |
| `scripts/check_branch_protection.py` | Compares "declared in repository ↔ configured in GitHub" required status checks for branch `main`; the **machine canon of composition** is its own `REQUIRED_CONTEXTS`/`NOT_REQUIRED`, to which documentation links. Always prints the actual list; exit `1` is drift and `2` is tool failure (not "no drift"); `--allow-drift "<reason>"` expresses intentional temporary drift with a printed reason rather than bypassing with `--no-verify` (#458). Called by `.githooks/pre-push` before `ci_check.py`; not put in CI because `GITHUB_TOKEN` lacks `administration` scope (#436). No separate controller-PR gate is needed: such a PR passes the same required contexts as any other (#483). Prose home for consequences: [`ci-branch-protection.md` §Required status checks](ci-branch-protection.md#required-status-checks-branch-protection) |
| `scripts/ci_check.py` | Local pre-commit/pre-push quality gate (mirror of the CI job) |
| `scripts/eval_trailers.py` | Trailer-selection evaluation harness with three scorecards: `TrailerStrategy` (YouTube pick), `evaluate_delivery` (production `select_trailer`, the user-visible result, #379), and `evaluate_tmdb` (TMDB source). It uses a frozen golden set with offline Hit/Wrong/Miss outcomes against `correct`, plus `--record`/`--record-tmdb`/`--update-baseline`. The **gate** is the per-film delivery result in `tests/fixtures/trailer_baseline.json`, enforced by `tests/test_eval_baseline.py` rather than a `ci_check` CHECKS entry. The dataset tests both finding an accepted trailer (`correct`) and rejecting verified wrong candidates (`trap`, #380). Deep dive: `testing.md#eval-harness--trailer-selection` (#139, #329, #379, #380) |
| `scripts/eval_summarizer.py` | RAGAS evaluation of `summary_ru`: faithfulness and answer relevancy against a frozen golden set instead of a `response_pattern` format vibe check. The LLM-as-judge metric is live/API-gated for development, not CI; the `_evaluate_dataset` boundary is doubled and pure seams are tested. RAGAS is a development-only dependency. Deep dive: `testing.md#eval-harness--summarizer-faithfulness` (#347) |
| `scripts/hooks.py`, `scripts/codex_hooks.py` | Shared post-edit checks plus the Claude and Codex hook adapters; ruff feedback and pip-compile reminder complement `ci_check.py`. `pre-bash` and `pre-read` (Claude `PreToolUse`, matchers `Bash` and `Read`) both delegate to `scripts/navigation_policy.py` |
| `scripts/navigation_policy.py` | Token-economy policy for both routes into the filesystem. **Shell** (#485): decides that a stage reads a file — by counting file operands, so `grep FILE` is denied while `cmd \| grep` is not. **`Read`** (#534): measures the bytes of the slice the tool will return against a 28 000-byte budget and hands back the `limit` that fits. Both denials **name the replacement call**. Separate carrier from the security policy `agent_policy.py`, and fails **open**: it claims only that a cheaper route exists |
| `scripts/token_trend.py` | Measures **observed raw-token** development-session use from Claude Code transcripts: input, output, cache-read and cache-creation remain separate; their sum is per-branch/per-turn trend input. Its same single pass also folds distinct tool blocks per assistant request and classifies same-session `Read` repeats by the exact window or another window. It detects rolling-window growth by median plus a measured absolute floor. A `SessionStart` hook in `.claude/settings.json` is quiet normally and **always** exits 0 so the hook does not emit its own alert; `--report` prints the table. Because transcripts are retained for only 30 days (`cleanupPeriodDays`), branch aggregates survive in local `token_ledger.jsonl`; schemas 1/2 retain reconstructible raw fields but interaction metrics, like legacy sidechain tokens, are unavailable rather than zero. Complements the static `test_always_load_budget.py` ratchet: that guards declared context, this measures observed history (#464, #565) |
| `observability/claude-code/` | Values-free Claude Code direct-OTel template and live-captured signal/attribute catalogue. Credentials stay outside git; operation is in `operations.md`, privacy in `llm-security.md`, and the choice in ADR-0006 (#471) |
| `observability/codex/`, `observability/agent-telemetry/`, `scripts/check_codex_otel_config.py` | Values-free Codex and Alloy templates, deterministic metrics-only/loopback pipeline guard, live-captured Codex name catalogue, and the shared importable Grafana dashboard. Operation is in `operations.md`, privacy in `llm-security.md`, and the bridge choice in ADR-0007 (#472) |
| `scripts/check_otel_event_delivery.py` | Operator-invoked, read-only, thresholdless check that both halves of the Claude Code telemetry signal arrive: reads metrics and events over one window through the Grafana datasource proxy and exits non-zero when either half is missing while the other arrived. Why the repository may hold live credentials at all: ADR-0010; operation is in `operations.md#verify-and-import`; the coverage it moved is `coverage-gaps-agent-tooling.md` §AN (#542) |
| `.github/workflows/ci.yml` | Quality job on PR/push (must mirror `ci_check.py`) |
| `.importlinter` | §II protocol boundaries as a machine contract (the `imports` gate in `ci_check`): dependency direction + adapter-no-auth; deep dive `ci-workflow.md` (#234) |

### Project source files

**Each file's module docstring answers its own question** and is the JIT canonical
description when the file is opened; ruff `D100`/`D104`/`D419` in `check_lint`
guarantees presence (#253). This section is only a **concern-to-file router** with
deep-dive pointers that individual docstrings lack. Tests and helpers are omitted.

| Concern | Files | Deep dive |
|---|---|---|
| Pipeline layer (core and contracts) | `src/kinozal_scraper/generic_pipeline.py`, `src/kinozal_scraper/pipeline_config.py` | `pipeline.md` (config → `principles.md §VI`) |
| Per-source extraction and normalization | `src/kinozal_scraper/kinozal_pipeline.py`, `src/kinozal_scraper/steam_pipeline.py`, `src/kinozal_scraper/soldout_pipeline.py`, `src/kinozal_scraper/github_popular_pipeline.py`, `src/kinozal_scraper/github_trending_pipeline.py` | `pipeline.md` |
| Boundaries (outward Protocol boundaries) | `src/kinozal_scraper/sheets_storage.py` (storage); `src/kinozal_scraper/telegram_notifier.py` / `src/kinozal_scraper/telegram_summarizer.py` (notify); `src/kinozal_scraper/alerting.py` (canonical operator-reporting home: `.run/technical_alert_sent`, per-source `report_failures` alerts #310, and `publish_run_summary` metrics in logs and GitHub Step Summary #459); `src/kinozal_scraper/gemini_enricher.py` / `src/kinozal_scraper/TelegramChannelSummarizer.py` (Gemini); `src/kinozal_scraper/llm_observability.py` (shared `llm_call` breadcrumb for both live Gemini call sites: `usage_metadata` tokens and latency, visibly degraded under §IV, #145); `src/kinozal_scraper/http_fetch.py` (shared HTML fetch via curl_cffi impersonation to bypass Cloudflare TLS fingerprinting #217; per-attempt anti-bot diagnostics from `describe_block`, #358) | `storage.md` · `runtime.md` · `gemini.md` |
| Trailer selection (retrieval → selection) | `src/kinozal_scraper/youtube.py` (retrieval: `search_candidates` unions Russian and original-title queries into `list[Candidate]`, #140); `src/kinozal_scraper/kinozal_pipeline.py` (`build_film_profile` prepares the richer details.php-backed `FilmProfile` for the harness; `enrich_with_trailer` is the **production composition #144**, using a lightweight title/year profile through `select_trailer`, the shared production/evaluation entry point from #379; Russian preference closes #315 and Gemini is not on the hot path); `src/kinozal_scraper/trailer_strategy.py` (selection data types, `TrailerStrategy` Protocol, baseline `FirstResultStrategy` #139, and language-aware `HeuristicStrategy` #141); `src/kinozal_scraper/trailer_picker_llm.py` (strategy A: Gemini structured-output `LLMTrailerStrategy` and `GeminiJsonGenerator`, #142); `src/kinozal_scraper/trailer_picker_embeddings.py` (strategy B: cosine-and-threshold `EmbeddingTrailerStrategy` and `GeminiEmbedder`, #143); `src/kinozal_scraper/tmdb_trailer.py` (alternative TMDB metadata source with pure `pick_trailer` and `TmdbClient` DI, evaluated offline but not connected to production, #329) | `pipeline.md#trailer-retrieval-and-selection` · `testing.md#eval-harness--trailer-selection` |
| Shared HTTP policy (not a Protocol boundary) | `src/kinozal_scraper/http_retry.py` is the single home for transient-error classification across curl_cffi and stdlib requests, with **two** status-code sets. Only Cloudflare-protected HTML transport retries 403/429 (#358); for JSON APIs those responses are rate limits with their own reset windows, and repeated GitHub API requests can get the integration banned (#365) | `coverage-gaps-ingestion.md` **M**/**M2**/**M3** |
| Utilities | `src/kinozal_scraper/text_utils.py` | — |

---

Residual open debt is tracked in [issue #177](https://github.com/ekolvah/kinozal_scraper/issues/177),
an instance of the [documentation scope rule](information-architecture.md#what-documentation-describes-current-state-not-history-or-ideas):
backlog and status tracking belong in issues, not `docs/`; completed items remain in their PR history.
