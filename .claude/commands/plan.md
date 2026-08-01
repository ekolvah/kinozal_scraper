---
description: Структурировать body GitHub issue по 9 required секциям перед hand-off implementer'у
argument-hint: <issue-number>
---

# /plan N — структурировать body issue

`$ARGUMENTS` = номер issue.

0. Если сессия тянется с прошлой задачи (issue/PR уже доведён до hand-off) — попроси user'а запустить `/compact` до старта. Сам вызвать не можешь, это built-in CLI.
1. `python scripts/validate_issue_sections.py $ARGUMENTS` — exit 0 значит план уже полный, отчитайся пользователю и выйди.
2. Если exit 1 — script напечатал список дыр. Закрой их короткими вопросами (≤3 на сессию), черпая контекст из репо (`Read`/`Grep`), а не из user'а.
3. **Architect review** — прогони собранный план через субагента `architect-reviewer` (персона в `.claude/agents/architect-reviewer.md`). Его сводку findings (BLOCKING/SHOULD-FIX/NICE/OK) положи в секцию `## Architect review`, а BLOCKING-замечания вплети в остальные секции до записи. Для тривиальной правки (опечатка/однострочник) ревью не нужно — впиши в секцию `skipped: <причина>`. Один проход, не зацикливаться.
4. Заполни `## Agent handoff`: `planner: Claude [<model if known>]`, успешный вызов валидации, `next role: implementer`, `handoff: ready`. Затем `gh issue edit $ARGUMENTS --body "<полный текст со всеми 9 секциями>"`. Не выбрасывай существующий текст — только дополняй и реструктурируй.
5. Повтори шаг 1. Если снова exit 1 — итерируй. Лимит 3 итерации, потом hand-off.
6. На выходе: ссылка на issue + предложение запустить Codex `$implement-issue #$ARGUMENTS`. Каталог ролей и advisory-роутинг живут в `.agents/orchestration/roles.yaml` и `scripts/agent_orchestrator.py`; это общий контракт, а не ещё одна Claude-команда.

`## Test plan` должен содержать конкретные `tests/<file>.py::<Class>::<test>` — это контракт RED-шага.
`## Docs to update` — список `.md` или явное «нет — behaviour не меняется».
`## Architect review` — findings `architect-reviewer` либо `skipped: <причина>`; гейт не даёт implementer'у стартовать с пустой секцией.
`## ADR` — ссылка на запись в `docs/adr/` (шаблон `docs/adr/template.md`) либо явное `none: <причина>`. Запись заводится на решение с **высокой ценой разворота**, задевающее несколько модулей или доков; маршрут и cost-of-change фильтр — `project-map.md` §Canonical-home. Планка узкая намеренно: `none:` — штатный, а не аварийный ответ. Заведённая запись идёт и в `## Docs to update`.

Не пиши код, не создавай ветку, не трогай label'ы — это работа implementer'а и issue templates. Канон ролей и hand-off — `docs/architecture/agent-process.md`.
