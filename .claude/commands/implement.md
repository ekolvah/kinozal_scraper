---
description: Довести распланированную issue до PR — ветка, RED→GREEN, docs, CI-гейт, review/fix-цикл
argument-hint: <issue-number>
---

# /implement N — довести issue до PR

Claude-интерфейс к ролям `implementer` и `fixer`. Шаги, гейты, exit-коды и правило остановки
цикла — канон в
[`../../docs/architecture/agent-process.md#deterministic-delivery-flow`](../../docs/architecture/agent-process.md#deterministic-delivery-flow)
и
[`../../docs/architecture/agent-process.md#review-gate-verdicts`](../../docs/architecture/agent-process.md#review-gate-verdicts);
сюда не копируются. Здесь только то, что специфично для этого харнесса.

`$ARGUMENTS` = номер issue.

0. Если сессия тянется с прошлой задачи (issue/PR уже доведён до hand-off) — попроси
   user'а запустить `/compact` до старта. Сам вызвать не можешь, это built-in CLI.
1. Пройди delivery flow по канону. План не выдумывай: провалившийся
   `validate_issue_sections.py` — это возврат к `planner`, а не повод достроить план самому.
2. Правки файлов — `Edit`/`Write`, не heredoc-скрипт ([mindset](../rules/mindset.md)).
3. `ci_check.py` и `git push` идут минуты — **один foreground-вызов с поднятым `timeout`**,
   без фона и без polling-цикла (тайминги и грабли — `CLAUDE.md` §Среда).
4. На выходе: ссылка на PR, вердикт `review_gate` и явное «merge — твой шаг».
