# Information architecture

**Question this document answers:** Where each kind of repository knowledge belongs and how the documentation graph is organized.

## IA policy: where knowledge lives

### Two graph layers: navigation (tree) vs references (not a tree)

The repository IA is **not** a star and **not** a single tree, but two deliberately different
layers; merging them into one picture creates the false impression of a star:

- **Containment (navigation)** — the table of contents through which to descend: `CLAUDE.md` →
  `project-map.md` (the complete "file → question" index) → specific documentation or source.
  `CLAUDE.md` also names this IA-policy document. The layer is **tree-shaped and single-parented**:
  the complete file list lives only in `project-map.md`, which `CLAUDE.md` links to rather than
  duplicating.
- **Reference (canonical-home links)** — which consumer links to which canonical fact (`§II`,
  `#bug-taxonomy`, `permissions.deny`). This layer is **deliberately not a tree**: one fact is needed
  in multiple contexts (e.g. the plugin's `principles.md` §II from `testing.md`, `.claude/rules/testing.md`,
  and `.importlinter`), so keyed links go upward and sideways. It cannot be
  made a tree without either duplicating the fact in each branch (paraphrase drift; a canonical-home
  violation) or denying a consumer its pointer to the canon.

### Always-load budget

`CLAUDE.md` and the `.claude/rules/` files *without* `paths:` load in every session.
`tests/test_always_load_budget.py` gates the total size of this unconditional charge (#375): the
threshold is a ratchet so growth happens through a deliberate review change, not silent drift
(the budget grew by ~3.8 KB in #416/#417). What the gate **does not** catch is ledger entry **AB**
in [`coverage-gaps.md`](coverage-gaps.md).

**Declared charge ≠ paid charge.** The bytes in the always-load set multiply by the number of
turns through `cache_read`, and the ratchet cannot see that multiplier. `scripts/token_trend.py`
measures observed raw-token consumption (#464, #565), from Claude Code transcripts by branch and turn.

### Decision records

`docs/architecture/` is reference: how the code works. `docs/adr/` is explanation: why a decision
was made this way and which alternatives were rejected (MADR 4.0.0, append-only).

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
  the context for which it was opened. `tests/test_adr_records.py` holds the structure. Whether a
  decision merits a record is a cost-of-change judgement made in the change's design, not a gate.

### Documentation guards

For mapped Markdown files, the sole accepted question marker is
`**Question this document answers:**`, before the first `## `. The `_MARKERS` expectation is this
single English marker; adding an alternative is a policy change, not a per-file test exception.

**What counts as a mapped file** (#421): **`.md` under `docs/architecture/` and
`.claude/rules/`**. This is the only scope rule; no second layer filters it. Two clarifications
explain why the boundary is here rather than expanding the rule:

- **`.claude/commands/*.md` are outside scope not as punishment, but
  because they already have a header — frontmatter `description:`.** Requiring a marker line too
  would keep the canon in two places. The converse confirms the boundary: `.claude/rules/testing.md`
  frontmatter has `paths:` but no `description:`, so its marker line is required and the scope
  already provides that.
- **`CLAUDE.md` is consciously excluded**: it is a thin router, marked ❌ kitchen-sink in the File
  map; requiring a single question from it would freeze with a gate the role from which it should be
  freed.
- **`docs/adr/` lies outside `docs/architecture/` for the same reason.** A MADR record has its own
  header (frontmatter `status`/`date` + decision title), so requiring a marker line too would keep
  the canon in two places — the same argument as for `.claude/commands/`. The directory still has an
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
in `CHECKS`: CI runs every registry entry as its own `--only` job, so an entry would add a CI job
for a static check that `pytest` already runs. Scope is
derived from a glob so the next architecture document enters the rule automatically. `tests/test_doc_links.py`
(#427) guards pointer integrity (an ID is an address): every internal link and code span of the form
`` `file.md#anchor` `` must resolve, otherwise a renamed section silently breaks all incoming anchors.
The mechanics of both are in
[`ci-workflow.md`](ci-workflow.md#doc-guards).

**Presence ≠ correctness.** Lint guarantees that a docstring *exists* and is non-empty, not that it
is *current*: an outdated non-empty docstring passes. The Markdown guard is the same: it guarantees
only that there is **something to dispute** about a file boundary, not that the header matches its
contents. A human catches docstring ↔ actual-purpose divergence in review — the honest §IV
position (a green detector that provides false coverage is worse than an honest "a human reviews
it").

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
  mentions of `#N` out of 300). The route is [§Decision records](#decision-records) above; the state document retains the
  decision, one sentence explaining why it remains valid, and a link.
- **Ideas, tasks, roadmaps, and unimplemented initiatives → GitHub issues** (they survive moving to
  another machine just as the repository does; that is their durable home). Precedent: an attempt
  to put the trailer-initiative roadmap in `docs/initiatives/` was rejected (#188), and the scope
  itself is distributed across the initiative's issues (#138–#145).

The backlog and status tracker is an instance of this umbrella, not a separate rule: it lives in
issues (remaining debt — [#177](https://github.com/ekolvah/kinozal_scraper/issues/177)), a special
case of "what is not currently implemented state does not live in `docs/`".
