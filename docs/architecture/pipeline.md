# Pipeline architecture

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

## Trailer retrieval and selection (#140, #141, #144)

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
  Для harness/#140-eval и потенциальной каст-эскалации; прод пока его не зовёт (ниже).

**Игровые раздачи (#385).** `KINOZAL_URLS` содержит и топ игр (`t=7`) наравне с фильмами (`t=0`)
и сериалами (`t=32`) — все текут в один source `kinozal_movies`. Грамматика заголовка у них
**другая**: `Название / x64 / RU / Жанр / Год / Формат / PC (Windows)` против фильмового
`RU / Original / Year / Format`. Поэтому `original_title` (2-й ` / `-сегмент) отдавал у игр
архитектуру, и в YouTube уходило `x64 2024 trailer` — 27 таких запросов в прогоне
[30143534431](https://github.com/ekolvah/kinozal_scraper/actions/runs/30143534431).

Категорию знает только URL листинга (у item'а `url` — это `details.php?id=N`), поэтому
`_is_game_url(url)` (матч через `parse_qs`, не подстрокой: `t=71` ≠ `t=7`) вычисляется в
`_fetch_and_extract` и едет в `item.raw["kinozal_is_game"]`. Для игрового item профиль строится
с `original_title == ""` — у игр русского названия нет, отдельного «оригинала» не существует, так
что пустая строка это верное значение, а не заглушка. Трейлеры для игр **ищутся** (по названию из
`parts[0]`); побочно union схлопывается в один запрос, то есть игровой item вдвое дешевле по
квоте (#384). Денилист формат-токенов отвергнут как лечение симптома (§V). Одна INFO-строка на
прогон сообщает, сколько URL распознано игровыми — иначе забытый новый игровой URL молча
разбирался бы фильмовой грамматикой. `dedupe_key` не трогаем: он обрезается по year-сегменту,
для игр стабилен, а «заодно» его чистить — повторить #363.

**Прод-композиция (#144):** `enrich_with_trailer(item, youtube)` строит облегчённый
title+year `FilmProfile` (ru_title=clean, original_title=2-й сегмент или "", year) и
делегирует `select_trailer(profile, youtube)` →
`youtube.search_candidates` (union #140) → `HeuristicStrategy().pick` (#141, = eval
`default_strategy()`) → `video_id` в youtube-URL. RU-трейлер в приоритете, EN — fallback
(закрывает RU-регрессию #138→#315; прежний одиночный `get_trailer_url` удалён). Пустой
pick → §IV miss-маркер + INFO; retrieval-исключение (в т.ч. `TrailerRetrievalError` —
все ветки union упали, #383) → §IV error-маркер + WARNING; успех →
INFO-breadcrumb `video_id`/`reason`/`confidence`; miss-ветка пишет размер пула (#359 —
«YouTube ничего не вернул» и «вернул N, ни один не прошёл relevance» это разные баги, а
без `video_id` отчёт «пришла не та ссылка» вообще неразбираем).

**Почему композиция разрезана надвое (#379).** `select_trailer` — всё, что стоит между
профилем и пользователем; `enrich_with_trailer` — только деривация профиля из
kinozal-заголовка. Разрез не косметический: #359 сломал именно верхнюю половину, не
тронув `HeuristicStrategy.pick`, и pick-скоркарта eval-харнесса была бы одинаковой до и
после регресса. Теперь замер заходит в `select_trailer`, а его исход пришпилен
`tests/fixtures/trailer_baseline.json` (см. [testing.md](testing.md#eval-harness--trailer-selection-139)).
Шов проходит по `FilmProfile` — родной форме golden-set: будь входом `NormalizedItem`,
фикстурам пришлось бы дублировать грамматику kinozal-заголовка (§II). Обратная сторона:
**нижняя** половина (clean-title / `original_title` / year-regex / игровая ветка — там
сидели #385 и #393) замером не покрыта и держится на юнит-тестах `TestEnrichWithTrailer`.

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
(`ru_title == original_title` стоит 1 запрос, а не 2 — все игры такие). Фактическую границу знает
только API, поэтому её называет он, а цена обнаружения — запросы одного фильма.

Почему не throttle/retry: лимит считается в запросах за сутки, паузами не создаётся. Прогон
[30143534431](https://github.com/ekolvah/kinozal_scraper/actions/runs/30143534431) просил 340
запросов при потолке 100 — 163 ушли в гарантированный 429. Пейсинг раздал бы те же 100 ровнее,
retry отбирал бы квоту у следующего фильма. Штатный потолок конфига (4 URL × `limit: 50` = 200
items) тоже выше квоты — сегодня спасает лишь дедупликация. Вернуть трейлеры **всем** фильмам
может только смена источника (TMDB — токен есть, `tmdb_trailer.py` с #329, суточного лимита нет).

**Отбор по `confidence` сознательно НЕ делается — и это проверено метрикой.** #359 пробовал
давить низкоуверенные picks (`< 0.5`, т.е. ambiguous-ничьи) в miss-маркер; откачено по замеру
на golden-set (28 кейсов): 26 hit → 16, 2 miss → 12, wrong 0 → 0. Все 10 подавленных picks
были **попаданиями**: `confidence=0.3` означает «несколько одинаково хороших трейлеров одного
фильма» (дубляж №1 vs №2 — ровно то, что моделируют accept-set'ы golden-set'а), а не
«возможно, не тот фильм». Ничьи в проде частые (5/6 picks в run `30066249488`), но
**безвредные**. **Перепроверено на наборе с отрицательным полюсом (#380):** тот замер шёл по
набору, где `wrong` не встречался, — то есть выигрыш политики он показать физически не мог. На
обновлённом наборе (31 кейс, `wrong=1`) откаченная политика даёт 26 → 14 и **wrong как был 1,
так и остался**: единственный реальный чужой pick («Крайние меры» → трейлер Minecraft-канала)
идёт с `confidence=0.9` — уникальный топ-ранг, — поэтому порог по уверенности его не задевает
вовсе. Вывод #359 не просто подтверждён, а усилен: отбор по `confidence` ортогонален тому классу
ошибок, который в проде реально наблюдается. Любое изменение логики отбора обязано проходить через
`scripts/eval_trailers.py` до мержа. **Gemini НЕ в hot path** — LLM(#142)/embeddings(#143)/
TMDB(#329) остаются eval-стратегиями (осознанно вне прода: равный Hit при нулевой рантайм-
стоимости vs Gemini-квота 04:00; coverage-следствие + open-world caveat —
[`testing.md` gap-ledger N](testing.md#consciously-accepted-coverage-gaps)). Каст в прод-профиль не тянем
(RU-приоритет на языке заголовка; per-item details-фетч ради каст-тай-брейка отложен — и #377
закрыт как wontfix: замер #359 показал, что ничьи, которые каст должен был разрывать, безвредны).

## extract_from_* contracts

- Take in-memory payload (list of dicts for JSON, HTML string for HTML)
- Return `PipelineResult(items, errors, warnings)`
- Zero items extracted → `errors` entry (quality failure)
- Missing `dedupe_key` or `title` on a record → `errors` entry, item skipped
- Never raise for data quality issues — caller decides what to do

## HTML source config

HTML sources require `row_selector` in source config (not in `fields`).
Field selectors use `css@attr` syntax to extract attributes.

## Macro expansion

Handled by `pipeline_config.py` before the pipeline runs.
Supported macros: `{{TODAY}}`, `{{DATE_MINUS_7_DAYS}}`, `{{GH_TOP_LIMIT}}`, `{{GH_TRENDING_LIMIT}}`, `{{STEAM_TOP_LIMIT}}`.
