<!--
PR template для kinozal_scraper. Все секции обязательны.

PR — это ОТЧЁТ по плану из issue (что реально сделано + доказательство), а не копия
issue. `Test plan` и `Docs touched` зеркалят одноимённые issue-секции как ПЛАН→ФАКТ
(галочки = прогнано). `Risk & Rollback` — delivery-only: у неё counterpart'а в issue
нет и быть не может (blast-radius известен только по факту диффа).

Если issue нет (тривиальный фикс) — всё равно заполни все секции.
-->

## Summary

<!--
2-3 предложения: что сделано и зачем. Linker `Closes #N` (или `Refs #N`) обязателен.
Divergence: совпало с issue `## Implementation outline`? Отклонения/сюрпризы — одной строкой
(или «совпало с планом»).
-->

Closes #

## Agent record

<!--
Короткая provenance-запись, без промптов или chain-of-thought.
- Implementer: <agent / model or version if known>
- Reviewer / fixer: <agent or none>
- CI evidence: <local ci_check result and PR checks URL/status>
- Route: <roles selected by the control plane>
- Model invocations: <role=count; completed run-count proxy at the time this record is written, not invented token totals; exclude a review triggered by a later push>
- Fixer revisions: <count>
- Conditional skips / escalations: <reason or none>
-->

- Implementer:
- Reviewer / fixer:
- CI evidence:
- Route:
- Model invocations:
- Fixer revisions:
- Conditional skips / escalations:

## Test plan

<!--
Markdown-чеклист. Должен зеркалить issue'шный `## Test plan` с галочками если прогнано.
Плюс локальные команды, которые ты реально запускал.
-->

- [ ] `python scripts/ci_check.py` — green локально
- [ ] CI на PR — green

## Risk & Rollback

<!--
Проверяемо за 30 сек. Для тривиального изменения — одна строка «low risk, revert-safe».
- Blast-radius: изменение изолировано или задевает несвязанное (крон-пайплайн,
  Sheets-дедуп, Telegram-доставка, Gemini)?
- Rollback: чистый `git revert` PR — или есть необратимые эффекты (уже отправленные
  Telegram-сообщения, записи в Google Sheets)?
- Мониторинг: за чем следить после мержа (ближайший крон-ран run-script.yml)?
-->

## Docs touched

<!--
Список изменённых `.md` файлов (docs/architecture/*, CLAUDE.md, MEMORY.md, …) или явно "none — behaviour unchanged". Должен зеркалить issue'шный `## Docs to update`.
-->
