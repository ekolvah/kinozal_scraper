# Information architecture

**Question this document answers:** Where each kind of repository knowledge belongs and how the documentation graph is organized.

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
measures observed raw-token consumption (#464, #565), from Claude Code transcripts by branch and turn.

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
  or rule Y was not adopted" → [`ci-tooling-decisions.md` §Consciously not adopted](ci-tooling-decisions.md#consciously-not-adopted);
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
- **Navigation policy** (which shell route a `Read`/`Grep`/`Glob` call replaces, and what a
  `Read` slice may cost) → canonical in `scripts/navigation_policy.py`, delivered as a
  `PreToolUse` denial message on both routes (#485, #534). Separate carrier from the security
  policy above on purpose; `.claude/rules/mindset.md` links, never restates the rule set.
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
copy for source files, but a [**concern-level router**](project-map.md#project-source-files) (concern → files + deep-dive
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
[`ci-workflow.md`](ci-workflow.md#doc-guards).

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
[`ci-workflow.md`](ci-workflow.md#doc-guards)):

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
