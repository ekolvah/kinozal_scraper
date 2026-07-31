# Pipeline architecture

**Question this document answers:** how a single pipeline run is built and behaves — the
extraction layers, the `extract_from_*` contracts and the `NormalizedItem` they produce, how a
new source is added by config rather than code, the error policy, notification templates, macro
expansion, trailer retrieval/selection, and **how fetching behaves** (HTML source config,
kinozal's mirror fallback). Depth on one run, where [`runtime.md`](runtime.md) gives breadth
over all of them.

**Not here.** Which pipelines exist and which Protocols they share →
[`runtime.md`](runtime.md). Sheets row schema and column invariants →
[`storage.md`](storage.md). Gemini rotation/quota/prompts → [`gemini.md`](gemini.md). The
credentials that switch the mirror fallback on, and everything else about operating the run →
[`operations.md`](operations.md).

## Layers

```
sources.json          declarative config (urls, selectors, limits, macros)
pipeline_config.py    loads config, expands macros, validates schema
generic_pipeline.py   extract + normalize (no network, no I/O)
sheets_storage.py     read existing keys, append confirmed rows
telegram_notifier.py  send items, return confirmed list
```

## Key principle: new source = config, not code (with known limitation)

Adding GitHub, Steam, or any future source requires only a new entry in
`sources.json` for extraction and normalization. No new Python class needed.

**Known limitation:** HTTP fetching (pagination, auth headers, rate limits) is
not declarative. Each new source requires a small fetch function in the caller.

## Data flow

For the full list of pipelines and how they connect, see [runtime.md](runtime.md).

**Delivery state is intentional: Sheets rows represent confirmed delivery.**
Pipelines that can partition notifier results write only successfully sent
items to Sheets. If delivery fails for any item, the step must surface a
non-ok result and exit non-zero instead of silently looking like "no news."

```
load_sources_config()
  → for each enabled source:
      fetch payload (HTTP, done by caller)
      extract_from_json / extract_from_html  → PipelineResult
      storage.get_existing_keys(sheet_tab)   → set[str]  ← raises SchemaError on mismatch
      new_items = [i for i if i.dedupe_key not in existing_keys]
      sent, failed = notifier.send(new_items)
      storage.append_rows(sheet_tab, [i.to_row() for i in sent])
      failed notifications -> PipelineResult.errors
```

## Error policy

`PipelineResult` carries `errors` and `warnings`; production callers exit
non-zero when any result is not ok. Notification delivery failures are errors,
not warnings, because users must receive either data or a failure signal — never silence.
Future: `on_error: skip_item | fail_source` field in `sources.json` — deferred
to issues #6/#7 when sources become real.

## NormalizedItem

Defined in `generic_pipeline.py`. All pipeline stages pass this type.

```python
@dataclass
class NormalizedItem:
    dedupe_key: str      # unique key for deduplication (required)
    title: str           # display title (required)
    source_id: str       # matches sources.json id
    url: str
    description: str
    metric: str
    image_url: str
    trailer_url: str     # enriched by caller; not stored in Sheets
    raw: dict            # original record for debugging
```

Row serialization: `item.to_row()` → `[dedupe_key, title, url, metric, source_id, notified_at]`
Headers constant: `ROW_HEADERS` in `generic_pipeline.py`

## Notification templates

`build_notification(item, template)` in `generic_pipeline.py` renders the
Telegram HTML message. Available template variables:

| Variable | Content |
|---|---|
| `{title}` | plain escaped title |
| `{title_link}` | `<a href="{url}">{title}</a>` — clickable title linking to the source page |
| `{url}` | raw URL of the item page |
| `{trailer_url}` | raw YouTube trailer URL, or a §IV miss/failure marker (see below) |
| `{trailer_link}` | `<a href="{trailer_url}">Trailer</a>` — clickable "Trailer" word for an http(s) URL; a non-http value (a §IV marker `🎬 трейлер не найден` on a clean miss / `⚠️ трейлер: ошибка поиска` on a lookup failure, #138) renders as visible escaped text; empty only when `trailer_url` is unset (non-kinozal sources) |
| `{description}` | plain escaped description |
| `{metric}` | numeric metric (stars, players, etc.) |
| `{image_url}` | raw image URL |
| any key from `item.raw` | e.g. `{summary_ru}` for GitHub sources, `{description_ru}` for Steam (see [gemini.md](gemini.md)) |

**Kinozal template** (`sources.json`):
```
{title_link}\n{trailer_link}
```
Renders as: clickable film title → kinozal page, then "Trailer" → YouTube.

## Trailer retrieval and selection

Эпик разводит **retrieval** (`film → list[Candidate]`) и **selection**
(`(profile, candidates) → pick`, `trailer_strategy.py`, #139/#141/#144). Слой data-prep:

- `youtube.search_candidates(profile)` (`youtube.py`) — пул кандидатов = **union**
  запроса по RU + оригинальному названию, дедуп по `video_id`, **без** year/title-фильтра
  (год отсеивает selection, не retrieval). RU-трейлер обязан быть в пуле, когда он есть
  (#315 — retrieval breadth). Сбой одной ветки union не роняет пул (§IV best-effort).
  Общий retrieval переиспользует harness `scripts/eval_trailers.py --record` (§II).
- `build_film_profile(item, fetcher)` (`kinozal_pipeline.py`) — richer-builder
  `FilmProfile` (каст/режиссёр/жанр/описание) с `details.php` через общий
  `_parse_labeled_field` (тот же sibling-walk, что `_parse_genre`). Сбой фетча/парса →
  деградация до title+year + WARNING; фетч ОК с нулём полей → WARNING-tripwire (§IV).
  Для harness-eval (#140) и потенциальной каст-эскалации; прод его не зовёт (ниже).

**Игровые раздачи (#385, #412).** `KINOZAL_URLS` содержит и топ игр (`t=7`) наравне с фильмами
(`t=0`) и сериалами (`t=32`) — все текут в один source `kinozal_movies`. Грамматика заголовка у
них **другая**: `Название / x64 / RU / Жанр / Год / Формат / PC (Windows)` против фильмового
`RU / Original / Year / Format`. Поэтому `original_title` (2-й ` / `-сегмент) отдавал у игр
архитектуру, и в YouTube уходило `x64 2024 trailer` — 27 таких запросов в прогоне
[30143534431](https://github.com/ekolvah/kinozal_scraper/actions/runs/30143534431).

**Дискриминатор — форма сегмента, а не категория листинга.** Категория (`t=7`) на эту роль не
годится: у локализованной игры `Marvel Человек-Паук 2 / Marvel's Spider-Man 2 (Digital Deluxe
Edition) / x64 / …` оригинал стоит ровно там же, где у фильма, и гашение по категории оставило бы
в запросе только русское название, которого на YouTube нет — `no trailer found` при пяти
официальных трейлерах в выдаче (#412). Поэтому `original_title` (`text_utils.py`) гасит **служебный**
2-й сегмент: год, архитектуру (`x64|x86|x32`) и языковой код (`RU|EN`). Набор закрыт замером всех
3764 raw-заголовков из Sheets — `x64` 888, `RU` 139, `EN` 1, и ничего иного служебного на этой
позиции нет; 96 игровых раздач несут там настоящее оригинальное название (из них 78 — со скобочным
суффиксом издания, который снимает уже матчинг в `HeuristicStrategy._relevant`; не путать со 160
не-игровыми, где скобка несёт альтернативное название). Эвристику «короткий
сегмент → служебный» тот же замер запрещает: `Silo`, `From`, `Halo`, `Apex` — реальные названия,
поэтому дискриминатор сверяет точный литерал. Гард действует для всех источников сразу, включая
`build_film_profile`, куда категория листинга не проброшена вовсе. Цена: локализованная игра стоит
**2** `search.list` вместо одного (#384).
Гашение служебного сегмента молчаливо, поэтому §IV-видимость грамматики держит одна INFO-строка
на item в `search_candidates` с фактическими запросами: без неё новый служебный литерал
(`RUS`, `Multi`, `Update 5`) уехал бы в YouTube как «название», а исход был бы неотличим от
честного «трейлера не существует». `dedupe_key` грамматику игр не разбирает намеренно: он
обрезается по year-сегменту и для игр стабилен, а правка «заодно» — тот же класс дефекта (#363).

**Прод-композиция (#144):** `enrich_with_trailer(item, youtube)` строит облегчённый
title+year `FilmProfile` (ru_title=clean, original_title=2-й сегмент или "", year) и
делегирует `select_trailer(profile, youtube)` →
`youtube.search_candidates` (union #140) → `HeuristicStrategy().pick` (#141, = eval
`default_strategy()`) → `video_id` в youtube-URL. RU-трейлер в приоритете, EN — fallback
(#138, #315). Пустой
pick → §IV miss-маркер + INFO; retrieval-исключение (в т.ч. `TrailerRetrievalError` —
все ветки union упали, #383) → §IV error-маркер + WARNING; успех →
INFO-breadcrumb `video_id`/`reason`/`confidence`; miss-ветка пишет размер пула:
«YouTube ничего не вернул» и «вернул N, ни один не прошёл relevance» — разные баги, а
без `video_id` отчёт «пришла не та ссылка» вообще неразбираем (#359).

**Почему композиция разрезана надвое (#379).** `select_trailer` — всё, что стоит между
профилем и пользователем; `enrich_with_trailer` — только деривация профиля из
kinozal-заголовка. Разрез не косметический: скоркарта на `pick` слепа к слою **над**
стратегией — политика, меняющая доставку на 10 фильмов (замер 26→16, #359), не двигает её
ни на балл. Гейт
`tests/test_eval_baseline.py::TestBaselineGate::test_reverted_359_policy_fails_the_gate`
краснеет на такой политике по **delivery**-колонке. Поэтому замер заходит в `select_trailer`,
а его исход пришпилен
`tests/fixtures/trailer_baseline.json` (см. [testing.md](testing.md#eval-harness--trailer-selection)).
Шов проходит по `FilmProfile` — родной форме golden-set: будь входом `NormalizedItem`,
фикстурам пришлось бы дублировать грамматику kinozal-заголовка (§II). Обратная сторона:
**нижняя** половина (clean-title / `original_title` / year-regex / грамматика служебного
сегмента) замером не покрыта и держится на юнит-тестах
`TestEnrichWithTrailer` / `TestGameTitleGrammar` (#385, #393, #412).

**Остановка по первому квотному отказу (#384).** Суточная квота YouTube — **100 `search.list`**
(замер 2026-07-26 через Service Usage API; квота дефолтная, billing выключен, поднять нельзя).
Пока API отвечает, обогащаются **все** фильмы; первый отказ из usageLimits-семейства
(`_is_quota_error` в `youtube.py`: статус 429/403 + reason из `error_details`) поднимает
`YoutubeQuotaExhausted`, и оставшиеся фильмы **не ходят в сеть вообще** — несут третий §IV-маркер
`⚠️ трейлер: дневная квота YouTube` (не промах и не поломка: другая причина, другое действие
оператора). Оператор видит **одну** WARNING-строку с числом необслуженных фильмов, а не строку
на каждый. Неквотный отказ (500/таймаут) по-прежнему роняет только свой фильм (#383) — иначе
один моргнувший ответ глушил бы трейлеры на весь прогон.

Почему не фиксированный бюджет: любое предвычисленное число угадано, ломается на **втором**
прогоне в сутки (квота суточная, бюджет — на прогон) и занижает охват на одноветочных items
(`ru_title == original_title` или пустой оригинал стоит 1 запрос, а не 2 — так у игр без
русского названия; локализованные игры двухветочные). Фактическую границу знает
только API, поэтому её называет он, а цена обнаружения — запросы одного фильма.

Почему не throttle/retry: лимит считается в запросах за сутки, паузами не создаётся. Прогон
[30143534431](https://github.com/ekolvah/kinozal_scraper/actions/runs/30143534431) просил 340
запросов при потолке 100 — 163 ушли в гарантированный 429. Пейсинг раздал бы те же 100 ровнее,
retry отбирал бы квоту у следующего фильма. Штатный потолок конфига (4 URL × `limit: 50` = 200
items) тоже выше квоты — сегодня спасает лишь дедупликация. Вернуть трейлеры **всем** фильмам
может только смена источника (TMDB — токен есть, `tmdb_trailer.py` (#329), суточного лимита нет).

**Отбор по `confidence` сознательно НЕ делается — и это проверено метрикой.** Низкая уверенность
здесь не значит «возможно, не тот фильм»: `confidence=0.3` — это «несколько одинаково хороших
трейлеров одного фильма» (дубляж №1 vs №2, ровно то, что моделируют accept-set'ы golden-set'а),
и в проде такие ничьи частые, но безвредные. Порог по уверенности режет попадания и **не**
задевает единственный наблюдаемый класс чужих picks: тот приходит уникальным топ-рангом с высокой
уверенностью. Отбор по `confidence` ортогонален реальному классу ошибок. Замеры, на которых это
держится (включая набор с отрицательным полюсом `trap`), — [gap-ledger N](coverage-gaps.md) и
[testing.md § Eval harness](testing.md#eval-harness--trailer-selection); любое изменение логики
отбора обязано проходить через `scripts/eval_trailers.py` до мержа.
**Gemini НЕ в hot path** — LLM(#142)/embeddings(#143)/
TMDB(#329) остаются eval-стратегиями (осознанно вне прода: равный Hit при нулевой рантайм-
стоимости vs Gemini-квота 04:00; coverage-следствие + open-world caveat —
[gap-ledger N](coverage-gaps.md)). Каст в прод-профиль не тянем
(RU-приоритет на языке заголовка; per-item details-фетч ради каст-тай-брейка не делается —
ничьи, которые каст разрывал бы, безвредны, #377 — wontfix).

## extract_from_* contracts

- Take in-memory payload (list of dicts for JSON, HTML string for HTML)
- Return `PipelineResult(items, errors, warnings)`
- Zero items extracted → `errors` entry (quality failure)
- Missing `dedupe_key` or `title` on a record → `errors` entry, item skipped
- Never raise for data quality issues — caller decides what to do

## HTML source config

HTML sources require `row_selector` in source config (not in `fields`).
Field selectors use `css@attr` syntax to extract attributes.

## Kinozal mirror fallback

Включается парой секретов `KINOZAL_USERNAME` + `KINOZAL_PASSWORD` — их описание в
[`operations.md` § kinozal_pipeline](operations.md#kinozal_pipeline).

**Fallback на зеркало при недоступности `kinozal.tv` (#227):** primary —
анонимный `kinozal.tv` (`KINOZAL_URLS` остаётся `.tv`, **переключать не нужно**). Если fetch какого-то
URL падает (напр. 522), пайплайн автоматически повторяет тот же топ на зеркале **`kinozal.guru`**
через авторизованную сессию. Логин **ленивый** — выполняется максимум раз за прогон и только при
первом срабатывании fallback, поэтому здоровый `.tv`-прогон не платит за логин и не требует кредов.

⚠️ **Анонимный свап домена на `.guru` не работает** (проверено 2026-06-30): `kinozal.guru` гейтит
весь контент за логином — `/top.php`, `/browse.php`, даже `/` → `302 .../login.php?m=5`. Поэтому
fallback идёт через `kinozal_auth.py` (`POST /takelogin.php`, обычного не-VIP аккаунта достаточно —
подтверждено живым прогоном).

**Включение fallback:** задай оба секрета `KINOZAL_USERNAME` + `KINOZAL_PASSWORD`. Без них (или при
partial) fallback отключён, и сбой `.tv` доходит видимой ошибкой `fetch failed ... (mirror
fallback disabled)` + exit 1 (§IV). Провал логина / both-failed тоже видимы:
`mirror login failed` / `primary failed (...); mirror ... also failed (...)`.
`sources.json` `base_url` остаётся `https://kinozal.tv` (дефолтный origin, когда primary жив) —
зеркало туда не прописывать.

**Ссылки следуют за фактическим origin (#247):** `Kinozal.fetch_listing` возвращает
`(html, effective_base_url)` — `kinozal.tv` при успехе primary, `kinozal.guru` при mirror-fallback.
Пайплайн резолвит относительные `url`/`image_url` листинга против этого базового хоста (per-fetch
override статичного `base_url`), поэтому mirror-прогон даёт **`.guru`-ссылки** — живые для
залогиненного получателя, а не мёртвые `.tv`. Canonical-origin-подход («`base_url` всегда `.tv`»)
здесь отвергнут сознательно: получатель залогинен на `.guru`, login-wall для него неактуален
(#227, #241, #247). Смешанный прогон (часть топов с `.tv`, часть с зеркала) даёт
корректный хост у каждого item; dedupe стабилен (ключ — чистый title, host в него не входит →
миграция старых `.tv`-строк в Sheet не нужна).

**Details-fetch genre-фильтра на mirror-прогонах (#317):** раз ссылки следуют за фактическим
origin, на mirror-днях `item.url` = `kinozal.guru/details.php?...`. `Kinozal.fetch_details` для mirror-host
URL идёт через **авторизованную** сессию (как listing), а не анонимным primary: `.guru` гейтит и
`details.php` за логином (см. ⚠️ выше), поэтому анонимный GET вернул бы `200` login-страницу без
блока `Жанр:` — ложный успех, который except-triggered failover `fetch_listing` не ловит, и
genre-фильтр тихо слепнет (`_parse_genre`=="" для всех → fail-open → всё уведомляется). Постеры
`/i/poster/` зеркало отдаёт анонимно (verified), поэтому `fetch_poster` этот путь не затрагивает.

Единственный потребитель — production-cron (`run-script.yml` / `kinozal_pipeline.py`). E2E
`tests/test_e2e_kinozal_titles.py` безусловно skip'нут, пока `kinozal.tv` отдаёт 522 (#136).

## Macro expansion

Handled by `pipeline_config.py` before the pipeline runs.
Supported macros: `{{TODAY}}`, `{{DATE_MINUS_7_DAYS}}`, `{{GH_TOP_LIMIT}}`, `{{GH_TRENDING_LIMIT}}`, `{{STEAM_TOP_LIMIT}}`.
