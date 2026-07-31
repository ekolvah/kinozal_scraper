# Operations — как прод-прогон исполняется и чем конфигурируется

**На какой вопрос отвечает этот файл:** как ежедневный прод-прогон эксплуатируется — расписание и
порядок шагов, чем он конфигурируется (env-переменные и секреты), как изолируются падения
отдельных источников и куда уходит алерт, какие runbook'и нужны оператору (ротация секрета,
снятие матрицы пробника).

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
5. **soldout_pipeline.py** — Soldout events
6. **kinozal_pipeline.py** — Kinozal movies
7. **telegram_summarizer.py** — `if: always()` (runs even if earlier steps fail)

### Pipeline-step isolation

**Root cause it fixes (#245):** GitHub Actions steps carry an *implicit* `if: success()`. So a
single step's `exit 1` used to cascade into **skipping every later step** — a transient
third-party 403 in `soldout_pipeline` skipped `kinozal_pipeline` and suppressed movie
delivery for that run ([run 28493805028](https://github.com/ekolvah/kinozal_scraper/actions/runs/28493805028)).
Per-source isolation existed *inside* each `run_*_pipeline`, but not *between* the workflow
steps.

**Fix:** each pipeline step (2–6) carries
`if: ${{ !cancelled() && steps.tests.outcome == 'success' }}`:

- `!cancelled()` — the step runs even if an **earlier pipeline** step failed (defeats the
  implicit `if: success()` cascade), so a flaky source can't suppress an unrelated one.
- `steps.tests.outcome == 'success'` — but a **red smoke gate still skips every pipeline**
  (the hard prerequisite is preserved; that's why the gate carries `id: tests`).
- **No `continue-on-error`** — a failed source still exits 1 → job goes red → the existing
  `Send fallback failure alert` (`if: failure()`) fires. Failure stays visible (§IV); it is
  *not* masked into a green job. This is why a `continue-on-error` + aggregate-gate design
  was rejected: it masks `conclusion` and adds a moving part.

`telegram_summarizer` (step 7) is deliberately **not** isolated — it keeps `if: always()`
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

## Soldout probe workflow (`soldout-probe.yml`) — ВРЕМЕННЫЙ

Измеритель доступности `soldoutticketbox.com` из датацентра GitHub Actions.
**Не гейт и не источник данных** — только замер, на который опирается решение по обходу
Cloudflare.

**Зачем.** RCA #396 установил, что исход ежедневного прогона решает единственная величина —
пробьёт ли хоть одна из 4 попыток вероятностный `cf-mitigated=challenge`. Историю по ней не
восстановить: диагностика `describe_block` приехала только 25.07 (#358). Без числа выбор между
«разнести попытки во времени» (бесплатно) и «residential-прокси» (платный сервис + секрет) —
покупка вслепую. Вторая причина: до пробника единственный способ дёрнуть fetch из датацентра —
прогнать `run-script.yml`, а он **рассылает в Telegram-канал**, т.е. каждая проверка гипотезы
стоила пользователю сообщений. У пробника нет ни одного `secrets.*`.

**Решающее правило (пред-зарегистрировано в issue до сбора данных).** Тайм-бокс 7 суток,
8 запусков в сутки × 3 попытки = 24 замера. Если ≥1 успех был в **каждые** сутки — достаточно
разнести попытки, прокси не покупаем; если хоть в одни сутки 0 из 24 — нужен другой egress.
Величина наблюдаемая напрямую, без предположений о независимости попыток.

**Коды выхода.** 403 — это *результат измерения*, прогон зелёный: красный workflow каждые
3 часа обесценил бы алертинг остальных. Красным пробник становится только при сбое **самого
инструмента** — пустой `PROBE_URL`, не-HTTP исключение, истёкший срок.

**Снять матрицу:**

```bash
gh run list --workflow=soldout-probe.yml --limit 60 --json databaseId -q '.[].databaseId' \
  | while read -r id; do gh run view "$id" --log | grep PROBE; done
```

В строке: `ts=` (UTC), `kind=html|poster`, `attempt=n/3`, `elapsed=`, `status=` + диагностика
`describe_block`. Меряются **оба** ломающихся пути: RCA дал разные доли (страница 1/4, постеры
0/8), поэтому «постеры вылечатся тем же обходом» — гипотеза, а не факт. Учти, что на блоке в логе
**две** строки: своя `PROBE …` и уже существующая `[http_fetch] …` из `_get` — `grep PROBE`
отбирает только замеры.

**Удаление.** Пробник, его workflow и `tests/test_probe.py` удаляются вместе с решением по #396.
Забыть не даст `_EXPIRES` в `scripts/probe.py`: после этой даты шаг печатает
`probe expired … delete per #396` и краснеет — механизм вместо обещания в доке.

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
| `GH_TOP_LIMIT` | var | max GitHub repos to fetch (github_popular_pipeline only) |
| `GH_TRENDING_LIMIT` | var | max GitHub trending repos to fetch (github_trending_pipeline; default 10) |
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
