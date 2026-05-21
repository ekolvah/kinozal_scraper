# /implement #N — исполнить план из issue с TDD red-green

Цель: одной командой выполнить весь pipeline (branch → RED тесты → GREEN код → docs → commit → push → PR → fix CI → fix review × 1), не давая агенту «забыть» правила.

## Когда использовать

- После `/plan #N` (или когда issue уже содержит все 6 required секций).
- НЕ для: тривиальных правок без issue — для них ручной workflow.

## Аргумент

`$ARGUMENTS` — номер issue. Если пусто — abort.

## Pre-flight (BLOCKING)

1. **Прочитать issue**: `gh issue view $ARGUMENTS --json number,title,body,labels,state`.
2. **Проверить state**: должен быть `OPEN`. Иначе — abort.
3. **Проверить required sections** в body (см. `/plan`):
   - `## Context / Why`, `## Acceptance criteria`, `## Test plan`, `## Implementation outline`, `## Docs to update`, `## Out of scope`.
   - Если любая секция отсутствует или пуста — abort с сообщением: «issue #N не структурирован — запусти `/plan #N` сначала».
4. **Проверить ветку**: если уже на `codex-issue-N-*` — продолжить на ней. Иначе — следующий шаг.

## Pipeline

### 1. Branch

`python scripts/new_branch.py codex-issue-$ARGUMENTS-<slug>` где `<slug>` — 2-4 слова kebab-case из title issue.

Скрипт сам делает checkout main + pull --ff-only + checkout -b. Никогда не вызывать `git checkout -b` напрямую (см. #66).

### 2. RED step — тесты сначала, должны упасть

1. Парсить `## Test plan` из issue body — список тест-кейсов `tests/<file>.py::<Class>::<test_name>`.
2. Написать **только тестовый код**, без production-изменений. Тесты должны проверять поведение из `## Acceptance criteria`.
3. Запустить `pytest <files> -v` (только новые/изменённые тесты).
4. **Проверка**: тесты должны **упасть** (FAILED, не PASSED, не ERROR из-за импорта).
   - Если хотя бы один проходит с первого раза → **abort, escalate**: «тест `<name>` проходит без production-изменений. Значит он не покрывает планируемое поведение. Пересмотри `## Test plan` в issue и запусти `/implement` снова».
   - Если ERROR из-за импорта/синтаксиса — это допустимо, исправь импорты, но логика теста должна остаться red.
5. Commit отдельно: `git add <test files> && git commit -m "test: failing tests for #$ARGUMENTS"`.

### 3. GREEN step — production-код

1. Парсить `## Implementation outline` — список файлов и изменений.
2. Реализовать **минимально достаточный** код. Не делать рефакторинг сверх плана (scope creep блокировать).
3. Периодически запускать `pytest <files> -v`.
4. Цель: все RED-тесты из шага 2 проходят + ни один существующий тест не сломан (`python -m pytest`).
5. **Loop с escape hatch**:
   - Лимит: 5 итераций edit-test подряд без прогресса (метрика прогресса: число failing tests уменьшается).
   - Если 5 итераций → escalate: «не могу сделать тесты зелёными, нужен human input. Failing: <list>».

### 4. Docs step

1. Парсить `## Docs to update` — список `.md` файлов.
2. Обновить каждый. Если в плане «нет — behaviour не меняется» — пропустить.
3. Если меняется test structure (новый test file/class) — обновить `docs/architecture/test-coverage.md`.
4. Можно commit'ить вместе с production-кодом или отдельным commit'ом (по объёму).

### 5. Pre-push gate

`python scripts/ci_check.py` — обязательный gate. `.githooks/pre-push` запустит его автоматически при push, но прогон вручную даёт быстрый feedback.

Если ci_check red — исправить **root cause** (не bypass с `--no-verify`). Это включает: ruff format/lint, mypy, pytest всю suite, pip-audit, lockfile drift.

### 6. Commit + push + PR

1. Финальный commit (если ещё не закоммичено): `git add <explicit paths> && git commit -m "<type>(<scope>): <summary> (closes #$ARGUMENTS)"`.
   - `<type>`: `feat`/`fix`/`refactor`/`test`/`docs`/`chore`.
   - Никаких `git add -A`/`git add .` — только явные пути.
2. Push: `git push -u origin codex-issue-$ARGUMENTS-<slug>`.
3. PR: `gh pr create --title "<short>" --body "$(cat <<'EOF' ...EOF)"`. Body по `.github/pull_request_template.md`:
   - `## Summary`: 2-3 предложения + `Closes #$ARGUMENTS`.
   - `## Test plan`: markdown-чеклист, зеркалит issue test plan, галочки за прогнанное.
   - `## Docs touched`: список `.md` или «none — behaviour unchanged».

### 7. Test-fix loop на CI

После push'а CI начнёт прогон. Дождаться (`gh pr checks <pr> --watch` или периодически `gh pr checks <pr>`).

- Если все green → переход к шагу 8.
- Если red:
  - `gh run view <run-id> --log-failed` — прочитать failing logs.
  - Найти root cause (не workaround).
  - Исправить, commit, push.
  - **Лимит**: 3 итерации подряд без прогресса (метрика: число failing checks/tests уменьшается).
  - При исчерпании → escalate user'у: «CI не зелёный, root cause не очевиден. Failing: <list>».

### 8. Code-review-fix loop — ровно 1 итерация

1. Дождаться комментария от `claude-review.yml` workflow на PR.
2. Прочитать findings: `gh pr view <pr> --comments` + inline через `gh api repos/<owner>/<repo>/pulls/<pr>/comments`.
3. Применить **все** разумные findings одним коммитом: `git commit -m "review: address claude-review feedback for #$ARGUMENTS"`. Push.
4. **После этого — STOP**. Не делать второй проход. Hand-off user'у с резюме: «PR #<pr> готов к review, claude-review feedback применён, тесты зелёные».

### 9. Финальный отчёт

Дать пользователю:
- Ссылку на PR.
- Список созданных тестов (с галочками что прошли).
- Список обновлённых docs.
- Статус CI checks.
- Явное напоминание: «merge — твой шаг, я не мержу сам».

## Что НИКОГДА не делать

- НЕ пушить в `main` напрямую (deny-list, плюс правило).
- НЕ мержить PR (`gh pr merge` запрещено без явного user OK per-PR).
- НЕ использовать `--no-verify`, `--no-gpg-sign`, `git push --force origin main`.
- НЕ делать `git reset --hard`, `git branch -D` без user confirmation.
- НЕ менять issue body во время `/implement` (это работа `/plan`).
- НЕ делать второй прогон code-review-fix.
- НЕ скрывать degraded behaviour — следовать принципу Visibility Over Silence (см. principles.md IV).

## Escape hatches (когда честно сдаться)

| Шаг | Условие | Действие |
|-----|---------|----------|
| RED | все тесты green с первой попытки | abort, пересмотр test plan |
| GREEN local | 5 итераций без прогресса | escalate, дать user'у список failing |
| Test-fix CI | 3 итерации без прогресса | escalate, дать ссылку на failing run |
| Pre-push gate | ci_check red и причина непонятна | escalate, не bypass'ить |
| Review-fix | findings противоречат `## Out of scope` issue | hand-off с пометкой «нужен human решение по scope» |

## Ссылки

- Sections checklist: `.github/ISSUE_TEMPLATE/bug.yml` / `feature.yml`.
- PR template: `.github/pull_request_template.md`.
- Architectural principles (Test-First, Root Cause, Visibility Over Silence, …): [docs/architecture/principles.md](../../docs/architecture/principles.md).
- Workflow rationale: issue #114.
