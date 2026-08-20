**Question this document answers:** which files of this repository's agentic-process contract
can move to a new project as-is, which need per-project parameterization, and which never leave —
the manifest a future copier template and Claude Code plugin are built from, not the build itself.

The distribution mechanism this manifest feeds is decided in
[ADR-0011](../adr/0011-agentic-process-distribution-mechanism.md): copier for the two exported
layers below, the official Claude Code plugin marketplace for the Claude-adapter layer. Building
the actual template repository, the plugin package, and installing either into a target project is
out of scope here — a follow-up issue opened when that build starts.

## Layer 0 — provider-neutral core (copier)

| File | Export status | Templated fields |
|---|---|---|
| `docs/architecture/agent-process.md` | generic templated | Repository name/owner in examples; the discovery-runbook capture-route names that name kinozal-specific scripts (`capture_kinozal_fixture.py`); `#N` citations (see §Citation policy) |
| `docs/architecture/principles.md` | generic templated | Repository name in illustrative examples; `#N` citations |
| `.agents/orchestration/roles.yaml` | generic as-is | None — `adapter_files` values are already repository-relative paths that hold in any project with the same directory layout |
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
| `scripts/check_language.py` | generic as-is | None — enforces this repository's English-only documentation choice (ADR-0005); a target project either keeps the script wholesale or drops it, not a per-field template |
| `scripts/gh_io.py` | generic as-is | None — the `gh` CLI transport `scripts/review_gate.py` and the two review-gate carriers below share |
| `scripts/check_agent_review_outcome.py` + `scripts/request_codex_review.py` | generic as-is | None — the review gate's two carriers (ADR-0003/ADR-0004); read `.github/workflows/agent-review.yml`'s structured outcome and Codex's PR review state, not this repository's domain |
| `scripts/__init__.py` | generic templated | The package docstring names `kinozal_scraper` |
| `.agents/orchestration/state.example.json` | generic as-is | None — a schema example, no repository content |
| `scripts/hooks.py` | generic as-is | None — the Claude-only `pre-bash`/`pre-read` entry points route through `scripts/navigation_policy.py` (Layer 1 below); the `on-edit` checks (`run_on_paths`) are reused by Codex's `scripts/codex_hooks.py` (Layer 2 below), so this module is shared rather than Claude-only |

## Layer 1 — Claude adapter (plugin marketplace)

| File | Export status | Notes |
|---|---|---|
| `.claude/commands/plan.md`, `.claude/commands/implement.md` | generic as-is | Link to Layer 0 sections by anchor; no repository-specific content of their own |
| `.claude/agents/discovery.md`, `.claude/agents/architect-reviewer.md` | generic as-is | Personas reference the Layer 0 contract, not this repository's domain |
| `.claude/rules/mindset.md` | generic templated | Harness token tactics are generic; the RED→GREEN boundary recipe's measured timings (`#517`) and this repository's `CLAUDE.md` §Environment pointer are not |
| `.claude/rules/workflow.md` | generic templated | Structure is generic; the default-adapter statement names this repository's `roles.yaml` |
| `.claude/rules/testing.md` | generic as-is | Path-scoped operational checklist; every rule is a pointer to Layer 0 (`principles.md`, `testing.md`), no repository-specific content |
| `.claude/settings.json` | generic templated | `permissions.deny` and the `PreToolUse`/`PostToolUse` hook wiring travel as-is; the `SessionStart` hook invoking `scripts/token_trend.py` is dropped in the exported copy — that script is Not exported (telemetry, below) |
| `scripts/navigation_policy.py` | generic as-is | None — the token-economy routing policy behind `mindset.md`'s PreToolUse rule; no repository-specific path or denylist entry |

## Layer 2 — Codex adapter (copier)

| File | Export status | Templated fields |
|---|---|---|
| `.agents/skills/plan-issue/SKILL.md` (+ `agents/openai.yaml` metadata sidecar) | generic as-is | Links to Layer 0 sections by anchor |
| `.agents/skills/implement-issue/SKILL.md` (+ `agents/openai.yaml` metadata sidecar) | generic as-is | Links to Layer 0 sections by anchor |
| `AGENTS.md` | generic templated | `## Codex adapter` and `## Code Review Rules` are generic process pointers already reused as this record's own reference point; `## Repository conventions` (Windows/git-bash shell quirks, subprocess encoding) is this repository's own environment pitfalls and would be re-authored per project; `#458`/`#478` citations (see §Citation policy) |
| `.codex/hooks.json` | generic as-is | None — wires `scripts/codex_hooks.py`, which is itself generic |
| `scripts/codex_hooks.py` | generic as-is | None — Codex's hook adapter, mirrors `scripts/hooks.py`'s Claude entry points |
| `scripts/agent_policy.py` | generic as-is | None — Codex's copy of the forbidden-command policy; Claude's equivalent is `.claude/settings.json`'s static `permissions.deny`, same policy in two encodings |

## Not exported (kinozal-specific)

| File | Why it stays |
|---|---|
| `CLAUDE.md` | The root router's *pattern* (a thin pointer file linking to the process contract) is what ADR-0011 recommends a target project adopt, but its content — app description, this machine's environment pitfalls, the PR-workflow adapter choice — is authored per project; the portable operational tactics that would otherwise live here are already Layer 1 (`mindset.md`, `workflow.md`) |
| `scripts/capture_kinozal_fixture.py` | Captures fixtures from the Kinozal production fetcher — this repository's own scrape target, not a process concern |
| `scripts/eval_trailers.py`, `scripts/eval_summarizer.py` | Product-domain evaluation harnesses (trailer selection, Telegram summaries), not agentic-process tooling |
| `observability/claude-code/`, `observability/codex/`, `observability/agent-telemetry/`, `scripts/check_codex_otel_config.py`, `scripts/check_otel_event_delivery.py`, `scripts/token_trend.py` | Telemetry pipeline wired to this repository's Grafana Cloud instance and credentials; a genuinely reusable telemetry template is a separate, larger decision than this manifest scopes |
| `.github/workflows/*.yml` | Encode this repository's specific job steps, dependency install, and test paths; a target project's CI needs its own authoring, not a copy |

## Citation policy for `#N` references

**Decision: strip `#N` issue citations from the Layer 0/2 exported copy.** A citation such as
`(#458)` addresses this repository's own GitHub issue tracker; in a new project it resolves to an
unrelated issue or nothing at all, which is worse than no citation. `project-map.md`'s own
Canonical-home rule already separates the operative rule from its narrative provenance ("retain
the decision plus one sentence explaining why it is still valid... move the narrative... to the
issue/PR body"); export applies that same split one level further out — the `#N` pointer is
provenance for *this* repository's history, not part of the rule a new project needs to follow.
Traceability to the source decision is not lost: a copier-generated project keeps a
`.copier-answers.yml` pinning the exact template commit, which is a stronger and more durable
address than a bare issue number would be in a foreign tracker.

The strip applies uniformly to every Layer 0 and Layer 2 file, not only the two rows above that name
it. `roles.yaml`, `change-classes.yaml`, and most of the gate scripts (`validate_issue_sections.py`,
`open_pr.py`, `check_red.py`, `agent_orchestrator.py`, `verify_pr_link.py`, `new_branch.py`,
`issue_branch.py`, `update_pr_body.py`, `implement-issue/SKILL.md`, `AGENTS.md`) carry `#N` citations
of their own; their Templated-fields column says "None" because the citation strip is a
template-authoring transform applied at export time across the whole payload, not a per-file field
to fill in — it is named explicitly only where a file's *dominant* content is citation-bearing
narrative (`agent-process.md`, `principles.md`).

## Size budget for the exported core

**Decision: cap the exported combined size of `agent-process.md` + `principles.md` at 30 KB.**
Today the two are 40,800 B (`agent-process.md`) + 15,821 B (`principles.md`) ≈ 55 KB combined,
sized for this repository's own history — precedent narrative like "twice ignored as prose (#458,
#465)" earns its place here because it explains why a rule is an exit code and not a checklist item,
for readers who already share that history, but a new project's readers do not. The cut applies the
same operative-rule-plus-one-sentence-rationale split the Canonical-home rule already uses for
internal moves (`project-map.md` §Canonical-home rule), not a blanket trim of rules. No automated
guard: the qualitative form is already gated where it is authored (`tests/test_doc_narrative.py`),
and a byte-count assertion on a not-yet-existing export would guard nothing today. Making the actual
cut is the copier-template build's own work (tracked in `docs/architecture/coverage-gaps.md` **AP**),
not this manifest's.
