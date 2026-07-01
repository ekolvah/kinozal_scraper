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
