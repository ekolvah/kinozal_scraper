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

1. **RED first** (§I) — сначала падающий тест из issue `## Test plan`, потом код.
   Исключения §I(a/b/c): rename/move уже покрытого символа, docs-only, one-line non-behavioural.
2. **Никаких моков внутренней логики** (§II) — `run_*_pipeline`/`_extract_*` вызывай напрямую;
   внешние границы → Protocol-double (`InMemoryStorage`, `InMemoryNotifier`, `NullEnricher`)
   или сохранённый HTML/JSON-фикстур.
3. **Уровень теста** выбирай по [bug-taxonomy](../../docs/architecture/testing.md#bug-taxonomy)
   (integration-first → unit для pure-функций → e2e smoke перед merge для structure-drift).
4. **Прогон** — `python -m pytest` инкрементально; перед коммитом `python scripts/ci_check.py`.
5. **Структура тестов изменилась** (новый файл/класс) — `test-coverage.md` регенерится
   `ci_check`'ом (`python scripts/gen_test_coverage.py`); инвентарь руками не правь.
