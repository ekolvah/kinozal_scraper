# Operations — как прод-прогон исполняется и чем конфигурируется

**На какой вопрос отвечает этот файл:** как ежедневный прод-прогон эксплуатируется — расписание и
порядок шагов, чем он конфигурируется (env-переменные и секреты), как изолируются падения
отдельных источников и куда уходит алерт, какие runbook'и нужны оператору (ротация секрета).

**Чего здесь нет** — один вопрос на файл ([`project-map.md`](project-map.md) §«Конвенция-заголовков»),
и этот файл собирает разнородные блоки, поэтому граница проговорена явно: гейты качества на пути
изменения (local `ci_check`, `ci.yml`, cloud-ревью, пиннинг моделей) → [`ci.md`](ci.md); какие есть
пайплайны, Protocol-границы и data-flow → [`runtime.md`](runtime.md); слои извлечения, контракты
`extract_from_*` и поведение fetch (включая mirror-fallback kinozal) → [`pipeline.md`](pipeline.md).

## Production workflow (`run-script.yml`)

Schedule: daily cron (UTC) defined in `run-script.yml` + manual `workflow_dispatch`.

Steps, in order:
1. **pytest** — smoke gate (`id: tests`), fails fast; a red gate blocks every prod pipeline below
2. **github_popular_pipeline.py** — GitHub `new_popular`
3. **github_trending_pipeline.py** — GitHub trending (HTML + enrichment)
4. **steam_pipeline.py** — Steam Most Played (Steam Charts API + appdetails)
5. **kinozal_pipeline.py** — Kinozal movies
6. **telegram_summarizer.py** — `if: always()` (runs even if earlier steps fail)
7. **soldout_pipeline.py** — Soldout events, **last on purpose** (see below)

### Pipeline-step isolation

**Root cause it fixes (#245):** GitHub Actions steps carry an *implicit* `if: success()`. So a
single step's `exit 1` used to cascade into **skipping every later step** — a transient
third-party 403 in `soldout_pipeline` skipped `kinozal_pipeline` and suppressed movie
delivery for that run ([run 28493805028](https://github.com/ekolvah/kinozal_scraper/actions/runs/28493805028)).
Per-source isolation existed *inside* each `run_*_pipeline`, but not *between* the workflow
steps.

**Fix:** every pipeline step carries
`if: ${{ !cancelled() && steps.tests.outcome == 'success' }}`:

- `!cancelled()` — the step runs even if an **earlier pipeline** step failed (defeats the
  implicit `if: success()` cascade), so a flaky source can't suppress an unrelated one.
- `steps.tests.outcome == 'success'` — but a **red smoke gate still skips every pipeline**
  (the hard prerequisite is preserved; that's why the gate carries `id: tests`).
- **No `continue-on-error`** — a failed source still exits 1 → job goes red → the existing
  `Send fallback failure alert` (`if: failure()`) fires. Failure stays visible (§IV); it is
  *not* masked into a green job. This is why a `continue-on-error` + aggregate-gate design
  was rejected: it masks `conclusion` and adds a moving part.

`telegram_summarizer` is deliberately **not** isolated — it keeps `if: always()`
and no `continue-on-error`, so its own failure hard-fails the job (§IV). The invariant is
guarded statically by `tests/test_workflow_isolation.py::TestPipelineStepIsolation`, which
*derives* the pipeline set from the workflow (any step running `kinozal_scraper.*_pipeline`)
so a newly-added source is automatically held to it — a hand-maintained list would let the
next source slip back into the cascade.

**Readable per-source alert (#310).** A failed scraper step used to reach the operator only as
the generic `Send fallback failure alert` (`⚠️ … run failed: <url>`) — visible but not
*actionable*: which source, which error class lived only in the CI log (precedent: run
29224080924, soldout 403 required a log dig). Each scraper `__main__` now calls
`alerting.report_failures(notifier, results)` before `sys.exit(1)`: it sends a readable
`source_id: <error>` breakdown to Telegram (reusing `PipelineResult.errors`) and, **on
successful delivery only**, writes the job-global marker `.run/technical_alert_sent`. That
marker gates the `Send fallback failure alert` step (`hashFiles(...) == ''`), so a delivered
rich alert suppresses the generic curl one.

The marker is **job-global**: it means *"≥1 rich alert delivered this run"*, not "this step
delivered". So the curl fallback stays the net only for the **first** undelivered alert (or a
crash *before* `report_failures` — import error, etc.). If a **second** step's alert delivery
fails after an earlier one already set the marker, the backstop is the **red run + logs**
(§III), not curl — a consciously accepted gap (no per-step marker infra; see #310 Out of
scope). `telegram_summarizer` keeps its own richer `deliver_results` alert path; `report_failures`
and the marker helpers share one canonical home in `alerting.py`.

### Run summary: reading the per-source metrics line

Both GitHub steps publish one line per source to the job log and, when
`GITHUB_STEP_SUMMARY` is set, to the GitHub Actions Step Summary (#459):

```text
github_new_popular: fetched=100 extracted=100 existing=93 new=7 sent=7 stored=7
```

- `fetched` — records/rows the source handed us; `extracted` — those that became
  items. A gap between them means records failed extraction. On
  `github_new_popular` that is already red (any bad record fails the source); on
  `github_trending` a *partial* failure stays green — the rows that parsed are
  worth delivering — but the reasons are printed under the counters (see below),
  so the gap is never unexplained. The asymmetry is deliberate: trending scrapes a
  page whose markup shifts cosmetically all the time, while popular reads a
  versioned JSON API where a record without `full_name` means the response
  contract changed and no item's identity can be trusted.
- `existing` — how many of the **examined candidates** were already known: rows
  in `github_projects` *plus* any repo seen twice within the run (the same repo on
  two search pages counts once as new, once as existing). It is *not* the size of
  the tab.
- `new` — candidates not yet known. Normally `new == sent`: paging stops as soon
  as `limit` new items are in hand, so a remainder only arises from overshoot
  *within* the last page. When it does (`new=12 sent=10`), the extra two wait for
  the next run. On `github_new_popular` they reliably come back — the query is
  stable. On `github_trending` they may not: the page churns daily, so a repo not
  delivered today can simply be off it tomorrow.
- `stored` — rows written to Sheets, i.e. confirmed deliveries.

Per-source **errors and warnings** are printed under the counters (bounded, then
collapsed into a count), so the numbers never stand without a reason. Not every
anomaly shows up as a gap between counters — a fully drifted `metric` column and a
scan truncated by the API's result ceiling both leave the counters looking healthy,
which is exactly why they travel here too:

```text
github_trending: fetched=25 extracted=23 existing=23 new=0 sent=0 stored=0
github_trending:   warning: [github_trending] row missing required field(s): dedupe_key='' title=''
github_trending:   warning: 25 of 25 rows have an empty metric — page layout may have drifted: …
```

Failures also reach Telegram through `report_failures`; the summary is the surface
that pairs them with the counters of the same run.

**`new=0` is green, and now explains itself.** `existing=100 new=0` means we
looked at a hundred candidates and knew every one — a normal quiet day. That used
to be indistinguishable from "the source returned nothing", which is what let
[#459](../adr/0003-limit-means-delivered-new-items.md) run silently. An extraction
that genuinely produced zero items is still red.

The line is published **before** the step computes its exit code, so it survives a
failed run. A summary file that cannot be written degrades to a WARNING — it is a
report channel and must not redden an otherwise-successful run.

**Only the two GitHub steps have a run summary at all.** `steam`, `kinozal` and
`soldout` are separate workflow steps that never call `publish_run_summary`, so the
absence of a Step Summary there is not the mechanism omitting them — there is
nothing to omit. Where counters genuinely can be missing is a source that died on an
unhandled error before anything was measured: then the summary prints the reason
without a counter line, so "nobody counted" stays distinct from "the source fetched
nothing".

## Soldout: терпеливый ретрай и место шага в прогоне

Единственный источник со **своим** расписанием ретраев. Cloudflare режет датацентровые
IP-диапазоны GitHub Actions вероятностно, и недельный замер показал, что исход прогона решает не
число попыток, а их разнос во времени: четыре попытки за ~7 секунд бьют в одно и то же решение
Cloudflare и стоят одной. Отсюда 12 красных ночных прогонов подряд. Решение и разбор
альтернатив — [ADR-0002](../adr/0002-soldout-cloudflare-spread-retries.md); политика в коде —
`retry_antibot_patient` (`http_retry.py`), точка применения — `fetch_html_patient`.

**Что это значит для оператора:**

- **Шаг идёт до ~4.6 часа** (24 попытки с паузой 12 минут). Это норма, а не зависание: каждая
  пауза печатает строку `WARNING` из `http_retry`, каждая попытка — `[http_fetch] … cf-ray=…
  cf-mitigated=…` из `_get_once`. Молчащий шаг — единственный признак настоящей проблемы.
- **Шаг стоит последним, после суммаризатора.** Иначе те же часы ожидания сдвинули бы доставку
  всех остальных источников. Инвариант статически пиньется
  `tests/test_workflow_isolation.py::TestSoldoutStepPlacement` — порядок ключей в YAML сам себя
  не защищает.
- **`timeout-minutes: 300`** — зажат с двух сторон. Снизу: худший случай самой политики — 288
  минут, меньше него таймаут убивал бы штатный прогон. Сверху: 360-минутный потолок job'а
  считается **от старта job'а**, а не шага, и job, убитый по нему, GitHub *отменяет*, а не
  проваливает — `if: failure()` у fallback-алерта тогда не сработает, и §IV-сигнал пропадёт ровно
  в той патологии, ради которой таймаут и заведён. Отсюда 60 минут резерва на все предшествующие
  шаги. Обе границы выведены из констант политики в
  `tests/test_workflow_isolation.py::TestSoldoutStepPlacement`, а не вписаны числом.
- **Красный soldout — ожидаемое состояние, а не инцидент.** Часть суток блок не пробивается
  вовсе; алерт при этом приходит не чаще одного раза в сутки (прогон-то один). Памяти «когда был
  последний успех» нет сознательно — принятый пробел записан в
  [`coverage-gaps.md`](coverage-gaps.md).

**Побочный эффект переноса, который иначе прочитают как случайность.** Маркер
`.run/technical_alert_sent` job-global, и до переноса его почти всегда ставил soldout — он падал
первым и чаще всех, глуша curl-fallback для более поздних шагов. Теперь маркер ставит тот, кто
упал раньше, а soldout — последний. Разбирая алертинг, учитывай это при чтении старых прогонов.

## Environment variables

### Shared across pipelines

| Variable | Type | Used by |
|---|---|---|
| `CREDENTIALS` | secret | github_popular_pipeline, soldout_pipeline, kinozal_pipeline (Google Sheets service account JSON) |
| `SPREADSHEET_URL` | secret | github_popular_pipeline, soldout_pipeline, kinozal_pipeline |
| `TELEGRAM_BOT_TOKEN` | secret | all 4 steps |
| `TELEGRAM_CHAT_ID` | secret | all 4 steps |

### github_popular_pipeline / github_trending_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | secret | GitHub API auth (github_popular_pipeline only) |
| `GH_TOP_LIMIT` | var | max **new** GitHub repos to notify about per run (github_popular_pipeline only; default 10). Not a fetch budget — the number of candidates examined is `per_page` × pages, decided in code (#459, [ADR-0003](../adr/0003-limit-means-delivered-new-items.md)) |
| `GH_TRENDING_LIMIT` | var | max **new** GitHub trending repos to notify about per run (github_trending_pipeline; default 10). The whole page is examined regardless |
| `GOOGLE_API_KEY` | secret | Gemini API for enrichment |
| `LLM_MODEL` | var | preferred Gemini model |

### steam_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `STEAM_TOP_LIMIT` | var | max Steam Most Played entries to fetch |

### soldout_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `SOLDOUT_URL` | var | Soldout events page URL |

### kinozal_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `API_KEY` | secret | Kinozal API key |
| `KINOZAL_URLS` | var | Kinozal page URLs to scrape, формат `label\|url;...`; local fallback — env `KINOZAL_TOP_URL` (plain url). Если не задано ни то ни другое — pipeline логирует ошибку `no URLs configured`. Легаси-имя `URLS` **больше не читается** (clean rename, #263). `sources.json` `url`/`base_url` для скрейпинга **не читается** (только schema-placeholder), см. `kinozal_pipeline.py::_kinozal_urls` |
| `KINOZAL_EXCLUDED_GENRES` | var | **Опционально.** `;`-разделённый denylist жанров (case-insensitive), напр. `Hidden objects`. Новый элемент, чей жанр (с details-страницы) в списке, **не** уведомляется, но сохраняется в Sheets (dedup). Пусто/не задано → фильтр выключен, details-страницы не запрашиваются (0 оверхеда). См. `kinozal_pipeline.py::_split_by_excluded_genre` (#263) |
| `KINOZAL_USERNAME` | secret | **Опционально.** Логин аккаунта на зеркале `kinozal.guru` — включает автоматический fallback на зеркало при сбое `kinozal.tv`. Что именно включается и как меняются ссылки — [`pipeline.md` § Kinozal mirror fallback](pipeline.md#kinozal-mirror-fallback). Парный к `KINOZAL_PASSWORD`; **partial** (только один из двух) → WARNING + fallback отключён (не fail) |
| `KINOZAL_PASSWORD` | secret | **Опционально.** Пароль аккаунта `kinozal.guru`. Парный к `KINOZAL_USERNAME` |

### telegram_summarizer

| Variable | Type | Purpose |
|---|---|---|
| `CHANNEL_URL` | var | semicolon-separated Telegram channel URLs/IDs |
| `GOOGLE_API_KEY` | secret | Gemini API for summarization |
| `API_HASH` | secret | Telethon app hash — **required, empty value fails fast** |
| `TELEGRAM_API_ID` | secret | Telethon app ID — **required, empty value fails fast** |
| `TELETHON_SESSION` | secret | Telethon session string — **required, empty value fails fast** |
| `LLM_MODEL` | var | preferred Gemini model |

`require_env` (`telegram_summarizer.py`) rejects an empty value, not just a missing
key: GitHub Actions expands an **undefined secret into an empty string**, so
`os.environ["X"]` cannot tell "not configured" from "configured". That gap is what
kept a user-account Telethon session (`anon.session.encrypted`) live in this public
repo — `TELETHON_SESSION` was never set, so the reader silently fell back to the
committed session file (#386).

#### Minting a new `TELETHON_SESSION`

Run locally, once, with the app credentials of the same Telegram account. Telethon
asks for the phone number and the login code; the printed string **is** a
credential — put it straight into the secret, never into a file in the repo:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())
```

```bash
gh secret set TELETHON_SESSION   # paste the string
```

Revoking the old session (Telegram → Settings → Devices) is what actually
invalidates a leaked one — re-encrypting or rotating a key cannot un-publish a blob.
