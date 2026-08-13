## Context / Why

Scheduled run [31663905928](https://github.com/ekolvah/kinozal_scraper/actions/runs/31663905928) от 2026-08-13 отправил в Telegram книжные раздачи, хотя Kinozal-поток предназначен для видео и игр:

- [Сергей Тармашев — Каждому своё (4 книги)](https://kinozal.guru/details.php?id=2104907);
- `Сергей Лукьяненко — Небесное воинство: Седьмой`.

Root cause локализован до Telegram. Production `KINOZAL_URLS` содержит три листинга `top.php?...t=0...`, отдельные `t=7` (игры) и `t=32` (зарубежные сериалы). Живой DOM Kinozal показывает, что `t=0` называется **«Избранные раздачи»**, а не «Избранные фильмы»; `t=1` — отдельная категория **«Избранные фильмы»**, `t=5` — библиотека, `t=6` — аудиокниги. Карточка #2104907 находилась на второй странице `t=0` и имела title `Сергей Тармашев - Каждому своё (4 книги) / Фантастика / 2025 / Аудиоспектакль / MP3`.

Все пять URL сейчас объединяются в source `kinozal_movies`. `_fetch_and_extract` без проверки `t` извлекает каждый `details.php`, `_normalize_items` не знает категорию, а pre-notification фильтр проверяет только `KINOZAL_EXCLUDED_GENRES=Hidden objects`. Поэтому книга попала в `_notify_and_persist`, где production log подтверждает неверную film-классификацию: для неё выполнены YouTube queries `Сергей Тармашев - Каждому своё 2025 trailer` и `Фантастика 2025 trailer`, после чего отправлено уведомление.

Исправление должно закрыть источник ошибки: production-листинги фильмов используют `t=1`, а код до сетевого fetch разрешает только явно поддержанные film/cartoon/series/game category IDs. Широкий `t=0`, библиотека, аудиокниги, музыка, программы, отсутствующий или неоднозначный `t` — видимая configuration error, а не элемент, который молча считается фильмом.

## Acceptance criteria

1. URL категории `t=0` отвергается до `fetch_listing`, trailer lookup и Telegram; book fixture с title из #2104907 не уведомляется.
2. Разрешены текущие Kinozal video/game families: фильмы (`t=1`, `101–116` из живого category selector), мультфильмы (`t=2`, `21–23`), сериалы (`t=3`, `31–32`) и игры (`t=7`). Production `t=1`, `t=7`, `t=32` продолжают обрабатываться.
3. `t=4/5/6/8`, `t=0`, отсутствующий, пустой, повторённый или non-integer `t` не fetch-ятся; каждый rejected URL добавляет читаемую ошибку в соответствующий `PipelineResult`, поэтому run завершается non-zero, но остальные разрешённые URL того же source продолжают доставляться.
4. Каждый новый разрешённый item несёт `kinozal_listing_url` и числовой `kinozal_listing_category` в `raw`; INFO breadcrumb для нового item называет title, listing URL и category, чтобы следующий RCA не терял provenance после объединения листингов.
5. Live repository variable `KINOZAL_URLS` заменяет три `t=0` URL на эквивалентные `t=1`, сохраняя page/year/прочие query-параметры; `t=7` и `t=32` остаются без изменений. Значение после записи перечитывается и проверяется без вывода credentials.
6. Не используется эвристика по автору, словам `книга`/`MP3` или позиции сегмента title и не добавляется per-item details fetch: source-of-truth — category selector `t` самого листинга.
7. Документация называет `t=0` смешанным top и описывает allowlist/fail-closed поведение; `python scripts/ci_check.py` проходит.

## Test plan

RED-first, все node IDs должны падать на текущем коде и затем стать GREEN:

- `tests/test_kinozal_pipeline.py::TestKinozalCategoryGuard::test_t0_book_listing_rejected_before_fetch_youtube_and_notification` — fake HTTP возвращает listing с #2104907; до fix книга fetch-ится/уведомляется, после fix `t=0` не достигает HTTP/YouTube/Telegram и result содержит category error.
- `tests/test_kinozal_pipeline.py::TestKinozalCategoryGuard::test_rejected_category_does_not_block_allowed_film_series_and_game_urls` — один run содержит `t=0`, `t=1`, `t=32`, `t=7`; book URL отвергнут, а по одному уникальному item из каждой разрешённой категории доставлены.
- `tests/test_kinozal_pipeline.py::TestKinozalCategoryGuard::test_missing_malformed_and_unsupported_categories_are_visible_errors` — missing/empty/repeated/non-integer и `t=4/5/6/8` не fetch-ятся и перечислены в `PipelineResult.errors`.
- `tests/test_kinozal_pipeline.py::TestKinozalListingProvenance::test_new_item_log_and_raw_name_listing_url_and_category` — разрешённый item получает raw provenance, а INFO log содержит title, полный URL и `t=1`.

GREEN/regression:

- `python -m pytest tests/test_kinozal_pipeline.py` — существующие extraction, genre filter, film/game title grammar, delivery и mirror tests остаются зелёными; test helpers, где категория не является предметом теста, используют явный разрешённый `t=1`.
- `python -m pytest` — вся suite.

## Implementation outline

1. В `kinozal_pipeline.py` добавить закрытые константы разрешённых category IDs из проверенного live selector и pure parser `_kinozal_listing_category(url) -> int | None`: через `urlsplit`/`parse_qs(..., keep_blank_values=True)`, принимая ровно одно целое `t` из allowlist.
2. В `_fetch_and_extract` перед `fetcher.fetch_listing(url)` проверять category. Rejected URL логировать как ERROR и добавлять в per-source `result.errors`, затем `continue`, сохраняя per-URL isolation для разрешённых соседей. Не падать исключением и не превращать ошибку в green skip.
3. Передать исходный listing URL/category в `_extract_kinozal_items` (optional parameters сохраняют прямые unit call sites) и записать их в `item.raw` вместе с `kinozal_raw_title`.
4. В `_dedup_and_log_coverage` после определения `new_items` добавить один bounded INFO breadcrumb на новый item с title/listing/category. Не логировать все 250 existing items.
5. Обновить test helper defaults с category-less `top.php` на `top.php?t=1`; добавить четыре RED nodes без моков internal helpers — real `run_kinozal_pipeline`, in-memory Storage/Notifier и fake HTTP/YouTube boundaries.
6. Обновить `docs/architecture/pipeline.md` и `docs/architecture/operations.md` текущим implemented contract.
7. Deployment companion: безопасно заменить в GitHub variable `KINOZAL_URLS` три `t=0` на `t=1`, сохранив остальные query fields и URL; перечитать variable и проверить набор `t={1,7,32}`. Эта config-правка немедленно убирает mixed listing, а code guard в PR предотвращает возврат дефекта.

## Docs to update

- `docs/architecture/pipeline.md` — `t=0` является mixed «Избранные раздачи»; разрешённые families, fail-closed guard и item provenance.
- `docs/architecture/operations.md` — operational contract `KINOZAL_URLS`: только allowlisted category IDs; production film pages используют `t=1`.
- ADR не создаётся; решение локально для одного pipeline/config contract и обратимо изменением category URL.

## Out of scope

- Per-item ML/LLM/content classifier и title heuristics — YAGNI: category `t` отвечает детерминированно до сети.
- Дополнительный details fetch ради типа контента — YAGNI и лишний HTTP на каждый новый item; details остаётся только для включённого genre denylist.
- Удаление уже доставленных книжных сообщений или строк Sheets — исторические данные не переуведомляются; текущий incident уже записан dedup-механизмом.
- Изменение trailer-selection grammar или исправление спорного trailer pick для иных разрешённых items — отдельный класс поведения, не нужен для #506.
- Поддержка музыки, библиотеки, аудиокниг и программ — сознательно запрещённые продуктом категории, не follow-up.

## Architect review

reviewer: Codex $plan-issue #506 self-review

- **BLOCKING (high, закрыт в Context/Impl.1/7):** фильтр по словам title или по genre чинит симптом и оставляет `t=0` смешанным источником. Источник истины проверен на live DOM: category query; production URL должен стать `t=1`, а code guard — fail-closed.
- **BLOCKING (high, закрыт в AC3/Impl.2/Test 2–3):** простой `continue` без `PipelineResult.errors` превратит конфигурационный дрейф в зелёное «нет новостей». Rejection обязан быть видимой ошибкой, не блокируя разрешённые sibling URLs.
- **BLOCKING (high, закрыт в AC1/Test 1):** тест только pure parser не докажет, что книга не дошла до HTTP/YouTube/Telegram. Нужен orchestration RED через production entry point и внешние doubles.
- **SHOULD-FIX (high, закрыт в AC4/Impl.3–4):** текущий merge теряет listing provenance; сохранять его в `raw` и логировать только для new items, иначе RCA снова не назовёт источник либо зашумит лог 250 строками.
- **SHOULD-FIX (medium, закрыт в AC5/Impl.7):** merge code guard при старом `t=0` сделает Kinozal step красным. Config transition выполняется и проверяется в рамках delivery, сохраняя остальные query-параметры.
- **NICE-TO-HAVE (low, отклонён в Out of scope):** универсальный content classifier мог бы классифицировать individual items из mixed pages, но добавляет HTTP/эвристики и не нужен при точном category URL.
- **OK:** минимальный stdlib change, без dependency/ADR/LLM; RED покрывает прежнее user-visible поведение, deterministic guard экономит trailer calls и соблюдает §I, §IV–§VII.

Self-review не независим и не заменяет PR review текущего head.

## ADR

none: локальная обратимая политика одного Kinozal pipeline и его environment variable; rationale живёт в `pipeline.md`, а стоимость изменения не требует межмодульного architecture record.

## Agent handoff

planner: Codex GPT-5
validation: `python scripts/validate_issue_sections.py 506` — passed
next role: implementer
handoff: ready

