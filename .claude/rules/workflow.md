# Development Workflow

**На какой вопрос отвечает этот файл:** какие процедурные правила обязательны при разработке
(создание ветки, PR-дисциплина, labels, plan→implement, гейты, зависимости, architect-review).
Это **канонический дом** операционных процедурных правил проекта (делегирован из
[`docs/architecture/principles.md §Governance`](../../docs/architecture/principles.md); IA-policy —
[`docs/architecture/project-map.md`](../../docs/architecture/project-map.md)). **Не добавляй сюда
контент, не отвечающий на этот вопрос** (формулировки принципов §I–VI — в `principles.md`;
git-запреты — в `.claude/settings.json` `permissions.deny`).

Эти процедурные правила дополняют принципы §I–VI и связывают **наравне** с ними:

1. **Branch creation** — new work happens on `issue-N-<slug>` branches
   created **only** via `python scripts/new_branch.py <name>`. Direct
   `git checkout -b` is forbidden; the script guarantees the branch starts
   at `origin/main` HEAD, preventing squash-merge divergence (issue #66).
2. **No direct main pushes** — main is updated exclusively through PRs.
3. **No self-merge** — `gh pr merge` is forbidden for the AI assistant
   without explicit per-PR human approval. The human reviewer keeps the
   merge button.
4. **One PR, one logical unit** — docs-only PRs are separate from
   refactor/feature PRs (precedent: #39 → #40 had to be redone after mixing).
   **Pragmatic exception:** a *temporary* CI unblock of an **unrelated**
   failing test (e.g. an E2E reddening a docs-PR via an external 520) goes
   into the branch being merged — with a **tracked follow-up issue** for the
   real fix — not a separate off-main PR (that costs an extra merge+rebase
   round-trip). Permanent changes are still split.
5. **Issues carry labels** — every `gh issue create` includes `--label
   bug|enhancement|documentation|testing|...`. Semantics: `bug` = something
   broken / current behaviour wrong; `enhancement` = improvement / new feature
   / quality; `documentation` = `*.md`/`docs/`-only; `testing` = test-coverage
   work. Full set: `gh label list` (don't hard-code the list here — it drifts).
6. **Pre-commit gate** — `python scripts/ci_check.py` runs ruff format +
   lint + pytest + mypy + pip-audit + lockfile drift. The `.githooks/pre-push`
   hook runs it automatically; do not bypass with `--no-verify`.
7. **Dependency consistency** — when a `requirements*.in` changes,
   `pip-compile` regenerates the corresponding `.txt` in the same commit.
8. **Plan-driven flow** — substantive new features and bug fixes are
   authored via the project's local workflow: `/plan #N` writes a
   structured plan into the issue body (Context / Acceptance / Test plan /
   Implementation outline / Docs to update / Out of scope / Architect
   review — canon набора секций: `REQUIRED_SECTIONS` в
   `scripts/validate_issue_sections.py`, эта проза ему подчинена), then
   `/implement #N` executes it with TDD red-green discipline.
   Trivial fixes (typos, single-line non-behavioural tweaks) may skip the
   workflow. See #114 for the rationale and exact contract.
9. **Architect review gate** — the issue body MUST carry a non-empty
   `## Architect review` section before `/implement` runs; the existing
   `scripts/validate_issue_sections.py` gate enforces it (no separate
   script). `/plan` fills it by running the `architect-reviewer` subagent
   (`.claude/agents/architect-reviewer.md`) over the drafted plan, whose
   goal function is: minimise future bugfix/support → dev+runtime tokens →
   predictability. Trivial issues record an explicit `skipped: <reason>` —
   the gate guarantees the review is a *consciously-decided step*, not a
   silently-forgotten one. **Rationale:** a plan-stage architect review on
   2026-05-29 caught defects the first plan missed — a §IV silent-skip
   (`try/except: return ""`), a §II mock-of-internal-logic in a golden test,
   and runtime-token overspend (LLM per item vs. a cheap pre-filter). The
   reviewer persona used to live in out-of-repo Claude memory and was
   re-typed by hand and easily forgotten; codifying it in-repo + gating it
   makes the review reproducible for any contributor (#150).
10. **Dedup check before issue creation** — before `gh issue create`, run
    `git fetch` and scan recently-merged PRs / recently-closed issues for the
    same topic (`git log --oneline origin/main -10`, `gh issue list --state
    closed --limit 10`); if it overlaps, open that PR/issue and check scope
    before filing. **Prose, not a gate, on purpose:** the deterministic half
    (`git fetch`) is one command, but judging *topic overlap* is a semantic
    call — the same class as the semantic-dup detector we deliberately don't
    build (`docs/architecture/project-map.md`) — so a script wouldn't remove
    the human step and a gate doesn't pay off. `scripts/new_branch.py` only
    pulls fresh main at branch time, too late to catch the duplicate.
    **Precedent:** #125 re-filed the `validate_issue_sections.py` UTF-8/cp1252
    bug already fixed in merged PR #123 (`e1548385`, closing #122); the
    duplicate had to be closed by hand.
