---
status: "accepted"
date: 2026-08-20
decision-makers: ekolvah
---

# Agentic-process distribution to a new repository uses copier plus the Claude Code plugin marketplace, not a hand-rolled sync script

## Context and Problem Statement

This repository carries a full agentic-process contract: provider-neutral core
(`docs/architecture/agent-process.md`, `principles.md`,
`.agents/orchestration/roles.yaml`, `change-classes.yaml`, the gate scripts
under `scripts/`), a Claude adapter (`.claude/commands`, `.claude/agents`,
`.claude/rules`, hooks), and a Codex adapter (`.agents/skills/*/SKILL.md`).
A second project — already real, not hypothetical — will run both Claude and
Codex and needs the same contract. Re-deriving it from scratch discards a
working design; copying files by hand loses versioning and any way to tell
"in sync" from "silently drifted".

`open_pr.py`/`check_red.py` verify a closing PR inside this repository; they
cannot verify work that creates and populates a different repository. This
record answers only the mechanism question — how a future export moves the
contract and stays in sync — so a separate export follow-up does not reopen it.

## Decision Drivers

* A vendored copy needs a **built-in** drift check, not a "remember to
  re-sync" discipline — an unenforced sync script is exactly the silent-skip
  design [`principles.md` §IV](../architecture/principles.md#iv-visibility-over-silence) forbids.
* Part of the core is **not** verbatim-portable: `scripts/set_issue_priority.py`
  and `scripts/set_issue_status.py` embed this repository's GitHub Project
  IDs, and `scripts/check_branch_protection.py` embeds this repository's
  required-context list. A copy mechanism that cannot parameterize these
  ships a contract that is silently wrong in the new project.
* The existing adapter split (`roles.yaml`: provider-neutral core vs. named
  Claude/Codex adapters) already answers "what is generic" for this
  repository; the distribution mechanism should carry that split forward,
  not collapse it into one bundle.
* No new paid or live infrastructure: the mechanism must run on `git` and
  `gh`, already required by every other gate in this repository.
* Claude Code already has an **official** distribution channel
  (plugin + marketplace) for its own adapter; re-inventing one would be
  duplicate work for a solved problem.

## Considered Options

* copier (template repository + Jinja parameterization + `copier update`)
* cruft (cookiecutter-based, `cruft check` gate, diff-based update, no
  per-project value templating)
* git subtree (verbatim snapshot vendoring, upstream commit recorded in the
  merge commit, `git subtree pull --squash` to sync)
* A hand-rolled `init.sh`/`sync.sh` pair
* A live dependency (git submodule, or a plugin that fetches at session
  start) instead of pull-on-demand
* Third-party Codex/Claude skill-sync tools (`skillshare`,
  `codex-skills-registry`)
* Claude Code plugin marketplace, for the Claude-specific adapter files

## Decision Outcome

Chosen: a **layered** mechanism, matching the three layers `roles.yaml`
already implies.

* **Layer 0 (provider-neutral core)** and **Layer 2 (Codex `SKILL.md`
  adapter)** distribute through **copier**. Both layers contain content this
  repository's own audit (`docs/architecture/agent-process-export.md`) marks
  "generic templated" — values that must change per project (repository
  name, GitHub Project IDs, required-context lists) — which cruft and git
  subtree do not parameterize at all. Once the template is published from a
  versioned source, `copier update` gives drift detection and a 3-way merge for
  free, so the target project never has to trust "we remembered to re-sync" as
  the only claim standing between it and staleness. An in-tree build records
  the source path and selected answers but has no source `_commit` to update.
* **Layer 1 (Claude-specific adapter files)** distributes through the
  **official Claude Code plugin marketplace** (`.claude-plugin/plugin.json` +
  `marketplace.json`, `/plugin marketplace add` + `/plugin install`).
  Nothing here needs building: the update is explicit
  (`/plugin marketplace update`, never silent), and the mechanism already
  exists.

"Build" is therefore scoped narrowly: preparing the copier template
repository's content (from the export manifest) and its Jinja parameter set,
not inventing sync mechanics for either layer.

### Consequences

* Good, because both layers get drift detection from a maintained tool
  instead of a repository-specific script this project would then have to
  maintain and test forever.
* Good, because the parameterization gap that ruled out git subtree and
  cruft is closed: project-specific values live in one place (the copier
  `data` file) instead of a manual find-and-replace after every copy.
* Good, because Layer 1 needs no new mechanism at all — the official plugin
  channel already gives explicit, auditable updates.
* Bad, because copier introduces a new tool dependency (Python package,
  Jinja templating) for a repository that otherwise depends only on `git`
  and `gh` for process tooling. Accepted: the alternative is a hand-rolled
  equivalent with none of copier's testing or update tooling behind it.
* Bad, because two distribution mechanisms (copier, plugin marketplace)
  now exist for one process instead of one. Accepted: they track the two
  layers that already have different content (parameterized core vs.
  provider-native adapter), so one mechanism would have had to force one of
  them into a shape it does not fit.

### Confirmation

This record and `docs/architecture/agent-process-export.md` are the
mechanism decision and the per-file export status; both are gated by the
existing structural guards (`tests/test_adr_records.py`,
`tests/test_doc_headers.py`, `tests/test_doc_links.py`,
`tests/test_doc_narrative.py`). Confirming that the mechanism actually works
— a real copier template producing a real second-project checkout — is the
scope of the follow-up issue that builds it, not of this record.

**2026-08-21 confirmation.** The Claude Code plugin documentation has neither a
`permissions` nor a `rules` field in `plugin.json`; the requested `rules` field
remains unshipped upstream. The premise that these project files could travel
in the marketplace plugin was therefore false. `.claude/rules/*.md` and
`.claude/settings.json` remain Layer 1 Claude-adapter content, but distribute
through its Copier channel instead; the plugin channel is limited to commands
and agents.

## Pros and Cons of the Options

### copier

* Good, because Jinja parameterization covers exactly the values this
  repository's audit found non-portable.
* Good, because a versioned Copier source provides a maintained 3-way-merge
  drift check, not a script this project would own.
* Neutral, because it adds a build step (authoring the template and its
  `copier.yml`) that a plain file copy would not need.

### cruft

* Good, because `cruft check` is a maintained drift gate, same as copier.
* Bad, because it has no native value templating: project-specific IDs would
  still need manual find-and-replace after every generation.

### git subtree

* Good, because it is verbatim and needs no template authoring at all.
* Bad, because it has no parameterization mechanism, so project-specific
  IDs would ship wrong by default; the sync step (`git subtree pull`) is
  correct but does not solve that gap.

### Hand-rolled `init.sh`/`sync.sh`

* Bad, because it has no built-in drift check — exactly the silent-skip
  pattern `principles.md` §IV forbids, and would duplicate what copier or
  cruft already does.

### Live dependency (submodule / fetch-at-session-start)

* Bad, because it trades predictability for freshness: a target project's
  agent-process contract would change under it without an explicit local
  action, unlike a pull-on-demand update.

### Third-party skill-sync tools

* Bad, because they are unaudited dependencies for a problem already solved
  by the vendor-official mechanism (Layer 1) or by copier (Layer 2).

### Claude Code plugin marketplace

* Good, because it is the vendor-official mechanism for exactly this
  content, with explicit (not silent) updates.
* Neutral, because it only covers Layer 1; it was never a candidate for
  Layer 0/2's parameterization need.

## More Information

* Overlaps with a future audit replacing bespoke repository scripts with
  market tools on domain, not question: that audit decides whether a script
  should exist; this record decides how an existing contract is
  distributed. A later replacement flows to the
  vendored copy through the same `copier update` as any other change.
* Conflicts with an MCP server over the dev scripts — one install
  instead of a per-project copy — on distribution mechanism. It must reconcile
  with this choice when implemented, not
  the other way around, because this record is accepted first.
* Revisit this record if a target project's parameterization needs outgrow
  what a single `copier.yml` answer set can express, or if the Claude Code
  plugin marketplace stops supporting explicit (pull-on-demand) updates.
* Revisit the Layer 1 channel assignment if upstream ships a `rules` field for
  Claude Code plugins.
