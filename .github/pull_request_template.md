<!--
PR template для kinozal_scraper. Контракт: все три секции обязательны. PR — это отчёт
по плану из issue. Если issue нет (тривиальный фикс) — всё равно заполни все секции.
-->

## Summary

<!-- 2-3 предложения: что сделано и зачем. Linker `Closes #N` (или `Refs #N`) обязателен. -->

Closes #

## Test plan

<!--
Markdown-чеклист. Должен зеркалить issue'шный `## Test plan` с галочками если прогнано.
Плюс локальные команды, которые ты реально запускал.
-->

- [ ] `python scripts/ci_check.py` — green локально
- [ ] CI на PR — green

## Docs touched

<!--
Список изменённых `.md` файлов (docs/architecture/*, CLAUDE.md, MEMORY.md, …) или явно "none — behaviour unchanged". Должен зеркалить issue'шный `## Docs to update`.
-->
