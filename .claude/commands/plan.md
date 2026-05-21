---
description: Структурировать body GitHub issue по 6 required секциям перед /implement
argument-hint: <issue-number>
---

# /plan N — структурировать body issue

`$ARGUMENTS` = номер issue.

1. `python scripts/validate_issue_sections.py $ARGUMENTS` — exit 0 значит план уже полный, отчитайся пользователю и выйди.
2. Если exit 1 — script напечатал список дыр. Закрой их короткими вопросами (≤3 на сессию), черпая контекст из репо (`Read`/`Grep`), а не из user'а.
3. `gh issue edit $ARGUMENTS --body "<полный текст со всеми 6 секциями>"`. Не выбрасывай существующий текст — только дополняй и реструктурируй.
4. Повтори шаг 1. Если снова exit 1 — итерируй. Лимит 3 итерации, потом hand-off.
5. На выходе: ссылка на issue + предложение `/implement #$ARGUMENTS`.

`## Test plan` должен содержать конкретные `tests/<file>.py::<Class>::<test>` — это контракт RED-шага.
`## Docs to update` — список `.md` или явное «нет — behaviour не меняется».

Не пиши код, не создавай ветку, не трогай label'ы — это работа `/implement` и issue templates.
