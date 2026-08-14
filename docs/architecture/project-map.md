# Project map — where knowledge lives and which file answers which question

**Question this document answers:** "which project file answers which question" (the index)
**and** "where each kind of knowledge belongs" (the IA policy: tier model + canonical-home rule).
They are two halves of the repository's information architecture. **Do not add content that does
not answer either question** (code detail belongs in a `docs/architecture/*` deep dive; principles
belong in `principles.md`).

**This is an index, not content.** It has one line per file and does not copy the files' contents
(otherwise the index becomes another source of drift). The only exception is `docs/adr/`: it is
indexed **by directory**, with no per-record rows here. The directory grows one record per decision,
and a per-file index would diverge on the first new record; navigation within it is by record number,
which is its address.

## IA policy: where knowledge lives

### Two graph layers: navigation (tree) vs references (not a tree)

The repository IA is **not** a star and **not** a single tree, but two deliberately different
layers; merging them into one picture creates the false impression of a star:

- **Containment (navigation)** — the table of contents through which to descend: `CLAUDE.md` →
  `project-map.md` (this file, the complete "file → question" index) → specific documentation or
  source. The layer is **tree-shaped and single-parented**: the complete file list lives only here;
  `CLAUDE.md` **links to it rather than duplicating it**.
- **Reference (canonical-home links)** — which consumer links to which canonical fact (`§II`,
  `#bug-taxonomy`, `permissions.deny`). This layer is **deliberately not a tree**: one fact is needed
  in multiple contexts (e.g. `principles.md §II` from `testing.md`, `.claude/rules/testing.md`,
  `architect-reviewer.md`, and `.importlinter`), so keyed links go upward and sideways. It cannot be
  made a tree without either duplicating the fact in each branch (paraphrase drift; a canonical-home
  violation) or denying a consumer its pointer to the canon.

The `principles.md ↔ project-map.md` edge is **intentionally bidirectional** (`principles` delegates
the IA policy here; this file describes the tier for principles); it is not a cycle error.

### Knowledge-carrier tier model (official, Claude Code)

Claude Code specifies not the names of `docs/*` (its standard does not regulate them, so there is
nothing to rename) but the **hierarchy of knowledge carriers**:

| Tier | Purpose | When loaded |
|---|---|---|
| `CLAUDE.md` (root) | Thin router: what app this is, environment pitfalls, and pointers. **Target: < 200 lines** | Every session, in full |
| `.claude/rules/*.md` | Operational instructions, **one file = one topic**; can be path-scoped with frontmatter `paths:` | Every session (or only when working on matching paths) |
| `AGENTS.md`, `.agents/skills/`, `.agents/orchestration/`, `.claude/`, `.codex/` | Agent adapters and the provider-neutral catalogues (roles **and** change classes): Codex and Claude adapters for planner/implementer/fixer, the Claude reviewer subagent, control plane, and local hook policies | On invocation / at start |
| `docs/architecture/*.md` | Reference: how the code works (runtime/pipeline/storage/gemini/…) plus this project map and `principles.md` | On demand |
| `docs/adr/*.md` | Explanation: why the decision was made this way and which alternatives were rejected (MADR 4.0.0, append-only) | Linked from a state document |
| `~/.claude/projects/<repo>/memory/` | Auto-memory: **machine- or process-specific only** (see below) | `MEMORY.md` index every session |

**Be honest about tokens.** `.claude/rules/` files *without* `paths:` load in every session just
like `CLAUDE.md`; this is **not** immediately fewer tokens. The gain is (a) **deduplication** (a
rule lives in one place), (b) **single responsibility**, and (c) **path scoping** (`paths:
[tests/**]` is not loaded when tests are untouched — the only token-positive case).

`tests/test_always_load_budget.py` gates the total size of this unconditional charge (#375): the
threshold is a ratchet so growth happens through a deliberate review change, not silent drift
(the budget grew by ~3.8 KB in #416/#417). What the gate **does not** catch is ledger entry **AB**
in [`coverage-gaps.md`](coverage-gaps.md).

**Declared charge ≠ paid charge.** The bytes in the always-load set multiply by the number of
turns through `cache_read`, and the ratchet cannot see that multiplier. `scripts/token_trend.py`
measures actual consumption (#464), from Claude Code transcripts by branch and turn.

### Canonical-home rule

> **Every fact has exactly one home. Other mentions are links only, never paraphrases.**

- **Shared agent rules** (procedure, role contracts, objective function, gates) →
  `docs/architecture/`, because every adapter reads them, not only the adapter whose directory
  contains it. A provider-specific file (`.claude/**`, `.agents/skills/**`, `AGENTS.md`) carries
  **only the interface and permissions**: how an issue number arrives, how a subagent is called,
  and how a body is written. The guard is
  `tests/test_agent_process.py::test_provider_specific_adapter_files_do_not_define_shared_gates`
  (a denylist of definitions: severity taxonomy, runbook limits, objective-function priorities).
  Script and command names in an adapter are legitimate; defining rather than pointing is forbidden
  (#452).
- **Operational procedural rules** (workflow) remain **whole — rule and rationale together**;
  they are not split (splitting recreates the duplicate). The former location becomes a pointer.
  **Rationale ≠ narrative** (#375): retain the decision plus one sentence explaining why it is
  still valid (without it, the rule looks ritualistic and the next "simplifier" will remove it);
  move the narrative — dates, commit numbers, and what a specific review caught — to the issue/PR
  body. The rule retains only bare `(#N)`. Before removing it, check `gh issue view N` to ensure
  the narrative is really there; some facts are session artefacts.
- **Decision rationale** ("why this was chosen rather than that, and why it remains valid") → a
  repository record with a **stable ID**, linked by a state document. Its destination is the
  **first match**: (1) "test X is not covered" → [`coverage-gaps.md`](coverage-gaps.md); (2) "tool
  or rule Y was not adopted" → [`ci.md` §Consciously not adopted](ci.md#consciously-not-adopted);
  (3) "an architectural decision costly to reverse and affecting several modules or documents" →
  a MADR record in [`docs/adr/`](../adr/); (4) everything else does **not** become a record — its
  home remains the issue/PR body. Filter (3) is cost of change: if every decision is architectural,
  none is, and the directory degenerates into a dump of the same narrative. The three homes are
  not forced into uniformity; the testing ledger works as it is. **Why a record rather than
  `(#N)`:** a task number is an external tracker event address, without repository body or status,
  so linking it **forces** nearby retelling; a record ID eliminates the retelling (the format
  rationale and measurement are in directory record `0001`).
- **`docs/adr/` directory policy** (its home is here because it changes while an accepted record
  does not): the [MADR 4.0.0](../adr/template.md) format is verbatim; filename `NNNN-slug.md`,
  with the number as record address; status is the **closed set** `proposed` / `rejected` /
  `accepted` / `deprecated` / `superseded by ADR-NNNN` (upstream provides `status` as free text,
  but append-only discipline cannot be expressed without a closed set); an **accepted record is
  not rewritten** — correct typos and broken links, and express a changed decision in a new record
  that the old one links to forward. The size guide is up to ~200 lines: a longer file displaces
  the context for which it was opened. `tests/test_adr_records.py` holds the structure. The `## ADR`
  issue section (part of the base `REQUIRED_SECTIONS`, kept by every change class) gates whether a
  record was considered: a link or explicit `none: <reason>`. The gate does not judge whether the
  decision merits a record — that is a cost-of-change judgement; it guarantees the question was
  **asked**, just as `## Architect review` guarantees awareness rather than review quality (#150).
- **Wording of principles §I–VII** → canonical in [`principles.md`](principles.md), referenced by
  number (`architect-reviewer.md`, `mindset.md`); **do not change the numbering**.
- **Enforcement facts** (git prohibitions) → canonical in `.claude/settings.json`
  `permissions.deny` (+ synchronisation test `tests/test_settings_deny.py`). **Do not create mirror
  files** — that is a duplicate by definition.
- **Navigation policy** (which shell route a `Read`/`Grep`/`Glob` call replaces) → canonical in
  `scripts/navigation_policy.py`, delivered as a `PreToolUse` denial message (#485). Separate
  carrier from the security policy above on purpose; `.claude/rules/mindset.md` links, never
  restates the rule set.
- A `.claude/rules/` file **does not** paraphrase a principle or deny line; it contains only a link
  or a procedure that exists nowhere else.

**A human enforces the boundary in review.** `grep` catches only verbatim copies, not semantic
paraphrase; when a rule moves, the reviewer checks that the former location retains a **link, not a
retelling**. We deliberately do **not** build a semantic-duplicate detector — it would create false
coverage (a §IV violation: a green detector that misses paraphrase is worse than an honest "a human
reviews it").

### Documentation and commentary language policy

All repository documentation prose and Python commentary are English-only. This makes the
repository legible to every supported agent and contributor without maintaining parallel-language
rules. The decision, alternatives, and migration rationale are in
[ADR-0005](../adr/0005-english-repository-documentation.md).

For mapped Markdown files, the sole accepted question marker is
`**Question this document answers:**`, before the first `## `. The `_MARKERS` expectation is this
single English marker; adding an alternative is a policy change, not a per-file test exception.
`scripts/check_language.py` enforces the English-only policy for tracked Markdown prose and Python
commentary; code spans and fenced code/data are deliberately outside its prose scope.

**The policy's carriers are `.md` and `.py` — nothing else, and that is a decision, not an
oversight.** Comments in workflow YAML, `.githooks/`, and `.gitattributes` stay as written: each
would need its own comment syntax in the gate, and an unenforced rule over them would be exactly
the invisible-cost shape ADR-0005 exists to remove. Python **string literals** are outside too, so
operator-facing Russian diagnostics in `scripts/` are legal here; ADR-0005 records what that costs.

**What counts as a mapped file** (there too, #421): **`.md` under `docs/architecture/` and
`.claude/rules/`**. This is the only scope rule; no second layer filters it. Two clarifications
explain why the boundary is here rather than expanding the rule:

- **`.claude/agents/*.md` and `.claude/commands/*.md` are outside scope not as punishment, but
  because they already have a header — frontmatter `description:`.** Requiring a marker line too
  would keep the canon in two places. The converse confirms the boundary: `.claude/rules/testing.md`
  frontmatter has `paths:` but no `description:`, so its marker line is required and the scope
  already provides that.
- **`CLAUDE.md` is consciously excluded**: it is a thin router, marked ❌ kitchen-sink in the File
  map; requiring a single question from it would freeze with a gate the role from which it should be
  freed.
- **`docs/adr/` lies outside `docs/architecture/` for the same reason.** A MADR record has its own
  header (frontmatter `status`/`date` + decision title), so requiring a marker line too would keep
  the canon in two places — the same argument as for `.claude/agents/`. The directory still has an
  invariant: `tests/test_adr_records.py` guards name, unique number, status, `superseded by`
  resolution, and required sections.

We consciously did **not generate the map from headers** (#164): a per-file "which question it
answers" text would duplicate the docstring verbatim, making the generated map a second copy of the
canon (redundant with what the agent already reads; static output ages and consumes tokens; the
script cannot output curated SR ✅/❌ judgements or duplicates anyway). Instead, use inexpensive
**presence lint** (ruff `D100`/`D104`/`D419` in `check_lint`, #253; formerly bespoke
`scripts/check_headers.py`): every public `.py` under `src/`, `scripts/`, and `tests/` (#433) must
carry a non-empty module docstring or be red. The map therefore provides not a per-file question
copy for source files, but a [**concern-level router**](#project-source-files) (concern → files + deep-dive
pointer) — orientation absent from a per-file docstring.

**The `tests/` docstring form is "genre: what it guards"** (`Anti-drift guard for …`,
`Tests for X.py — …`, `E2E: …`); a parenthetical issue pointer is optional. Guards normally have
one; product suites often have no ancestor to which to point. This is the format's **only home**:
`D100` holds presence but not content, and categorising tests into directories was consciously
rejected (#433) — the move would cost 71 path references in prose and code and create the silent
failure mode "a test landed in the wrong directory". Test navigation remains `grep`, now over a
meaningful docstring.

For `.md`, `tests/test_doc_headers.py` (#421) gates the same presence by test rather than an entry
in `CHECKS`: `test_ci_check.py::TestStepParity` requires registry parity with `ci.yml`, so an entry
would require an additional `--only` step for a static check that `pytest` already runs. Scope is
derived from a glob so the next architecture document enters the rule automatically. `tests/test_doc_links.py`
(#427) guards pointer integrity (an ID is an address): every internal link and code span of the form
`` `file.md#anchor` `` must resolve, otherwise a renamed section silently breaks all incoming anchors.
`tests/test_doc_narrative.py` (#428) guards issue-link **form**: `#N` is a parenthetical pointer,
not a sentence member, and is forbidden in a section title. The mechanics of all three are in
[`ci.md`](ci.md#doc-guards).

**Presence ≠ correctness.** Lint guarantees that a docstring *exists* and is non-empty, not that it
is *current*: an outdated non-empty docstring passes. The Markdown guard is the same: it guarantees
only that there is **something to dispute** about a file boundary, not that the header matches its
contents. A human catches docstring ↔ actual-purpose divergence in review — the same honest §IV
position as for semantic duplicates (a green detector that provides false coverage is worse than an
honest "a human reviews it").

The `docs/adr/` record guard (`tests/test_adr_records.py`) has the same boundary: it holds the
structure — name, unique number, status, `superseded by` resolution, and required sections — but
does **not** distinguish a draft record with unfilled `{placeholder}` from a real one, judge whether
a decision merits a record (cost of change), or determine whether rationale is outdated. This is not
a coverage gap worth testing, but the same class of semantic judgement: a detector would provide
false coverage.

### What documentation describes: current state, not history or ideas

> **`docs/` describes the currently implemented state of the product and architecture — decisions
> as they exist now. It is not a dumping ground: knowledge that is not "currently implemented
> state" lives in its own home.**

- **A changed decision → edit the existing file, do not add another.** The need is a current
  description of existing decisions, not a changelog: change history belongs in git/PR, not a
  document body. Two files for "before" and "after" guarantee drift.
- **Decision rationale → a record with a stable ID, not a state-document paragraph.** Banning
  narrative without a rationale home does not work — this was measured (ADR-0001: 174 narrative
  mentions of `#N` out of 300). The route is §Canonical-home above; the state document retains the
  decision, one sentence explaining why it remains valid, and a link.
- **Ideas, tasks, roadmaps, and unimplemented initiatives → GitHub issues** (they survive moving to
  another machine just as the repository does; that is their durable home). Precedent: an attempt
  to put the trailer-initiative roadmap in `docs/initiatives/` was rejected (#188), and the scope
  itself is distributed across the initiative's issues (#138–#145).

**Link form** (gated by `tests/test_doc_narrative.py`; mechanics —
[`ci.md`](ci.md#doc-guards)):

- **`#N` is a parenthetical pointer, not a sentence member.** The criterion is testable: remove the
  parentheses — does the statement remain complete? Then the form is correct
  (`` discriminator compares an exact literal (#385) ``). If a phrase is incomplete without
  visiting the tracker (`` Closed by #88. ``, `` RCA #396 established that… ``), it retells an
  event with its own home. The gate checks **form**, not genre: a chronicle neatly placed in
  parentheses passes; a human catches it in review.
- **`#N` is forbidden in a section title, including in parentheses.** GitHub generates an anchor
  from title text, so `` ## Eval harness (#139) `` makes the section address `#eval-harness-139`:
  an address tied to the number of the task that produced it.
- **The `#` sigil is reserved for issue/PR links.** The rule is `` `agent-process.md` ``; the board
  is `` `Project 1` ``. Otherwise the guard would need an open dictionary of left contexts that
  grows with every new notation form. It is gated across **all** tracked files, not only `.md`:
  in `.py`/`.toml` the same error costs more because `#N` is printed to the agent. The branch is a
  line-by-line regexp over the raw file, so a **code span is not an escape hatch**: write the
  negative example through a metavariable (`` `workflow #N` ``), where `#` is not followed by a
  digit, and the rule can be illustrated without failing on its own example.
- **Write a negative form example in a code span**; otherwise the document explaining the rule
  fails it. This works for the two Markdown branches; the sigil branch has a different escape hatch:
  the metavariable above.
- **MADR records (`docs/adr/`) are outside the guard's scope by genre**: a record is the rationale
  home, structurally dated and immutable after acceptance, so a guard failure on it would have no
  legal fix.

The existing subsections are **instances** of this umbrella, not separate rules: machine/environment-
specific material → out-of-repository memory (["Memory ↔ repository"](#memory--repository-resolved-policy)
below); backlog/status tracker → issues (remaining debt —
[#177](https://github.com/ekolvah/kinozal_scraper/issues/177), see the end of the file). Each is a
special case of "what is not currently implemented state does not live in `docs/`".

### Memory ↔ repository: resolved policy

An instance of ["What documentation describes"](#what-documentation-describes-current-state-not-history-or-ideas)
(machine-specific → not `docs/`). **Project instructions live in the repository** (`.claude/`,
`docs/`, scripts, templates), not in private out-of-repository Claude memory. Out-of-repository
memory is **only** for machine/environment-specific material or a working style with a particular
operator; otherwise a clone on another machine cannot see project knowledge and the source of truth
splits. This is **active policy, not backlog**: the `architect-review` persona formerly lived in
memory, moved to the repository (`.claude/agents/architect-reviewer.md` +
`validate_issue_sections.py` gate + `principles.md §Governance`), and its memory was deleted (#150).
The issue-priority mechanism made the same memory→repository move (Priority field in GitHub Project
1): from private memory to `scripts/set_issue_priority.py` (embedded Project/field/option IDs + unit
tests) + the [`agent-process.md`](agent-process.md) rule (the agent asks the user for priority →
script); the memory was deleted (#351).

**Gate instead of prose (#353).** This policy was itself prose and was violated twice in one
session (priority and open_pr link-lag process facts were put in memory instead of the repository).
**Root cause:** a deterministic trigger (writing a file to the memory directory) was left unenforced
despite existing hook infrastructure — a direct violation of
[`principles.md` §Scripts over instructions](principles.md#scripts-over-instructions). It is not
possible to fully gate "this prose must be a script" (a semantic judgement; no semantic-duplicate
classifier), but the special case — writing to `.claude/projects/<slug>/memory/` — is trivially
gated: `scripts/hooks.py` (`_is_memory_write` → `memory_write_signal`, PostToolUse exit 2) emits a
**checkpoint reminder**: "is this machine/operator-specific? Otherwise move it into the repository".
It is a forcing function (a deliberate decision point), **not** a semantic classifier and **not** a
hard block: predicate "wrote to memory" ≠ violation "wrote process knowledge", so it signals on all
writes (including legitimate ones) — false-positive-by-design, with low miss cost for rare memory
writes. PreToolUse blocking, a semantic classifier, and Agent Governance Toolkit are consciously
out of scope.

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
| `.agents/skills/plan-issue/` | Codex planner adapter, invoked as `$plan-issue #N`; same runbook, performs architect review itself | ✅ |
| `.agents/skills/implement-issue/` | Codex implementer/fixer adapter, invoked as `$implement-issue #N` | ✅ |
| `.claude/agents/architect-reviewer.md` | Plan-reviewer persona; reads its contract (what to check; coverage-first finding format: grade, do not filter, #392) and objective function **from the canon** [`agent-process.md` §Architect review contract](agent-process.md#architect-review-contract) (the subagent does not load always-load rules; it reads them itself and retains no copy). Model/`effort` are pinned; policy and pin boundaries are in [`ci.md` §Model pinning](ci.md), with guard `tests/test_agent_frontmatter.py` | ✅ |
| `.claude/settings.json`, `.codex/hooks.json`, `scripts/agent_policy.py` | Local deny policy for Claude and Codex; branch protection remains final | ✅ |
| `.claude/settings.local.json` (gitignored) | Personal mode + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, personal) |

### `docs/architecture/`

| File | Question answered | Single-responsibility? |
|---|---|---|
| `principles.md` | Mix: §I–VII principles (partly RUNTIME: §III Delivery, §IV Visibility) + Quality Gates + Governance (workflow delegated to `agent-process.md`) | ❌ runtime principles + development process together |
| `project-map.md` (this file) | Which file answers which question + where knowledge lives (IA policy) | ✅ |
| `runtime.md` | What exists at runtime and how it connects: available pipelines, Protocol boundaries, generic data flow, and modules that consciously bypass the generic pattern (Telethon-direct). Breadth, not depth | ✅ |
| `pipeline.md` | How **one** run is structured and behaves: extraction layers, `extract_from_*` → `NormalizedItem` contracts, "a new source = configuration, not code", error policy, notification templates, macros, trailers, **and fetch behaviour** (HTML source configuration, Kinozal mirror fallback — #418) | ✅ |
| `storage.md` | Storage Protocol + implementations, DI, EAFP sheet creation and schema validation, dedupe-key lookup, row schema, column invariants, write order | ✅ |
| `testing.md` | How quality is guaranteed: test levels, bug taxonomy, what to mock (links to `principles.md §II`, does not duplicate it). Strategy, not exceptions | ✅ |
| `coverage-gaps.md` | Where we consciously **do not** test and why: ledger `A`…`AI` with stable letter IDs + modules without dedicated tests. Moved out of `testing.md` so the growing exception list does not mix with strategy | ✅ |
| `ci.md` | Quality gates on the change path (local pre-commit, `ci.yml`, cloud `agent-review`), live GitHub API context for reruns (body + SHA as untrusted data), schema-validated outcome that deterministically completes the ordinary review job without marker/polling/retry, and `github_token`, which makes a PR for the review controller itself reviewed like every other PR (#483), plus the **single home for agent-tooling model-pinning policy** (§Model pinning: both surfaces — `agent-review.yml` and `.claude/agents/*.md`, pin boundaries, two guards). The runtime half (environment variables, production workflow) moved to `operations.md` (#418); only the gate facet remains from the production cron (E2E smoke under `principles.md` §Quality Gates). Decision rationales are compressed to the operational minimum (#419): the "how we got here" narrative belongs in the issue/PR; this file retains only the sentence without which an agent would act wrongly or redo rejected work. Rejected tools are a row at their gate or in §Consciously not adopted | ✅ |
| `operations.md` | How the production run and maintainer-operated services are run: schedule and step order, environment variables and secrets, failure isolation (#245) and alerting (#310), operator runbooks (`TELETHON_SESSION` rotation), patient Soldout retries, Claude Code direct OTel, and the Codex → Alloy → Grafana setup, verification, rollback, and baseline review (#471, #472). Took the runtime half of `ci.md` (#418) | ✅ |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts / call observability (token+latency `llm_call` log + Phoenix development recipe, #145) | ✅ |
| `llm-security.md` | Enricher LLM threats (OWASP LLM Top 10 → safeguards/residual) plus Claude/Codex development-telemetry trust boundaries: prompt-injection fence, output escaping, honest blast radius, content-logging deny flags, loopback Alloy, and external metadata exposure (#308, #471, #472) | ✅ |

### `docs/adr/`

| File | Question answered | Single-responsibility? |
|---|---|---|
| `docs/adr/` (whole directory) | Why this decision was made and what was rejected: MADR 4.0.0 records with stable `NNNN` IDs, append-only (a changed decision = a new record with `superseded by`). Entry is the cost-of-change filter (§Canonical-home). `template.md` is the verbatim upstream template; `tests/test_adr_records.py` is the guard | ✅ |

### Process scripts and templates

| File | Question answered |
|---|---|
| `evidence/` (Git-ignored) | Working-tree-only planning captures retained until merge; the durable compressed record and fixture boundary are canonical in [`agent-process.md` §Issue contract](agent-process.md#issue-contract) |
| `scripts/validate_issue_sections.py` + `scripts/check_orphan_scope.py` + `.agents/orchestration/change-classes.yaml` | Verifies the section set **resolved from the issue's one type label** (base `REQUIRED_SECTIONS` ± the class row) plus the `Agent handoff`, `Architect review`, and discovery-section field contracts — `Evidence` for `bug`, `Prior art` with its reuse/build verdict for every other class — and fails when an issue carries zero or several type labels; on a passing issue, prints the resolved class with its derived RED obligation and surfaces the non-blocking reminder for an explicit `Out of scope` follow-up without `#N` or `wontfix`/`YAGNI` (#368). Gate for planner and implementer adapters; the reminder itself never changes the exit code. The planner-only `--mark-planned` moves the issue's Project 1 Status to `Planned` on a passing validation and warns without changing the exit code when the board write fails; the unflagged implementer call stays read-only (#519) |
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
| `scripts/check_branch_protection.py` | Compares "declared in repository ↔ configured in GitHub" required status checks for branch `main`; the **machine canon of composition** is its own `REQUIRED_CONTEXTS`/`NOT_REQUIRED`, to which documentation links. Always prints the actual list; exit `1` is drift and `2` is tool failure (not "no drift"); `--allow-drift "<reason>"` expresses intentional temporary drift with a printed reason rather than bypassing with `--no-verify` (#458). Called by `.githooks/pre-push` before `ci_check.py`; not put in CI because `GITHUB_TOKEN` lacks `administration` scope (#436). No separate controller-PR gate is needed: such a PR passes the same required contexts as any other (#483). Prose home for consequences: [`ci.md` §Required status checks](ci.md#required-status-checks-branch-protection) |
| `scripts/ci_check.py` | Local pre-commit/pre-push quality gate (mirror of the CI job) |
| `scripts/eval_trailers.py` | Trailer-selection evaluation harness with three scorecards: `TrailerStrategy` (YouTube pick), `evaluate_delivery` (production `select_trailer`, the user-visible result, #379), and `evaluate_tmdb` (TMDB source). It uses a frozen golden set with offline Hit/Wrong/Miss outcomes against `correct`, plus `--record`/`--record-tmdb`/`--update-baseline`. The **gate** is the per-film delivery result in `tests/fixtures/trailer_baseline.json`, enforced by `tests/test_eval_baseline.py` rather than a `ci_check` CHECKS entry. The dataset tests both finding an accepted trailer (`correct`) and rejecting verified wrong candidates (`trap`, #380). Deep dive: `testing.md#eval-harness--trailer-selection` (#139, #329, #379, #380) |
| `scripts/eval_summarizer.py` | RAGAS evaluation of `summary_ru`: faithfulness and answer relevancy against a frozen golden set instead of a `response_pattern` format vibe check. The LLM-as-judge metric is live/API-gated for development, not CI; the `_evaluate_dataset` boundary is doubled and pure seams are tested. RAGAS is a development-only dependency. Deep dive: `testing.md#eval-harness--summarizer-faithfulness` (#347) |
| `scripts/hooks.py`, `scripts/codex_hooks.py` | Shared post-edit checks plus the Claude and Codex hook adapters; ruff feedback and pip-compile reminder complement `ci_check.py`. `pre-bash` (Claude `PreToolUse`) delegates to `scripts/navigation_policy.py` |
| `scripts/navigation_policy.py` | Token-economy policy (#485): decides that a shell stage reads the filesystem — by counting file operands, so `grep FILE` is denied while `cmd \| grep` is not — and returns a denial that **names the replacement tool call**. Separate carrier from the security policy `agent_policy.py`, and fails **open**: it claims only that a cheaper route exists |
| `scripts/token_trend.py` | Measures **actual** development-session token use from Claude Code transcripts, computing price-weighted effective tokens per branch and turn and detecting rolling-window growth by median plus an absolute floor. A `SessionStart` hook in `.claude/settings.json` is quiet normally and **always** exits 0 so the hook does not emit its own alert; `--report` prints the table. Because transcripts are retained for only 30 days (`cleanupPeriodDays`), branch aggregates survive in local `token_ledger.jsonl`. Complements the static `test_always_load_budget.py` ratchet: that guards declared cost, this measures paid cost (#464) |
| `observability/claude-code/` | Values-free Claude Code direct-OTel template and live-captured signal/attribute catalogue. Credentials stay outside git; operation is in `operations.md`, privacy in `llm-security.md`, and the choice in ADR-0006 (#471) |
| `observability/codex/`, `observability/agent-telemetry/`, `scripts/check_codex_otel_config.py` | Values-free Codex and Alloy templates, deterministic metrics-only/loopback pipeline guard, live-captured Codex name catalogue, and the shared importable Grafana dashboard. Operation is in `operations.md`, privacy in `llm-security.md`, and the bridge choice in ADR-0007 (#472) |
| `.github/workflows/ci.yml` | Quality job on PR/push (must mirror `ci_check.py`) |
| `.importlinter` | §II protocol boundaries as a machine contract (the `imports` gate in `ci_check`): dependency direction + adapter-no-auth; deep dive `ci.md` (#234) |

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
| Shared HTTP policy (not a Protocol boundary) | `src/kinozal_scraper/http_retry.py` is the single home for transient-error classification across curl_cffi and stdlib requests, with **two** status-code sets. Only Cloudflare-protected HTML transport retries 403/429 (#358); for JSON APIs those responses are rate limits with their own reset windows, and repeated GitHub API requests can get the integration banned (#365) | `coverage-gaps.md` **M**/**M2**/**M3** |
| Utilities | `src/kinozal_scraper/text_utils.py` | — |

---

Residual open debt is tracked in [issue #177](https://github.com/ekolvah/kinozal_scraper/issues/177),
an instance of the [documentation scope rule](#what-documentation-describes-current-state-not-history-or-ideas):
backlog and status tracking belong in issues, not `docs/`; completed items remain in their PR history.
