**Question this document answers:** which files of this repository's agentic-process contract
can move to a new project as-is, which need per-project parameterization, and which never leave —
the manifest consumed by the tracked copier template and the future Claude Code plugin.

The distribution mechanism this manifest feeds is decided in
[ADR-0011](../adr/0011-agentic-process-distribution-mechanism.md): copier for the two exported
layers below, the official Claude Code plugin marketplace for the Claude-adapter layer.
[`templates/agent-process/`](../../templates/agent-process/) is the reviewed,
one-shot seed for the external copier-template repository; its parity and render
guard is `tests/test_agent_process_template.py`. Creating that external repository,
the plugin package, and an installation in a target project remain separate work.

## Manifest scope

This manifest classifies the agentic-process contract's own files — the docs, scripts, and
adapter files `roles.yaml` and the tables below name directly — plus, for each, the tests that
gate it by matching name. It is not a claim that every file reachable by *any* transitive
reference (every fixture, every `conftest.py`, every CLI-flag or edge-case test) has a row: three
rounds of review each found a further tranche of those at finer grain, which is a sign the
hand-maintained boundary sits at "the contract and its direct gates," not at "everything Python
can import from there." A row is added when a reader following a table entry would hit a missing
file *the entry itself depends on to run* (e.g. a template a test asserts exists) — not for every
file a test module happens to also read.

## Layer 0 — provider-neutral core (copier)

| File | Export status | Templated fields |
|---|---|---|
| `docs/architecture/agent-process.md` | generic templated | Repository name/owner in examples; the discovery-runbook capture-route names that name kinozal-specific scripts (`capture_kinozal_fixture.py`); `#N` citations (see §Citation policy) |
| `docs/architecture/principles.md` | generic templated | Repository name in illustrative examples; `#N` citations |
| `.agents/orchestration/roles.yaml` | generic as-is | Repository-relative paths describe project-native carriers and Copier-installed `.claude/rules`; plugin-provided `commands/` and `agents/` are logical carrier sources, not target-tree paths |
| `.agents/orchestration/change-classes.yaml` | generic as-is | None |
| `scripts/validate_issue_sections.py` + `scripts/check_orphan_scope.py` | generic as-is | None — section names and the type-label taxonomy are process vocabulary, not domain vocabulary |
| `scripts/agent_orchestrator.py` | generic as-is | None |
| `scripts/review_gate.py` | generic as-is | None |
| `scripts/issue_branch.py` + `scripts/new_branch.py` | generic as-is | None |
| `scripts/set_issue_priority.py` + `scripts/set_issue_status.py` | generic templated | Embedded GitHub Project number, owner, Project node ID, and field/option IDs — specific to this repository's Project 1 |
| `scripts/check_red.py` | generic as-is | None |
| `scripts/open_pr.py` + `scripts/update_pr_body.py` + `scripts/verify_pr_link.py` | generic as-is | None |
| `scripts/check_branch_protection.py` | generic templated | `REQUIRED_CONTEXTS`/`NOT_REQUIRED` — this repository's specific required-check names |
| `scripts/ci_check.py` | generic templated | The `CHECKS` registry names this repository's specific tool invocations (ruff, pytest, pip-audit, import-linter); the run/report loop is generic |
| `scripts/capture_external_fixture.py` + `scripts/check_fixture_ratchet.py` | generic templated | The narrow read-only route list (GitHub, Telegram, Gemini, Sheets) names this repository's external systems; the capture/ratchet mechanism is generic |
| `scripts/gh_io.py` | generic as-is | None — the `gh` CLI transport the two review-gate carriers below import (`scripts/review_gate.py` shells out to `gh` through its own `subprocess.run` and does not import this module) |
| `scripts/check_agent_review_outcome.py` + `scripts/request_codex_review.py` | generic as-is | None — the review gate's two carriers (ADR-0003/ADR-0004); read `.github/workflows/agent-review.yml`'s structured outcome and Codex's PR review state. Both arrive inert without an invoker: `agent-review.yml` is Not exported (below), so a target project must author an equivalent review workflow before these carriers run |
| `scripts/__init__.py` | generic templated | The package docstring names `kinozal_scraper` |
| `.agents/orchestration/state.example.json` | generic as-is | None — a schema example, no repository content |
| `scripts/hooks.py` + `scripts/navigation_policy.py` | generic as-is | None — `hooks.py`'s `pre-bash`/`pre-read` entry points import `navigation_policy.py` directly (same layer, so the import resolves within one copier payload); its `on-edit` checks (`run_on_paths`) are reused by Codex's `scripts/codex_hooks.py` (Layer 2, also copier-distributed — see §Install order below) |
| `docs/adr/0004-controller-pr-review-runs-on-the-workflow-token.md`, `docs/adr/0009-discovery-is-a-separate-role-chained-inside-the-planner-run.md` | generic as-is | None — both explain design decisions behind the exported mechanism itself (the review-gate trust model, the discovery role), not a product feature |
| `docs/adr/0003-second-carrier-for-the-required-review-gate.md`, `docs/adr/0011-agentic-process-distribution-mechanism.md` | generic as-is | None — 0003 is the design record the carriers row above cites by number; 0011 is the mechanism decision this manifest itself implements. Both are process-generic, no kinozal content |
| `docs/adr/template.md` | generic as-is | None — the stock MADR template; `tests/test_adr_records.py:160` hard-asserts it exists, so it is a required input of an exported test, not optional narrative |
| `.github/pull_request_template.md` | generic templated | Repository name in the header comment (line 2); the section structure (`## Summary`, `## Agent record`, `## Test plan`, `## Risk & Rollback`, `## Docs touched`) mirrors the issue contract, which is process vocabulary |
| `pyproject.toml` | generic templated | Supplies the portable default's `ruff` line length and pytest discovery paths. It deliberately excludes the source package name and import contracts; a target owns those product-specific settings |
| `tests/test_hooks.py`, `tests/test_navigation_policy.py`, `tests/test_codex_hooks.py`, `tests/test_review_gate.py`, `tests/test_adr_records.py`, `tests/test_agent_orchestrator.py`, `tests/test_issue_branch.py`, `tests/test_branch_protection.py`, `tests/test_ci_check.py` | generic as-is | None as a set — each test gates the row above with the matching name where that input is exported. Integration classes skip when their optional workflow, hook, or Claude-adapter input is absent; their pure script checks remain active. A row marked "generic templated" above (`check_branch_protection.py`, `ci_check.py`) still exports its test as-is, because the test itself asserts the templated *mechanism*, not the templated *value*. Scope note: this row and the file-gate rows around it are the process contract's docs/scripts/adapters core and the tests that directly gate them — it is not a claim that every fixture, `conftest.py`, or CLI-flag test in `tests/` has a row; §Manifest scope (top of file) states that boundary explicitly |
| `tests/test_doc_links.py`, `tests/test_doc_headers.py`, `tests/test_doc_narrative.py` | generic templated | Each hard-codes documentation directories that include the Copier-delivered Layer 1 `.claude/rules` path. A target without the Claude adapter must drop it; a Claude-adapter target includes that rules directory. The templated field is therefore the directory list, selected by the explicit adapter answer. |

## Layer 1 — Claude adapter

| File | Export status | Channel | Notes |
|---|---|---|---|
| `.claude/commands/plan.md` | generic as-is | plugin marketplace | Payload path: `commands/plan.md`; invoke as `/agent-process:plan N`. Relative Layer 0 links are dropped for the isolated plugin payload. |
| `.claude/commands/implement.md` | generic as-is | plugin marketplace | Payload path: `commands/implement.md`; invoke as `/agent-process:implement N`. Relative Layer 0 links are dropped for the isolated plugin payload. |
| `.claude/agents/discovery.md` | generic as-is | plugin marketplace | Payload path: `agents/discovery.md`; relative Layer 0 links are dropped for the isolated plugin payload. |
| `.claude/agents/architect-reviewer.md` | generic as-is | plugin marketplace | Payload path: `agents/architect-reviewer.md`; relative Layer 0 links are dropped for the isolated plugin payload. |
| `.claude/rules/mindset.md` | generic templated | copier | Harness token tactics are generic; the source repository's measured timings and environment pointer are stripped or generalized |
| `.claude/rules/workflow.md` | generic templated | copier | Structure is generic; the default-adapter statement is generalized for a target project's role catalogue |
| `.claude/rules/testing.md` | generic as-is | copier | Path-scoped operational checklist; its links to Not-exported `docs/architecture/testing.md` and `coverage-gaps.md` are dropped under §Link policy |
| `.claude/settings.json` | generic as-is | copier | The whole file travels as one unit: `permissions.deny` plus the three non-`SessionStart` hook entries. Its commands resolve `scripts/hooks.py` from the Layer 0 Copier payload; the plugin carries no hooks |

### Install order across layers

Layer 1's Copier channel includes every hook command and therefore installs beside its
`scripts/hooks.py` Layer 0 target. The plugin channel carries only commands and agents, but it still
depends on the Layer 0 contract they link to. State the order explicitly wherever the plugin is
installed: Copier first, plugin second. This is a real cross-channel dependency: Copier and the
plugin marketplace do not share a package-manager dependency graph.

## Link policy for cross-layer references

**Decision: an exported file's Markdown links to a Not-exported target get the same
rewrite-or-drop treatment as `#N` citations (§Citation policy), applied at export time.**
`tests/test_doc_links.py::test_every_internal_link_resolves` (exported, Layer 0) resolves every
relative link in its scope against the tree, so an exported file that still points at a
Not-exported one — `agent-process.md` → `project-map.md`; `.claude/rules/testing.md` →
`docs/architecture/testing.md`, `coverage-gaps.md` — ships a payload whose own gate fails on
first run. The export-time transform: either rewrite the link to the target project's
own file at that path (the common case — `testing.md` and `project-map.md` are exactly the kind
of file a target project is expected to author its own copy of, per their Not-exported rows), or
drop the sentence if no equivalent is expected to exist. Left unresolved, no per-file row is
missing — the manifest already marks these targets Not exported — but the exported *payload* is
broken by a link the manifest did not think to rewrite.

The plugin-marketplace payload is copied into an isolated plugin directory, so it has no co-located
Layer 0 tree to target. Its transform drops every relative Markdown link and leaves the link title
as plain text; this applies to Layer 0 and Copier-channel Layer 1 targets alike. The payload test
guards that declared drop outcome rather than treating an absence of dangling links as sufficient.

## Layer 2 — Codex adapter (copier)

| File | Export status | Templated fields |
|---|---|---|
| `.agents/skills/plan-issue/SKILL.md` (+ `agents/openai.yaml` metadata sidecar) | generic as-is | Links to Layer 0 sections by anchor |
| `.agents/skills/implement-issue/SKILL.md` (+ `agents/openai.yaml` metadata sidecar) | generic as-is | Links to Layer 0 sections by anchor |
| `AGENTS.md` | generic templated | `## Codex adapter` and `## Code Review Rules` are generic process pointers already reused as this record's own reference point; `## Repository conventions` (Windows/git-bash shell quirks, subprocess encoding) is this repository's own environment pitfalls and would be re-authored per project; `#458`/`#478` citations (see §Citation policy) |
| `.codex/hooks.json` | generic as-is | None — wires `scripts/codex_hooks.py`, which is itself generic |
| `scripts/codex_hooks.py` | generic as-is | None — Codex's hook adapter, mirrors `scripts/hooks.py`'s Claude entry points |
| `scripts/agent_policy.py` | generic as-is | None — Codex's copy of the forbidden-command policy; Claude's equivalent is `.claude/settings.json`'s static `permissions.deny`, same policy in two encodings |

## Not exported (project- or adapter-specific)

| File | Why it stays |
|---|---|
| `CLAUDE.md` | The root router's *pattern* (a thin pointer file linking to the process contract) is what ADR-0011 recommends a target project adopt, but its content — app description, this machine's environment pitfalls, the PR-workflow adapter choice — is authored per project; the portable operational tactics that would otherwise live here are already Layer 1 (`mindset.md`, `workflow.md`) |
| `scripts/capture_kinozal_fixture.py` | Captures fixtures from the Kinozal production fetcher — this repository's own scrape target, not a process concern |
| `scripts/eval_trailers.py`, `scripts/eval_summarizer.py` | Product-domain evaluation harnesses (trailer selection, Telegram summaries), not agentic-process tooling |
| `observability/claude-code/`, `observability/codex/`, `observability/agent-telemetry/`, `scripts/check_codex_otel_config.py`, `scripts/check_otel_event_delivery.py`, `scripts/token_trend.py` | Telemetry pipeline wired to this repository's Grafana Cloud instance and credentials; a genuinely reusable telemetry template is a separate, larger decision than this manifest scopes |
| `.github/workflows/*.yml`, including `agent-review.yml` | Encode this repository's specific job steps, dependency install, and test paths; a target project's CI needs its own authoring, not a copy. `agent-review.yml` specifically is the workflow the two review-gate carrier scripts above depend on — a target project must author its own equivalent, not just import the carrier scripts |
| `tests/test_agent_frontmatter.py`, `tests/_model_pin_policy.py` | Guard Layer 1's Claude-agent surface and share its model-pin policy with the cloud review workflow. Neither `.claude/agents/` nor that workflow is part of the Layer 0/2 Copier payload, so exporting the test would make a fresh Copier project fail before it had installed or authored those adapter artefacts |
| `tests/test_agent_process.py` | Pins this repository's complete documentation, Git-ignore, and Layer 1 adapter surface. Its source-repository assertions cannot be made true in a portable Layer 0/2 payload; `tests/test_agent_process_template.py` instead renders the payload and runs its default quality gate before export is accepted |
| `docs/architecture/testing.md` | Its checklist is this repository's own boundary map (Sheets/Telegram/YouTube/kinozal.tv fixtures, which layer gets a real vs. fake client) — process-shaped but not process-generic; a target project's equivalent needs its own authoring against its own external systems |
| `docs/architecture/project-map.md` | Mixed, like `CLAUDE.md`: the §Canonical-home IA policy this manifest itself leans on is a portable pattern, but the file-map table it sits inside is a per-file index of this repository's own tree and does not generalize |
| `docs/architecture/coverage-gaps.md` | A ledger of this repository's own accepted untested-behaviour history (entries A–AQ); the pattern — "known test gaps get a stable-ID ledger entry instead of a silently-dropped TODO" — is worth a target project adopting, but the entries themselves are this repository's own |
| `docs/architecture/agent-process-export.md` (this file) | Every row classifies a path in *this* repository's tree; a target project needs its own audit of its own files, not a copy of this table. What travels is the *pattern* — Layer 0/1/2 columns, generic-as-is/templated/Not-exported status, §Citation and §Link policy — the same relationship this row's neighbors (`CLAUDE.md`, `project-map.md`) already have to their own portable patterns |
| `scripts/check_language.py`, `tests/test_language_policy.py`, `docs/adr/0005-english-repository-documentation.md` | Enforce `kinozal_scraper`'s repository-wide English-only documentation and commentary policy. The template build instead verifies that its own rendered Markdown is English-only; it must not scan or govern a target project's product-owned prose. |

## Citation policy for `#N` references

**Decision: strip `#N` issue citations from the Layer 0/2 exported copy and Layer 1 plugin payload, except where a test
fixture constructs it as data under test.** A citation such as
`(#458)` addresses this repository's own GitHub issue tracker; in a new project it resolves to an
unrelated issue or nothing at all, which is worse than no citation. `project-map.md`'s own
Canonical-home rule already separates the operative rule from its narrative provenance ("retain
the decision plus one sentence explaining why it is still valid... move the narrative... to the
issue/PR body"); export applies that same split one level further out — the `#N` pointer is
provenance for *this* repository's history, not part of the rule a new project needs to follow.
Traceability to the source decision is not lost: the in-tree copier build records its source path
and selected answers in `.copier-answers.yml`, while this repository's Git history records the
exact source revision. A separately published, versioned template source would also let Copier
record `_commit` for later updates; the in-tree build does not claim that capability.

The strip applies uniformly to every Layer 0 and Layer 2 file, not only the two rows above that name
it. `roles.yaml`, `change-classes.yaml`, and most of the gate scripts (`validate_issue_sections.py`,
`open_pr.py`, `check_red.py`, `agent_orchestrator.py`, `verify_pr_link.py`, `new_branch.py`,
`issue_branch.py`, `update_pr_body.py`, `implement-issue/SKILL.md`, `AGENTS.md`) carry `#N` citations
of their own; their Templated-fields column says "None" because the citation strip is a
template-authoring transform applied at export time across the whole payload, not a per-file field
to fill in — it is named explicitly only where a file's *dominant* content is citation-bearing
narrative (`agent-process.md`, `principles.md`).

The exception is deliberately narrow: a test may construct a synthetic `#N` value by interpolation
when its predicate or expected output needs to recognise that form. It is not provenance and must
not be stripped; the render guard checks both that no literal source citation survives and that the
resulting payload contains no malformed citation-strip prose.

## Size budget for the exported core

**Decision: cap the exported combined size of `agent-process.md` + `principles.md` at 30 KB.**
Today the two are 40,800 B (`agent-process.md`) + 15,821 B (`principles.md`) ≈ 55 KB combined,
sized for this repository's own history — precedent narrative like "twice ignored as prose (#458,
#465)" earns its place here because it explains why a rule is an exit code and not a checklist item,
for readers who already share that history, but a new project's readers do not. The cut applies the
same operative-rule-plus-one-sentence-rationale split the Canonical-home rule already uses for
internal moves (`project-map.md` §Canonical-home rule), not a blanket trim of rules. The qualitative
form is already gated where it is authored (`tests/test_doc_narrative.py`); the copier-template render
guard now verifies the concrete 30 KB payload. The export keeps operative rules plus one rationale
sentence rather than repository-history narrative.
