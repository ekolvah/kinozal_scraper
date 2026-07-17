---
description: Исполнить план из issue с TDD red-green циклом (branch → RED → GREEN → docs → PR → CI-fix → review-fix×1)
argument-hint: <issue-number>
---

# /implement N — исполнить план из issue с TDD red-green

`$ARGUMENTS` = номер issue. Каждый шаг — детерминированный скрипт или внешний gate; правила в текстах команд агент забывает, скрипты — нет.

1. **Pre-flight**: `python scripts/validate_issue_sections.py $ARGUMENTS`. exit ≠ 0 → abort, попроси user'а запустить `/plan #$ARGUMENTS`.
2. **Branch**: `python scripts/issue_branch.py $ARGUMENTS` (slug из title, ветка от свежего origin/main).
3. **RED**: напиши тесты по `## Test plan` issue body. Только тестовый код, без production-изменений. Затем `python scripts/check_red.py <test paths>`. exit ≠ 0 → abort: либо тесты уже зелёные (плохой test plan), либо тесты не собрались. Когда RED — commit: `test: failing tests for #$ARGUMENTS`.
4. **GREEN**: реализуй `## Implementation outline`. Запускай `python -m pytest <files>` инкрементально, пока RED-тесты не позеленеют и существующая suite не сломалась.
5. **Docs**: обнови `.md` из `## Docs to update`. Если сознательно отклонил покрытие (scope-/cost-skip, live-E2E как negative-ROI) — запиши это в ledger `docs/architecture/testing.md#consciously-accepted-coverage-gaps` руками; автогенерации инвентаря тестов нет.
6. **Gate**: `python scripts/ci_check.py`. Red → root cause, не workaround. `.githooks/pre-push` всё равно повторит этот gate.
7. **Commit + push + PR**: явные пути в `git add`, commit (msg БЕЗ `(closes #N)` — squash пересобирает commit из заголовка PR и выбрасывает тело, keyword теряется; линковку несёт body PR), push, затем `python scripts/open_pr.py --title "<title>" --body-file <path>` (body из `.github/pull_request_template.md`). Скрипт форсит английский `Closes #$ARGUMENTS` в body и пост-верифицирует `closingIssuesReferences`; **exit ≠ 0 → abort/fix** (не создавай PR через ручной `gh pr create` — именно это в #319 оставило issue открытым). Backstop независимо от этого шага — CI-job `pr-link` (`scripts/verify_pr_link.py`) валит PR из `issue-N` ветки без линковки, так что ручной `gh pr create` без `Closes #N` всё равно упрётся в required check.
8. **CI loop**: `gh pr checks <pr> --watch`. При red — `gh run view <id> --log-failed`, фикс, push. Лимит 3 итерации без снижения числа failures — hand-off.
9. **Review loop (1 проход)**: после комментария `claude-review.yml` — `gh pr view <pr> --comments`, применить findings одним коммитом `review: address claude-review feedback for #$ARGUMENTS`, push. Второй проход не делать.
10. **Hand-off**: дай user'у ссылку на PR, статус checks, явное «merge — твой шаг».

Запреты (источник истины — `permissions.deny` в трекаемом `.claude/settings.json`; список здесь — self-check, синхронность проверяет `tests/test_settings_deny.py`): push в main, `gh pr merge`, `--no-verify`, `git reset --hard`, `git push --force`, `git branch -D`.

deny — defense-in-depth для типовых форм команд, **не герметичный sandbox** (Claude Code матчит по разбору команды; цепочки/`bash -c`/env-vars его обходят). Авторитетный барьер для `main` — **GitHub branch protection** (require PR, restrict push/merge), а не локальный deny.
