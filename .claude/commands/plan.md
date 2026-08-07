---
description: Структурировать body GitHub issue по 9 required секциям перед hand-off implementer'у
argument-hint: <issue-number>
---

# /plan N — структурировать body issue

Claude-интерфейс к роли `planner`. Шаги, лимиты и контракты секций — канон в
[`../../docs/architecture/agent-process.md#planner-runbook`](../../docs/architecture/agent-process.md#planner-runbook);
сюда не копируются. Здесь только то, что специфично для этого харнесса.

`$ARGUMENTS` = номер issue.

0. Если сессия тянется с прошлой задачи (issue/PR уже доведён до hand-off) — попроси
   user'а запустить `/compact` до старта. Сам вызвать не можешь, это built-in CLI.
1. Пройди planner runbook по канону. Контекст для дыр черпай из репо (`Read`/`Grep`).
2. Architect review на шаге 3 runbook'а выполняет **субагент `architect-reviewer`**
   (персона — `.claude/agents/architect-reviewer.md`), а не сама сессия.
3. Запись body — `gh issue edit $ARGUMENTS --body "<полный текст со всеми 9 секциями>"`.
4. На выходе: ссылка на issue + передача `implementer`'у. Точку входа выбирает user: Codex
   `$implement-issue #$ARGUMENTS` (дефолт каталога) либо `/implement $ARGUMENTS` в этой же сессии.
