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

**Прод-композиция (#144):** `enrich_with_trailer(item, youtube)` строит облегчённый
title+year `FilmProfile` (ru_title=clean, original_title=2-й сегмент или "", year) →
`youtube.search_candidates` (union #140) → `HeuristicStrategy().pick` (#141, = eval
`default_strategy()`) → `video_id` в youtube-URL. RU-трейлер в приоритете, EN — fallback
(закрывает RU-регрессию #138→#315; прежний одиночный `get_trailer_url` удалён). Пустой
pick → §IV miss-маркер + INFO; retrieval-исключение → §IV error-маркер + WARNING; успех →
INFO-breadcrumb `reason`/`confidence`. **Gemini НЕ в hot path** — LLM(#142)/embeddings(#143)/
TMDB(#329) остаются eval-стратегиями (осознанно вне прода: равный Hit при нулевой рантайм-
стоимости vs Gemini-квота 04:00; coverage-следствие + open-world caveat —
[`testing.md` gap-ledger N](testing.md#consciously-accepted-coverage-gaps)). Каст в прод-профиль не тянем
(RU-приоритет на языке заголовка; per-item details-фетч ради каст-тай-брейка отложен).

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
