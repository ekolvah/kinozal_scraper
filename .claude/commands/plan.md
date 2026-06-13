---
description: Структурировать body GitHub issue по 7 required секциям перед /implement
argument-hint: <issue-number>
---

# /plan N — структурировать body issue

`$ARGUMENTS` = номер issue.

1. `python scripts/validate_issue_sections.py $ARGUMENTS` — exit 0 значит план уже полный, отчитайся пользователю и выйди.
2. Если exit 1 — script напечатал список дыр. Закрой их короткими вопросами (≤3 на сессию), черпая контекст из репо (`Read`/`Grep`), а не из user'а.
3. **Architect review** — прогони собранный план через субагента `architect-reviewer` (персона в `.claude/agents/architect-reviewer.md`). Его сводку findings (BLOCKING/SHOULD-FIX/NICE/OK) положи в секцию `## Architect review`, а BLOCKING-замечания вплети в остальные секции до записи. Для тривиальной правки (опечатка/однострочник) ревью не нужно — впиши в секцию `skipped: <причина>`. Один проход, не зацикливаться.
4. `gh issue edit $ARGUMENTS --body "<полный текст со всеми 7 секциями, включая ## Architect review>"`. Не выбрасывай существующий текст — только дополняй и реструктурируй.
5. Повтори шаг 1. Если снова exit 1 — итерируй. Лимит 3 итерации, потом hand-off.
6. На выходе: ссылка на issue + предложение `/implement #$ARGUMENTS`.

`## Test plan` должен содержать конкретные `tests/<file>.py::<Class>::<test>` — это контракт RED-шага.
`## Docs to update` — список `.md` или явное «нет — behaviour не меняется».
`## Architect review` — findings `architect-reviewer` либо `skipped: <причина>`; гейт не даёт `/implement` стартовать с пустой секцией.

Не пиши код, не создавай ветку, не трогай label'ы — это работа `/implement` и issue templates.
