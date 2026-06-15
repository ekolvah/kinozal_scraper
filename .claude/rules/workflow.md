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
5. **Issues carry labels** — every `gh issue create` includes `--label
   bug|enhancement|documentation|testing|...`.
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
