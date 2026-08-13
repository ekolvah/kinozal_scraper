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
| `KINOZAL_URLS` | var | Kinozal page URLs to scrape, format `label\|url;...`. Production includes film, series, and game pages. The response's selected readable category is compared with `KINOZAL_EXCLUDED_CATEGORIES`; numeric `t=` remains provenance only. Local fallback `KINOZAL_TOP_URL` follows the same rule. If neither variable is set, the pipeline logs `no URLs configured`. The legacy name `URLS` is **no longer read** (clean rename, #263). `sources.json` `url`/`base_url` is **not read** for scraping (schema placeholder only); see `kinozal_pipeline.py::_kinozal_urls` |
| `KINOZAL_EXCLUDED_CATEGORIES` | var | **Required.** `;`-separated, case-insensitive denylist of readable Kinozal selector names. Production: `Избранные раздачи;Топ Музыки;Библиотека;Избранные аудиокниги;Избранные программы`. A parent such as `Топ Музыки` also matches descendants such as `Топ Музыки > Русская`. Matching stops before extraction, trailer lookup, and notification. Empty or stale configuration is a visible error but fails open, as does selector drift, so an unknown film category is never silently withheld. See the [listing-category guard](pipeline.md#trailer-retrieval-and-selection) (#506) |
| `KINOZAL_EXCLUDED_GENRES` | var | **Optional.** `;`-separated denylist of details-page `Жанр` values (case-insensitive), e.g. `Hidden objects`. A new item whose genre is in the list is **not** notified, but is saved to Sheets (dedup). Empty/unset → filter disabled; details pages are not requested (zero overhead). This is independent from `KINOZAL_EXCLUDED_CATEGORIES`: genres such as `Фантастика` can belong to both films and audiobooks and therefore cannot identify content type safely. See `kinozal_pipeline.py::_split_by_excluded_genre` (#263, #506) |
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

## Claude Code development telemetry

This is maintainer-workstation observability, not scraper runtime telemetry.
Claude Code exports its native metrics and events directly to Grafana Cloud;
there is no repository collector, daemon, hook, scheduled analysis, or model
consumer. The repository-owned assets live in `observability/claude-code/`, and
the decision and external-service trade-offs are in
[ADR-0006](../adr/0006-claude-code-telemetry-in-grafana-cloud.md).

### User-scope setup

Copy the names from `observability/claude-code/otel.env.example` into the
Windows user environment or the user-level Claude settings. Substitute values
only outside git. The project `.env` remains the application's local secret
carrier; Claude Code does not automatically use it as its own process
environment.

The required Grafana Cloud access-policy scopes are only `metrics:write` and
`logs:write`. Use the stack's base OTLP endpoint ending in `/otlp`. Two settings
are load-bearing for the current direct exporter:

* `OTEL_EXPORTER_OTLP_HEADERS` uses a literal space in
  `Authorization=Basic <credential>`. The generic Grafana wizard can render the
  space as `%20`; Claude Code 2.1.220 sends that form literally and Grafana
  rejects it as missing credentials.
* `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` is required by
  the Grafana Claude Code integration. Without it, log export succeeds while
  the metrics request is rejected as bad input.

Restart the terminal or Claude Code after changing user environment variables;
an already-running process retains its inherited environment. Keep the normal
export intervals at 60 seconds for metrics and 5 seconds for events. Shorter
intervals are for a bounded setup probe only.

### Verify and import

1. Run one real Claude session long enough for both export intervals.
2. In Claude debug output, confirm that the first metrics and logs exports both
   succeed. A successful Claude response alone does not prove telemetry delivery.
3. In Grafana Explore, select the stack Metrics datasource and confirm a
   `claude_code_*` metric. Select the stack Logs datasource and query
   `{service_name="claude-code"}`.
4. Import the shared `observability/agent-telemetry/dashboard.json` through
   **Dashboards → New → Import**, then select the stack Metrics and Logs
   datasources. A Grafana service-account token with Editor permission is
   needed only for API-driven verification/import, not for ingestion.

The dashboard uses only signal names and attributes captured from the real
destination. A missing compaction, agent, or skill dimension is displayed as
unavailable, never as zero. Claude's cost metric is estimated and must not be
used as a billing source of truth.

### Fourteen-day baseline

Grafana Cloud Free remains usable after the trial, but metrics and logs have a
rolling 14-day retention window. At the end of the first complete window,
review session cost, cost per API request, cache-read tokens per request, the
context-size proxy, compaction availability, tool failure rate, and active/wall
duration. Also record active-series/cardinality and ingested log volume in the
Grafana usage view.

Create a separate threshold/notification issue only when a measured boundary
has both a named operator action and a tolerable observed false-positive rate.
Otherwise the threshold remains YAGNI. Keep `scripts/token_trend.py` and its
SessionStart hook: the local ledger retains git-branch attribution and history
that the 14-day Grafana window does not provide.

### Rollback and rotation

To stop export, remove `CLAUDE_CODE_ENABLE_TELEMETRY` and the `OTEL_*` exporter
variables from the user environment, then restart Claude Code. Revoke the
Grafana Cloud access-policy token to invalidate ingestion immediately. Revoke
the Grafana service-account token separately if API import/query access is no
longer needed. Deleting the dashboard alone does not stop data ingestion.

## Codex development telemetry through Alloy

Codex uses the same Grafana Cloud metrics stack, but it does not export there
directly. The current app-server exporter fixes metric temporality to Delta,
which Grafana Cloud rejects for some Codex sums and histograms. A local Grafana
Alloy 1.18.1 bridge receives metrics on loopback, converts them to Cumulative,
batches them, and exports them to the cloud. The compatibility decision and its
removal condition are in
[ADR-0007](../adr/0007-export-codex-metrics-to-grafana-cloud.md).

### Install and configure

Install the official Windows amd64 Alloy 1.18.1 binary under the user profile
and verify the release SHA-256. Copy
`observability/codex/config.alloy.example` to
`%USERPROFILE%\.config\alloy\config.alloy` and copy the tables from
`observability/codex/otel.toml.example` to `~/.codex/config.toml`. Do not place
substituted values in the repository.

The Alloy process needs three user environment variables:

* `GRAFANA_CLOUD_OTLP_ENDPOINT` — the stack base OTLP URL ending in `/otlp`;
* `GRAFANA_CLOUD_INSTANCE_ID` — the OTLP access-policy username;
* `GRAFANA_CLOUD_OTLP_TOKEN` — a stack-scoped token with only `metrics:write`.

The Grafana dashboard API service-account token is separate and is not given to
Alloy. Validate the safety contract and Alloy syntax before start:

```powershell
python scripts/check_codex_otel_config.py
& "$env:LOCALAPPDATA\GrafanaAlloy\1.18.1\alloy.exe" validate `
  --stability.level=experimental `
  "$env:USERPROFILE\.config\alloy\config.alloy"
```

Run Alloy hidden in the user-logon task `Kinozal Codex Alloy` with the same
pinned executable, config, and
`--stability.level=experimental`; set `--storage.path` under
`%LOCALAPPDATA%\GrafanaAlloy\data`. The experimental stability flag is required
by `otelcol.processor.deltatocumulative`. The explicit `stderr` log destination
keeps a non-admin user-logon task out of the Windows Event Log; the launcher
redirects that stream to the local data directory. The receiver must remain
`127.0.0.1:4318`, not `0.0.0.0` or `[::]`.

### Verify Codex delivery

1. Confirm `http://127.0.0.1:12345/-/ready` returns HTTP 200 and port 4318 is
   listening only on loopback.
2. Restart VS Code after changing `~/.codex/config.toml`; an existing
   `app-server` retains its process-global telemetry configuration.
3. Complete one real Codex turn and wait at least one 60-second metrics export
   interval. A completed turn alone does not prove delivery.
4. Confirm Alloy reports accepted points, sent points, and zero failed points.
   Then query Grafana for `codex_*` metrics.
5. Import `observability/agent-telemetry/dashboard.json`, selecting the existing
   Metrics and Logs datasources. Codex panels use Metrics only; the retained
   Claude panels still use both.

The values-free catalogue contains only metric and attribute names observed
after successful cloud ingestion. Native Codex metrics currently have no git
branch or GitHub issue dimension and expose aggregate tool-call volume without
tool name or success. The dashboard displays those gaps rather than inferring
them or rendering them as zero.

### Restart, failure, and rollback

If Alloy is down, Codex export fails at the loopback boundary and telemetry is
unavailable; it is not zero usage. Check Alloy's local log and component UI
before restarting the process. Re-run both validators after any Alloy or Codex
upgrade and repeat the live capture before changing the catalogue.

To stop Codex telemetry, remove the `[analytics]` and `[otel]` tables from
`~/.codex/config.toml`, restart VS Code, stop Alloy, and disable/remove its
user-logon task. Remove `GRAFANA_CLOUD_INSTANCE_ID` and
`GRAFANA_CLOUD_OTLP_TOKEN` from the user environment. Revoke the OTLP token to
invalidate ingestion immediately. Deleting the dashboard does not stop export.
