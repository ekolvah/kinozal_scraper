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

## Layer 1 — Claude adapter (plugin marketplace)

| File | Export status | Notes |
|---|---|---|
| `.claude/commands/plan.md`, `.claude/commands/implement.md` | generic as-is | Link to Layer 0 sections by anchor; no repository-specific content of their own |
| `.claude/agents/discovery.md`, `.claude/agents/architect-reviewer.md` | generic as-is | Personas reference the Layer 0 contract, not this repository's domain |
| `.claude/rules/mindset.md` | generic templated | Harness token tactics are generic; the RED→GREEN boundary recipe's measured timings (`#517`) and this repository's `CLAUDE.md` §Environment pointer are not |
| `.claude/rules/workflow.md` | generic templated | Structure is generic; the default-adapter statement names this repository's `roles.yaml` |

## Layer 2 — Codex adapter (copier)

| File | Export status | Templated fields |
|---|---|---|
| `.agents/skills/plan-issue/SKILL.md` | generic as-is | Links to Layer 0 sections by anchor |
| `.agents/skills/implement-issue/SKILL.md` | generic as-is | Links to Layer 0 sections by anchor |

## Not exported (kinozal-specific)

| File | Why it stays |
|---|---|
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

## Size budget for the exported core

Today, `agent-process.md` (~41 KB) and `principles.md` (~14 KB) combined are sized for this
repository's own history — precedent narrative like "twice ignored as prose (#458, #465)" earns its
place here because it explains why a rule is an exit code and not a checklist item, for readers who
already share that history. A new project's readers do not. **Target: the exported combined size of
these two files stays at or under 30 KB** (roughly a 45% reduction from today's ~55 KB combined),
achieved by the same operative-rule-plus-one-sentence-rationale split the Canonical-home rule
already applies to internal moves (`project-map.md` §Canonical-home rule), not by cutting rules.
This is a target for whoever builds the copier template, not a new automated guard: the qualitative
form of the rule is already gated where it is authored (`tests/test_doc_narrative.py`), and a
byte-count assertion on a not-yet-existing export would guard nothing today.
