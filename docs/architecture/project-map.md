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
| `~/.claude/CLAUDE.md` (global, outside the repository) | Cross-project material (generic mindset for non-repository projects) | ✅ |
| `CLAUDE.md` (project) | Mix: what the app does + Windows pitfalls + PR-workflow summary + architecture-document index | ❌ kitchen-sink |
| `.claude/rules/testing.md` | Operational test-writing checklist (RED-first/doubles/level/ci_check) — path-scoped `tests/**`, links to §I/§II | ✅ |
| `.claude/settings.json` | `SessionStart` hooks and local deny policy (`permissions.deny`); the ruleset remains final | ✅ |
| `.claude/settings.local.json` (gitignored) | Personal mode + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, personal) |

### `docs/architecture/`

| File | Question answered | Single-responsibility? |
|---|---|---|
| `information-architecture.md` | Where repository knowledge belongs and how documentation navigation is organized | ✅ |
| `project-map.md` (this file) | Which project file answers which question | ✅ |
| `runtime.md` | What exists at runtime and how it connects: available pipelines, Protocol boundaries, generic data flow, and modules that consciously bypass the generic pattern (Telethon-direct). Breadth, not depth | ✅ |
| `pipeline.md` | How **one** run is structured and behaves: extraction layers, `extract_from_*` → `NormalizedItem` contracts, "a new source = configuration, not code", error policy, notification templates, macros, trailers, **and fetch behaviour** (HTML source configuration, Kinozal mirror fallback — #418) | ✅ |
| `storage.md` | Storage Protocol + implementations, DI, EAFP sheet creation and schema validation, dedupe-key lookup, row schema, column invariants, write order | ✅ |
| `testing.md` | How quality is guaranteed: test levels, bug taxonomy, what to mock (links to [the plugin's `principles.md` §II](https://github.com/ekolvah/agent-process-distribution/blob/main/skills/agent-process/principles.md#ii-protocol-boundaries-with-dependency-injection), does not duplicate it). Strategy, not exceptions | ✅ |
| `coverage-gaps.md` | Stable-ID router for consciously accepted test gaps | ✅ |
| `coverage-gaps-ingestion.md` | Accepted gaps in source ingestion, retrieval, and transport | ✅ |
| `coverage-gaps-enrichment.md` | Accepted gaps in enrichment and content selection | ✅ |
| `coverage-gaps-quality-gates.md` | Accepted gaps in repository quality gates | ✅ |
| `coverage-gaps-runtime.md` | Accepted gaps in runtime behavior | ✅ |
| `coverage-gaps-agent-tooling.md` | Accepted gaps in agent tooling and observability | ✅ |
| `coverage-gaps-modules.md` | Modules without dedicated tests and their accepted rationale | ✅ |
| `ci.md` | Router for CI and quality-gate documentation | ✅ |
| `ci-local.md` | Local pre-commit quality gate | ✅ |
| `ci-workflow.md` | `agent-process.yml` quality checks, lint ratchets, and document guards | ✅ |
| `ci-production.md` | Scheduled production workflow | ✅ |
| `ci-tooling-decisions.md` | Consciously rejected CI tooling | ✅ |
| `operations.md` | How the production run and maintainer-operated services are run: schedule and step order, environment variables and secrets, failure isolation (#245) and alerting (#310), operator runbooks (`TELETHON_SESSION` rotation), patient Soldout retries, and Claude Code direct OTel (#471). Took the runtime half of `ci.md` (#418) | ✅ |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts / call observability (token+latency `llm_call` log + Phoenix development recipe, #145) | ✅ |
| `llm-security.md` | Enricher LLM threats (OWASP LLM Top 10 → safeguards/residual) plus Claude Code development-telemetry trust boundaries: prompt-injection fence, output escaping, honest blast radius, content-logging deny flags, and external metadata exposure (#308, #471) | ✅ |

### `docs/adr/`

| File | Question answered | Single-responsibility? |
|---|---|---|
| `docs/adr/` (whole directory) | Why this decision was made and what was rejected: MADR 4.0.0 records with stable `NNNN` IDs, append-only (a changed decision = a new record with `superseded by`). Entry is the cost-of-change filter ([`information-architecture.md`](information-architecture.md#decision-records)). `template.md` is the verbatim upstream template; `tests/test_adr_records.py` is the guard | ✅ |

### Process scripts and configuration

| File | Question answered |
|---|---|
| `evidence/` (Git-ignored) | Working-tree-only planning captures retained until merge; the durable compressed record and the fixture boundary are canonical in [`testing.md`](testing.md#external-data-capture-routes) |
| `scripts/capture_kinozal_fixture.py` + `scripts/capture_external_fixture.py` + `scripts/check_fixture_ratchet.py` | Reproducible fixture capture through the Kinozal production fetcher or narrow read-only GitHub, Telegram, Gemini, Sheets, and stdin routes; the ratchet rejects new external-HTML parser tests that construct their input inline (#509). Canonical routing and repository-safety contract: [`testing.md`](testing.md#external-data-capture-routes) |
| `scripts/ci_check.py` | The quality gate: the pre-push hook runs it whole, the plugin's CI runs each registry entry via `--only` |
| `scripts/eval_trailers.py` | Trailer-selection evaluation harness with three scorecards: `TrailerStrategy` (YouTube pick), `evaluate_delivery` (production `select_trailer`, the user-visible result, #379), and `evaluate_tmdb` (TMDB source). It uses a frozen golden set with offline Hit/Wrong/Miss outcomes against `correct`, plus `--record`/`--record-tmdb`/`--update-baseline`. The **gate** is the per-film delivery result in `tests/fixtures/trailer_baseline.json`, enforced by `tests/test_eval_baseline.py` rather than a `ci_check` CHECKS entry. The dataset tests both finding an accepted trailer (`correct`) and rejecting verified wrong candidates (`trap`, #380). Deep dive: `testing.md#eval-harness--trailer-selection` (#139, #329, #379, #380) |
| `scripts/eval_summarizer.py` | RAGAS evaluation of `summary_ru`: faithfulness and answer relevancy against a frozen golden set instead of a `response_pattern` format vibe check. The LLM-as-judge metric is live/API-gated for development, not CI; the `_evaluate_dataset` boundary is doubled and pure seams are tested. RAGAS is a development-only dependency. Deep dive: `testing.md#eval-harness--summarizer-faithfulness` (#347) |
| `scripts/token_trend.py` | Measures **observed raw-token** development-session use from Claude Code transcripts: input, output, cache-read and cache-creation remain separate; their sum is per-branch/per-turn trend input. Its same single pass also folds distinct tool blocks per assistant request and classifies same-session `Read` repeats by the exact window or another window. It detects rolling-window growth by median plus a measured absolute floor. A `SessionStart` hook in `.claude/settings.json` is quiet normally and **always** exits 0 so the hook does not emit its own alert; `--report` prints the table. Because transcripts are retained for only 30 days (`cleanupPeriodDays`), branch aggregates survive in local `token_ledger.jsonl`; schemas 1/2 retain reconstructible raw fields but interaction metrics, like legacy sidechain tokens, are unavailable rather than zero. Complements the static `test_always_load_budget.py` ratchet: that guards declared context, this measures observed history (#464, #565) |
| `observability/claude-code/` | Values-free Claude Code direct-OTel template and live-captured signal/attribute catalogue. Credentials stay outside git; operation is in `operations.md`, privacy in `llm-security.md`, and the choice in ADR-0006 (#471) |
| `observability/agent-telemetry/` | Importable Grafana dashboard over the Claude Code signal catalogue (#471) |
| `scripts/check_otel_event_delivery.py` | Operator-invoked, read-only, thresholdless check that both halves of the Claude Code telemetry signal arrive: reads metrics and events over one window through the Grafana datasource proxy and exits non-zero when either half is missing while the other arrived. Why the repository may hold live credentials at all: ADR-0010; operation is in `operations.md#verify-and-import`; the coverage it moved is `coverage-gaps-agent-tooling.md` §AN (#542) |
| `.github/workflows/agent-process.yml`, `.github/workflows/agent-review.yml` | Plugin-managed (installer-rendered, do not edit) quality and review callers, check runs `agent-process / *` and `agent-review / *`; the two checks the default-branch ruleset requires (`ci.md`, ADR-0013) |
| `.github/agent-process-quality.json` | The plugin's quality declaration: `setup`, `test` (`ci_check.py`), `checks` (`ci_check.py --list-checks`); `TestStepParity` pins it to the registry (#597) |
| `.pre-commit-config.yaml` | Plugin-managed block: the pre-push `quality` hook that runs the declared `test`; outside the block, the repository's ruff hooks (`pre-commit` stage, the `lint` check and edit-time lint); setup in `ci-local.md` |
| `.github/dependabot.yml` | Plugin-managed Dependabot configuration |
| `openspec/`, `.claude/commands/opsx/`, `.claude/skills/openspec-*` | OpenSpec workspace and its `/opsx:*` commands and skills, installed by the agent-process plugin (its `agent-process` skill is the process); do not edit |
| `.claude/agent-process-check.py` | Plugin-managed `SessionStart` check that the agent-process plugin is installed at the pinned version |
| `.importlinter` | §II protocol boundaries as a machine contract (the `imports` gate in `ci_check`): dependency direction + adapter-no-auth; deep dive `ci-workflow.md` (#234) |

### Repository-owned process files that stay

After the agent-process plugin took over the shared process (#612), these process files remain
the repository's own; each row names why or the issue that owns its future.

| Files | Why they stay |
|---|---|
| `CLAUDE.md` | Application context and Windows pitfalls no plugin can know |
| `.claude/rules/testing.md`, fixture capture scripts, `scripts/check_fixture_ratchet.py` | Repository test checklist and external-data capture; owned by #613 |
| `scripts/ci_check.py`, `ci*.md` | The repository's quality gate, which the plugin runs as the declared `test` |
| `.pre-commit-config.yaml` hooks outside the plugin block | Ruff's only pin and its config, read by the plugin's edit-time lint (#628) |
| `SessionStart` `token_trend` hook, `scripts/token_trend.py`, `observability/*`, `scripts/check_otel_event_delivery.py` and their tests | Development telemetry; owned by #614 track 2 |
| `permissions.deny` in `.claude/settings.json`, `tests/test_settings_deny.py` | Local security carrier until #632 |
| `tests/test_doc_headers.py`, `tests/test_doc_links.py`, `tests/test_adr_records.py`, `tests/test_always_load_budget.py`, `tests/test_repo_layout.py`, `information-architecture.md` | Doc guards and the policy they enforce |
| `coverage-gaps-*.md`, `docs/adr/` | Repository decision records and accepted gaps |

### Project source files

**Each file's module docstring answers its own question** and is the JIT canonical
description when the file is opened; ruff `D100`/`D104`/`D419` in `check_lint`
guarantees presence (#253). This section is only a **concern-to-file router** with
deep-dive pointers that individual docstrings lack. Tests and helpers are omitted.

| Concern | Files | Deep dive |
|---|---|---|
| Pipeline layer (core and contracts) | `src/kinozal_scraper/generic_pipeline.py`, `src/kinozal_scraper/pipeline_config.py` | `pipeline.md` (config → [`runtime.md`](runtime.md#configuration)) |
| Per-source extraction and normalization | `src/kinozal_scraper/kinozal_pipeline.py`, `src/kinozal_scraper/steam_pipeline.py`, `src/kinozal_scraper/soldout_pipeline.py`, `src/kinozal_scraper/github_popular_pipeline.py`, `src/kinozal_scraper/github_trending_pipeline.py` | `pipeline.md` |
| Boundaries (outward Protocol boundaries) | `src/kinozal_scraper/sheets_storage.py` (storage); `src/kinozal_scraper/telegram_notifier.py` / `src/kinozal_scraper/telegram_summarizer.py` (notify); `src/kinozal_scraper/alerting.py` (canonical operator-reporting home: `.run/technical_alert_sent`, per-source `report_failures` alerts #310, and `publish_run_summary` metrics in logs and GitHub Step Summary #459); `src/kinozal_scraper/gemini_enricher.py` / `src/kinozal_scraper/TelegramChannelSummarizer.py` (Gemini); `src/kinozal_scraper/llm_observability.py` (shared `llm_call` breadcrumb for both live Gemini call sites: `usage_metadata` tokens and latency, visibly degraded under §IV, #145); `src/kinozal_scraper/http_fetch.py` (shared HTML fetch via curl_cffi impersonation to bypass Cloudflare TLS fingerprinting #217; per-attempt anti-bot diagnostics from `describe_block`, #358) | `storage.md` · `runtime.md` · `gemini.md` |
| Trailer selection (retrieval → selection) | `src/kinozal_scraper/youtube.py` (retrieval: `search_candidates` unions Russian and original-title queries into `list[Candidate]`, #140); `src/kinozal_scraper/kinozal_pipeline.py` (`build_film_profile` prepares the richer details.php-backed `FilmProfile` for the harness; `enrich_with_trailer` is the **production composition #144**, using a lightweight title/year profile through `select_trailer`, the shared production/evaluation entry point from #379; Russian preference closes #315 and Gemini is not on the hot path); `src/kinozal_scraper/trailer_strategy.py` (selection data types, `TrailerStrategy` Protocol, baseline `FirstResultStrategy` #139, and language-aware `HeuristicStrategy` #141); `src/kinozal_scraper/trailer_picker_llm.py` (strategy A: Gemini structured-output `LLMTrailerStrategy` and `GeminiJsonGenerator`, #142); `src/kinozal_scraper/trailer_picker_embeddings.py` (strategy B: cosine-and-threshold `EmbeddingTrailerStrategy` and `GeminiEmbedder`, #143); `src/kinozal_scraper/tmdb_trailer.py` (alternative TMDB metadata source with pure `pick_trailer` and `TmdbClient` DI, evaluated offline but not connected to production, #329) | `pipeline.md#trailer-retrieval-and-selection` · `testing.md#eval-harness--trailer-selection` |
| Shared HTTP policy (not a Protocol boundary) | `src/kinozal_scraper/http_retry.py` is the single home for transient-error classification across curl_cffi and stdlib requests, with **two** status-code sets. Only Cloudflare-protected HTML transport retries 403/429 (#358); for JSON APIs those responses are rate limits with their own reset windows, and repeated GitHub API requests can get the integration banned (#365) | `coverage-gaps-ingestion.md` **M**/**M2**/**M3** |
| Utilities | `src/kinozal_scraper/text_utils.py` | — |

---

Residual open debt is tracked in [issue #177](https://github.com/ekolvah/kinozal_scraper/issues/177),
an instance of the [documentation scope rule](information-architecture.md#what-documentation-describes-current-state-not-history-or-ideas):
backlog and status tracking belong in issues, not `docs/`; completed items remain in their PR history.
