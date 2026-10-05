# Proposal

## Why

Epic #614 makes the agent-process plugin the only owner of the process layer; a gap becomes an
upstream issue, not a local file. Upstream closed the documentation-policy request as not
planned (`ekolvah/agent-process-distribution#314`, `#341`): the closing comment on `#341` reads
"The memory rule is already enforced by the memory_checkpoint hook, the CLAUDE.md and rules
loading facts are platform documentation the harness already gives the agent, and one-home alone
did not justify a skill section … The consumer deletes its portable parts without a
replacement: ekolvah/kinozal_scraper#615." The same issue body records that the language check
"served a one-time Russian-to-English migration, and no recurrence has been observed".

Observed on `main` @ `68d38ff`:

- `scripts/check_language.py` runs as the `language` entry of `CHECKS` (`scripts/ci_check.py:59`,
  `:247`); `tests/test_language_policy.py` tests it and pins the registry entry (`:135`).
  ADR-0005 `MIGRATION_ALLOWLIST` is already empty (`test_migration_allowlist_is_empty`).
- `docs/architecture/information-architecture.md` carries the portable policy (§Knowledge-carrier
  tier model, §Canonical-home rule's generic part, §Memory ↔ repository) and the language section
  next to repository-specific material (`docs/adr/` directory policy, doc-header, link and
  narrative guards).
- `docs/architecture/coverage-gaps-quality-gates.md:29-32` records that dropping the language gate
  "silently reopens" the Russian return path of the `.claude/agents/*.md` wording denylist.
- ADR-0005 names `check_language.py` as its Confirmation and rejects "no language gate"; the
  maintainer chose (2026-10-04) to record the changed decision as a new ADR that supersedes it.

## What Changes

- Delete `scripts/check_language.py`, `tests/test_language_policy.py`, and the `language` entry
  of `CHECKS`, with no replacement check.
- `information-architecture.md`: delete §Documentation and commentary language policy (moving
  the one sentence on the closed question marker into the doc-header section), §Knowledge-carrier
  tier model (keeping the `docs/architecture/`/`docs/adr/` roles and the always-load budget gate
  paragraphs), the generic part of §Canonical-home rule, and §Memory ↔ repository. Keep §Two graph
  layers, the decision-rationale route, the `docs/adr/` directory policy, the doc-header / link /
  narrative guards, and §What documentation describes.
- New ADR-0014 "Drop the documentation-language gate": English stays the documentation language,
  enforcement is review only. ADR-0005 status → `superseded by ADR-0014`.
- Update every live reference to the removed check or sections: `ci-local.md`,
  `coverage-gaps-quality-gates.md`, `CLAUDE.md`, `ci-tooling-decisions.md`, `project-map.md`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. No spec exists under `openspec/specs/`, and the change removes a developer-tooling check and
documentation only; `.openspec.yaml` sets `skip_specs: true`.

## Impact

- Removed: `scripts/check_language.py`, `tests/test_language_policy.py`.
- Added: `docs/adr/0014-drop-the-documentation-language-gate.md`.
- Edited: `scripts/ci_check.py`, `docs/architecture/information-architecture.md`,
  `docs/adr/0005-english-repository-documentation.md` (status line only),
  `docs/architecture/ci-local.md`, `docs/architecture/coverage-gaps-quality-gates.md`,
  `docs/architecture/ci-tooling-decisions.md`, `docs/architecture/project-map.md`, `CLAUDE.md`,
  `tests/test_doc_headers.py` (one comment), `tests/test_adr_records.py` (pointer comments and one
  failure message).
- CI: the plugin's quality workflow runs `--only <name>` per entry of `ci_check.py --list-checks`
  (`.github/agent-process-quality.json:4`, `ci-local.md:69-76`), so the
  `language` job disappears with the registry entry; no workflow file changes. The live ruleset
  (`gh api repos/ekolvah/kinozal_scraper/rulesets/<id>`, read by the architect review) requires
  only `agent-process / quality` and `agent-review / agent-review`
  (`docs/architecture/ci-branch-protection.md:9-16`), so no required `language` check can block
  merges.
- Not touched: `principles.md`, `mindset.md`, `scripts/hooks.py` memory check — #612 deletes them;
  their links to `information-architecture.md` keep resolving to the file.
