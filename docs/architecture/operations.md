# Operations — how the production run executes and is configured

**Question this document answers:** how the daily production run is operated — its schedule and
step order, how it is configured (environment variables and secrets), how failures of
individual sources are isolated and where alerts go, and which runbooks the operator needs
(secret rotation).

**What is not here** — one question per file ([`project-map.md`](project-map.md) §"Heading
convention"), and this file assembles heterogeneous blocks, so its boundary is explicit:
quality gates on the change path (local `ci_check`, `ci.yml`, cloud review, model pinning) →
[`ci.md`](ci.md); available pipelines, Protocol boundaries, and data flow →
[`runtime.md`](runtime.md); extraction layers, `extract_from_*` contracts, and fetch behaviour
(including the Kinozal mirror fallback) → [`pipeline.md`](pipeline.md).

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
github_new_popular: fetched=10 extracted=10 existing=8 new=2 sent=2 stored=2
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
- `existing` — how many of the examined top-N entries were already in
  `github_projects`. It is *not* the size of the tab.
- `new` — entries not yet known; every one of them is delivered, so `new == sent`
  unless delivery itself failed.
- `stored` — rows written to Sheets, i.e. confirmed deliveries.

Per-source **errors and warnings** are printed under the counters (bounded, then
collapsed into a count), so the numbers never stand without a reason. Not every
anomaly shows up as a gap between counters — a fully drifted `metric` column leaves
them looking perfectly healthy, which is exactly why warnings travel here too:

```text
github_trending: fetched=10 extracted=8 existing=8 new=0 sent=0 stored=0
github_trending:   warning: row missing required field(s): dedupe_key='' title=''
github_trending:   warning: row missing required field(s): dedupe_key='' title=''
github_trending:   warning: 8 of 8 rows have an empty metric — page layout may have drifted: …
```

Two rows failed extraction, hence `fetched=10 extracted=8` and one warning each.
The two lines are identical on purpose, not a typo: `github_trending` reads both
`dedupe_key` and `title` from the same selector (`h2 a@href`), so every row that
fails there fails the same way. The drift check runs over the **extracted** items,
so its denominator is 8, not 10 — a row that never became an item has no field to
be blank.

Failures also reach Telegram through `report_failures`; the summary is the surface
that pairs them with the counters of the same run.

**`new=0` is green, and now explains itself.** `existing=10 new=0` means the
source's top-N was examined and every entry was already known — a normal quiet day,
which is the expected outcome whenever the top-N did not change. That used to be
indistinguishable from "the source returned nothing" (#459). An extraction that
genuinely produced zero items is still red.

The line is published **before** the step computes its exit code, so it survives a
failed run. A summary file that cannot be written degrades to a WARNING — it is a
report channel and must not redden an otherwise-successful run.

**Only the two GitHub steps have a run summary at all.** `steam`, `kinozal` and
`soldout` are separate workflow steps that never call `publish_run_summary`, so the
absence of a Step Summary there is not the mechanism omitting them — there is
nothing to omit. Both GitHub sources allocate their counters before any work starts,
so in practice every source they report has a counter line; the `metrics is None`
branch is defence for a future pipeline that reports messages without instrumenting
counters, keeping "nobody counted" distinct from "the source fetched nothing".

## Soldout: patient retries and the step's position in the run

The only source with **its own** retry schedule. Cloudflare probabilistically blocks the
datacentre IP ranges of GitHub Actions, and a week-long measurement showed that the outcome is
decided not by the number of attempts but by their spacing in time: four attempts over ~7
seconds hit the same Cloudflare decision and count as one. Hence 12 consecutive failed nightly
runs. The decision and alternatives analysis are in
[ADR-0002](../adr/0002-soldout-cloudflare-spread-retries.md); the code policy is
`retry_antibot_patient` (`http_retry.py`), applied at `fetch_html_patient`.

**What this means for the operator:**

- **The step takes up to ~4.6 hours** (24 attempts with a 12-minute pause). This is normal, not
  a hang: every pause prints a `WARNING` line from `http_retry`, and every attempt prints
  `[http_fetch] … cf-ray=… cf-mitigated=…` from `_get_once`. A silent step is the only sign of a
  real problem.
- **The step comes last, after the summariser.** Otherwise those same waiting hours would delay
  delivery from every other source. The invariant is statically pinned by
  `tests/test_workflow_isolation.py::TestSoldoutStepPlacement` — YAML key order does not protect
  itself.
- **`timeout-minutes: 300`** is constrained from both sides. Below: the policy's own worst case
  is 288 minutes, so a smaller timeout would kill a normal run. Above: the 360-minute job cap is
  counted **from job start**, not step start, and GitHub *cancels* rather than fails a job killed
  by it — the fallback alert's `if: failure()` would then not run, and the §IV signal would be
  lost in exactly the pathology the timeout is for. Hence a 60-minute reserve for all preceding
  steps. Both limits are derived from policy constants in
  `tests/test_workflow_isolation.py::TestSoldoutStepPlacement`, not written as numbers.
- **A failed Soldout is an expected state, not an incident.** For part of the day the block is
  unreachable altogether; the alert nevertheless arrives no more than once per day (there is
  only one run). There is deliberately no memory of "when the last success was" — the accepted
  gap is recorded in
  [`coverage-gaps.md`](coverage-gaps.md).

**A side effect of the move that could otherwise be read as accidental.** The
`.run/technical_alert_sent` marker is job-global, and before the move Soldout almost always set
it — it failed first and most often, suppressing the curl fallback for later steps. Now the
marker is set by whatever failed earlier, while Soldout is last. Take this into account when
reading old runs while investigating alerts.

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
| `GH_TOP_LIMIT` | var | how deep into the star-ranked search result to look — the top-N of `created:>=T-30 stars:>1000` (github_popular_pipeline only; default 10). Also the `per_page` of the single request |
| `GH_TRENDING_LIMIT` | var | how many rows off the top of today's trending page to consider (github_trending_pipeline; default 10) |
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
| `KINOZAL_URLS` | var | Kinozal page URLs to scrape, format `label\|url;...`; local fallback — environment variable `KINOZAL_TOP_URL` (plain URL). If neither is set, the pipeline logs `no URLs configured`. The legacy name `URLS` is **no longer read** (clean rename, #263). `sources.json` `url`/`base_url` is **not read** for scraping (schema placeholder only); see `kinozal_pipeline.py::_kinozal_urls` |
| `KINOZAL_EXCLUDED_GENRES` | var | **Optional.** `;`-separated denylist of genres (case-insensitive), e.g. `Hidden objects`. A new item whose genre (from the details page) is in the list is **not** notified, but is saved to Sheets (dedup). Empty/unset → filter disabled; details pages are not requested (zero overhead). See `kinozal_pipeline.py::_split_by_excluded_genre` (#263) |
| `KINOZAL_USERNAME` | secret | **Optional.** Account login for the `kinozal.guru` mirror — enables automatic fallback to the mirror when `kinozal.tv` fails. What is enabled and how links change — [`pipeline.md` § Kinozal mirror fallback](pipeline.md#kinozal-mirror-fallback). Paired with `KINOZAL_PASSWORD`; **partial** (only one of the two) → WARNING + fallback disabled (not failure) |
| `KINOZAL_PASSWORD` | secret | **Optional.** `kinozal.guru` account password. Paired with `KINOZAL_USERNAME` |

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
