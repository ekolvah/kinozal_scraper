---
paths:
  - "tests/**"
---

# Testing — operational checklist

**На какой вопрос отвечает этот файл:** какие шаги обязательны, когда пишешь или
правишь тесты. Это **операционный чеклист**, не дизайн-документ: формулировки
принципов — канон в [`principles.md §I`](../../docs/architecture/principles.md) (Test-First)
и [`§II`](../../docs/architecture/principles.md) (Protocol Boundaries + DI); стратегия
(уровни, bug-taxonomy, что мокать) — [`docs/architecture/testing.md`](../../docs/architecture/testing.md).
**Не перефразируй сюда принцип — только ссылайся** (path-scoped: грузится лишь при работе с `tests/**`).

1. **RED first** — падающий тест из issue `## Test plan` пишется до кода
   (правило и исключения — [`principles.md §I`](../../docs/architecture/principles.md)).
   Но прежде — **стоит ли тест писать вообще:** регресс ломает корректность/безопасность
   (→ тест) или только тратит ресурсы CI-минут/токенов (→ forcing-function, не guard-тест)?
   Канон — [`testing.md`](../../docs/architecture/testing.md#rule-when-a-test-is-not-worth-writing).
2. **Никаких моков внутренней логики** — действует [`principles.md §II`](../../docs/architecture/principles.md);
   как это выглядит в репо (какие границы внешние, какой паттерн) — [`testing.md`](../../docs/architecture/testing.md#rule-no-mocks-of-internal-functions).
3. **Уровень теста** выбирай по [bug-taxonomy](../../docs/architecture/testing.md#bug-taxonomy)
   (integration-first → unit для pure-функций → e2e smoke перед merge для structure-drift).
4. **Прогон** — `python -m pytest` инкрементально; перед коммитом `python scripts/ci_check.py`.
5. **Покрытие сместилось** (новый bug-class, закрытая дыра, инвертирован documents-current-bug
   тест) — обнови curated-таблицу в [`test-coverage.md`](../../docs/architecture/test-coverage.md)
   руками: это hand-curated карта «какой тест какой баг ловит», не авто-генерация.
