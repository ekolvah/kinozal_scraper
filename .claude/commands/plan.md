# /plan #N — структурировать план в body issue

Цель: превратить draft issue в полный, исполнимый план, который `/implement #N` сможет выполнить без догадок. План живёт **внутри body issue** (не в `specs/`-папках) — single source of truth.

## Когда использовать

- Любой новый bug/feature, который пойдёт в работу через `/implement`.
- НЕ для: исправления опечаток, однострочных non-behavioural твиков, чисто docs-PR. Для них — прямой workflow без `/plan`.

## Аргумент

`$ARGUMENTS` — номер issue (целое). Если пусто — abort с просьбой указать `#N`.

## Шаги

1. **Прочитать issue**: `gh issue view $ARGUMENTS --json number,title,body,labels,state`.
   - Если `state != OPEN` — abort: «issue #N closed, открой новый».
   - Если `labels` пусто — warn, спросить у пользователя какой label повесить, потом `gh issue edit $ARGUMENTS --add-label <label>`.

2. **Проверить наличие required sections** в `body`:
   - `## Context / Why`
   - `## Acceptance criteria`
   - `## Test plan`
   - `## Implementation outline`
   - `## Docs to update`
   - `## Out of scope`

   Для каждой секции — секция присутствует И содержит non-empty содержимое (не только placeholder из template).

3. **Заполнить недостающее**:
   - Если все 6 секций уже заполнены и осмыслены — план готов, отчитаться пользователю и выйти.
   - Иначе — провести с пользователем **максимум 3 коротких вопроса** по самым критичным дырам (scope > acceptance > test plan > impl outline > docs > out-of-scope). Не задавать вопросы про детали, которые можно вывести из кода (читать репо через `Read`/`Grep`).
   - На каждом этапе предлагать draft секции, user либо принимает либо корректирует.

4. **Особое внимание к `## Test plan`**:
   - Должен содержать **конкретные имена тестов** в формате `tests/<file>.py::<Class>::<test_name>`.
   - Это контракт для RED step в `/implement` — эти тесты будут написаны первыми и упадут.
   - Если поведение существующее меняется — указать, какой существующий тест нужно перевернуть/удалить.

5. **Особое внимание к `## Docs to update`**:
   - Перечислить все `.md` файлы, которые надо обновить в том же PR.
   - Минимум проверить: `docs/architecture/*.md` (если меняется архитектурное поведение), `CLAUDE.md` (если новое правило/грабли), `docs/architecture/test-coverage.md` (если добавляется новый test class/file).
   - Если behaviour снаружи не меняется — явно написать «нет — behaviour не меняется».

6. **Записать финальный body обратно в issue**:
   - `gh issue edit $ARGUMENTS --body "<полный текст всех 6 секций>"`.
   - Сохранять весь оригинальный body, который user успел написать, — только дополнять и реструктурировать, не выкидывать.

7. **Отчёт пользователю**: ссылка на issue, краткое summary плана, предложение «готов запускать `/implement #N`?».

## Что НЕ делать

- НЕ создавать `specs/00N-*` папок, ADR-файлов, отдельных plan.md.
- НЕ писать код.
- НЕ создавать ветку — это работа `/implement`.
- НЕ ставить milestone/assignee/projects — только структурировать body и при необходимости label.
- НЕ задавать более 3 вопросов user'у. Если осталось много дыр после 3 вопросов — лучше abort с честным «issue недостаточно описан, опиши X/Y/Z и запусти `/plan` снова».

## Ссылки

- Workflow rationale: issue #114.
- Quality gates и принципы: [docs/architecture/principles.md](../../docs/architecture/principles.md).
