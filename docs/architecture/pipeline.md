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
until sources become real (#6, #7).

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

## Kinozal item-category filtering

`KINOZAL_URLS` selects the listing pages to inspect; it is not a content-type
allowlist. A listing such as `top.php?t=0` can contain films, books, music and
software together, and the listing markup has no reliable per-row type marker.
The pipeline therefore classifies each **new item** from its own `details.php`
page: exactly one `img.cat_img_r` supplies Kinozal's category id through
`onclick="cat(N)"`, with `/pic/cat/N.gif` as the fallback. Zero or multiple
markers leave the category unknown.

The committed id-to-name table mirrors the authenticated `browse.php?c=`
taxonomy read on 2026-08-13. The numeric details-page id is authoritative; the
table only makes it readable for operators and contains no delivery policy. It
is not fetched at runtime because that would add an authenticated request whose
failure could disable the whole denylist. A future id absent from the table is
therefore unknown and delivered fail-open until the table is updated.

`KINOZAL_EXCLUDED_ITEM_CATEGORIES` owns the delivery policy as a
semicolon-separated, case-insensitive list of readable names with normalized
whitespace. A full name matches exactly; a group prefix such as `Музыка`
matches every `Музыка - ...` descendant. Categories not named by the operator
remain allowed regardless of their numeric id. A denied item is stored for
dedup but is not notified and never reaches YouTube trailer lookup.

Category and `KINOZAL_EXCLUDED_GENRES` filtering share one details fetch per
new item, with category evaluated first. The pass runs when either denylist is
configured and makes no details request only when both are empty. A failed
fetch, missing or ambiguous marker, unparseable id, or unknown id keeps the
individual item and logs a WARNING. If category resolution succeeds for zero
of one or more new items, or configuration names are absent from the committed
taxonomy, the items still flow through but the source gains a visible pipeline
error. An empty category denylist is a normal disabled state and logs INFO.

Each new item's raw provenance records `kinozal_listing_url`,
`kinozal_item_category`, and `kinozal_item_category_name`. Its post-filter INFO
breadcrumb reports `delivered`, `denied by category <name>`, or
`denied by genre <name>`, so a mixed listing remains diagnosable without
rejecting the whole feed (#506).

## Trailer retrieval and selection

The epic separates **retrieval** (`film → list[Candidate]`) from **selection**
(`(profile, candidates) → pick`, `trailer_strategy.py`, #139/#141/#144). The data-prep layer:

- `youtube.search_candidates(profile)` (`youtube.py`) — the candidate pool is the **union**
  of queries by RU and original title, deduplicated by `video_id`, **without** year/title filtering
  (year filters selection, not retrieval). An RU trailer must be in the pool when it exists
  (#315 — retrieval breadth). Failure of one union branch does not fail the pool (§IV best effort).
  Shared retrieval reuses the `scripts/eval_trailers.py --record` harness (§II).
- `build_film_profile(item, fetcher)` (`kinozal_pipeline.py`) — a richer `FilmProfile` builder
  (cast/director/genre/description) from `details.php` through shared
  `_parse_labeled_field` (the same sibling walk as `_parse_genre`). Fetch/parse failure →
  degradation to title+year + WARNING; successful fetch with zero fields → WARNING tripwire (§IV).
  For harness eval (#140) and potential cast escalation; production does not call it (below).

**Game releases (#385, #412).** `KINOZAL_URLS` contains the games top (`t=7`) alongside films
(`t=0`) and series (`t=32`) — all flow into one `kinozal_movies` source. Their title grammar is
**different**: `Название / x64 / RU / Жанр / Год / Формат / PC (Windows)` versus the film
`RU / Original / Year / Format`. Therefore `original_title` (the second ` / ` segment) yielded
architecture for games, and YouTube received `x64 2024 trailer` — 27 such queries in run
[30143534431](https://github.com/ekolvah/kinozal_scraper/actions/runs/30143534431).

**The discriminator is segment form, not listing category.** Category (`t=7`) is unsuitable for
this role: in a localized game, `Marvel Человек-Паук 2 / Marvel's Spider-Man 2 (Digital Deluxe Edition) / x64 / …`, the original appears in exactly the same position as for a film, and
category-based suppression would leave only the Russian title in the query, which YouTube lacks —
`no trailer found` despite five official trailers in results (#412). Therefore `original_title`
(`text_utils.py`) suppresses a **service** second segment: year, architecture (`x64|x86|x32`), and
language code (`RU|EN`). The set is closed by measuring all 3,764 raw titles from Sheets — `x64` 888,
`RU` 139, `EN` 1, and no other service text occurs in this position; 96 game releases contain a real
original title there (78 of them with an edition suffix in parentheses, removed later by matching in
`HeuristicStrategy._relevant`; do not confuse them with 160 non-game releases where parentheses hold
an alternative title). The same measurement forbids the “short segment → service” heuristic:
`Silo`, `From`, `Halo`, `Apex` are real titles, so the discriminator checks an exact literal. The
guard applies to all sources at once, including `build_film_profile`, where listing category is not
propagated at all. Cost: a localized game uses **2** `search.list` calls instead of one (#384).
Service-segment suppression is silent, so one INFO line per item in `search_candidates` with actual
queries provides §IV visibility into the grammar: without it, a new service literal
(`RUS`, `Multi`, `Update 5`) would go to YouTube as a “title”, and the outcome would be
indistinguishable from an honest “trailer does not exist”. `dedupe_key` deliberately does not parse
game grammar: it cuts at the year segment and is stable for games, while an “as well” change is the
same class of defect (#363).

**Production composition (#144):** `enrich_with_trailer(item, youtube)` builds a lightweight
title+year `FilmProfile` (ru_title=clean, original_title=second segment or "", year) and
delegates `select_trailer(profile, youtube)` →
`youtube.search_candidates` (union #140) → `HeuristicStrategy().pick` (#141, = eval
`default_strategy()`) → `video_id` into the YouTube URL. RU trailers have priority, EN is fallback
(#138, #315). An empty pick → §IV miss marker + INFO; a retrieval exception (including
`TrailerRetrievalError` — every union branch failed, #383) → §IV error marker + WARNING; success →
INFO breadcrumb `video_id`/`reason`/`confidence`; the miss branch writes pool size:
`YouTube ничего не вернул` and `вернул N, ни один не прошёл relevance` are different bugs, and
without `video_id`, the report `пришла не та ссылка` is impossible to diagnose (#359).

**Why composition is split in two (#379).** `select_trailer` is everything between profile and user;
`enrich_with_trailer` only derives a profile from the Kinozal title. The split is not cosmetic: a
scorecard over `pick` is blind to the layer **above** the strategy — a policy changing delivery for
10 films (measurement 26→16, #359) does not move it by a point. The gate
`tests/test_eval_baseline.py::TestBaselineGate::test_reverted_359_policy_fails_the_gate`
turns red for such a policy in the **delivery** column. Therefore the measurement enters
`select_trailer`, and its result is pinned in
`tests/fixtures/trailer_baseline.json` (see [testing.md](testing.md#eval-harness--trailer-selection)).
The seam is at `FilmProfile` — the natural golden-set form: if input were `NormalizedItem`, fixtures
would have to duplicate Kinozal title grammar (§II). The converse is that the **lower** half
(clean-title / `original_title` / year regex / service-segment grammar) is not covered by the
measurement and relies on unit tests `TestEnrichWithTrailer` / `TestGameTitleGrammar`
(#385, #393, #412).

**Stop at the first quota failure (#384).** The daily YouTube quota is **100 `search.list`**
(measured 2026-07-26 through the Service Usage API; quota is default, billing is disabled, cannot be
raised). While the API responds, **all** films are enriched; the first failure from the usageLimits
family (`_is_quota_error` in `youtube.py`: status 429/403 + reason from `error_details`) raises
`YoutubeQuotaExhausted`, and the remaining films **do not access the network at all** — they carry a
third §IV marker, `⚠️ трейлер: дневная квота YouTube` (not a miss or a breakage: a different cause,
a different operator action). The operator sees **one** WARNING line with the number of unserved
films, not a line for each. A non-quota failure (500/timeout) still fails only its own film (#383) —
otherwise one flickering response would suppress trailers for the entire run.

Why not a fixed budget: any precomputed number is a guess, fails on the **second** run of the day
(quota is daily, budget is per run), and understates coverage for one-branch items
(`ru_title == original_title` or an empty original costs 1 request, not 2 — as for games without a
Russian title; localized games have two branches). Only the API knows the actual boundary, so it
names it, and detection costs the requests for one film.

Why not throttle/retry: the limit is counted in requests per day; pauses do not create it. Run
[30143534431](https://github.com/ekolvah/kinozal_scraper/actions/runs/30143534431) requested 340
requests with a limit of 100 — 163 received a guaranteed 429. Pacing would distribute the same 100
more evenly; retry would take quota from the next film. The configured upper limit (4 URLs ×
`limit: 50` = 200 items) also exceeds quota — today only deduplication saves it. Returning trailers
to **all** films requires only changing source (TMDB — token exists, `tmdb_trailer.py` (#329), no
daily limit).

**Selection by `confidence` is deliberately NOT performed — and this is metric-verified.** Low
confidence here does not mean “possibly the wrong film”: `confidence=0.3` means “several equally
good trailers for one film” (dub #1 vs #2, exactly what golden-set accept sets model), and in
production such ties are common but harmless. A confidence threshold cuts hits and does **not** affect
the sole observed class of foreign picks: it arrives as a unique top rank with high confidence.
Selection by `confidence` is orthogonal to the actual error class. The measurements supporting this
(including the set with negative pole `trap`) are [gap-ledger N](coverage-gaps-enrichment.md) and
[testing.md § Eval harness](testing.md#eval-harness--trailer-selection); every selection-logic change
must pass through `scripts/eval_trailers.py` before merge.
**Gemini is NOT in the hot path** — LLM(#142)/embeddings(#143)/
TMDB(#329) remain eval strategies (deliberately outside production: equal Hit at zero runtime cost
vs Gemini quota at 04:00; coverage consequence + open-world caveat —
[gap-ledger N](coverage-gaps-enrichment.md)). Cast is not pulled into the production profile
(RU priority follows title language; no per-item details fetch for a cast tie-break — the ties cast
would break are harmless, #377 — wontfix).

## extract_from_* contracts

- Take in-memory payload (list of dicts for JSON, HTML string for HTML)
- Return `PipelineResult(items, errors, warnings)`
- Zero items extracted → `errors` entry (quality failure)
- Missing `dedupe_key` or `title` on a record → `errors` entry, item skipped
- Never raise for data quality issues — caller decides what to do
- `limit` truncates the payload **before** normalisation: a source's `limit` is the
  top-N it is interested in, not a delivery cap

## Per-source run counters

`SourceMetrics` (on `PipelineResult.metrics`) records
`fetched / extracted / existing / new / sent / stored` for one source, and
`select_new_items(candidates, existing)` produces the `existing`/`new` split so
`extracted == existing + new` always holds.

The counters exist because `new=0` used to be unreadable: it is the normal outcome
of a quiet day (the top-N did not change), and it was indistinguishable from a
source that fetched nothing at all. `existing=10 new=0` says the top-N was examined
and every entry was already known. How the line reaches the operator —
[`operations.md` § Run summary](operations.md#run-summary-reading-the-per-source-metrics-line).

Counters are written as work proceeds and the `PipelineResult` is allocated by the
*caller* of the per-source function, so a failure — including one caught by the
per-source catch-all after delivery already happened — still publishes what it
managed to measure. A red run reporting zeros would be a wrong number, which is
worse than none (§IV).

**Depth is deliberately the top-N, not the whole result set.** Scanning deeper and
treating `limit` as a delivery cap was implemented and reverted: it changes the
product from "the most-starred recent repositories" into "any repository above the
star floor we have not seen yet". Measured 2026-08-05, `created:>=T-30 stars:>1000`
returns `total_count=77`, and positions 60+ sit at ~1000 stars — exactly the
one-day newcomers the source is not for. `github_trending` has the same shape: its
`limit` selects the top of today's trending list, not any 10 unseen rows of ~25.

## HTML source config

HTML sources require `row_selector` in source config (not in `fields`).
Field selectors use `css@attr` syntax to extract attributes.

## Kinozal mirror fallback

Enabled by the `KINOZAL_USERNAME` + `KINOZAL_PASSWORD` secret pair — described in
[`operations.md` § kinozal_pipeline](operations.md#kinozal_pipeline).

**Mirror fallback when `kinozal.tv` is unavailable (#227):** primary is anonymous
`kinozal.tv` (`KINOZAL_URLS` remains `.tv`, **no switch is needed**). If a fetch for any URL fails
(for example, 522), the pipeline automatically retries the same top on the **`kinozal.guru`** mirror
through an authorized session. Login is **lazy** — performed at most once per run and only at the
first fallback, so a healthy `.tv` run does not pay for login or require credentials.

⚠️ **An anonymous domain swap to `.guru` does not work** (verified 2026-06-30): `kinozal.guru`
gates all content behind login — `/top.php`, `/browse.php`, even `/` → `302 .../login.php?m=5`.
Therefore fallback goes through `kinozal_auth.py` (`POST /takelogin.php`; an ordinary non-VIP account
is sufficient — confirmed by a live run).

**Enabling fallback:** set both `KINOZAL_USERNAME` + `KINOZAL_PASSWORD` secrets. Without them (or
when partial), fallback is disabled and a `.tv` failure reaches a visible
`fetch failed ... (mirror fallback disabled)` + exit 1 (§IV). Login failure / both-failed are also
visible: `mirror login failed` / `primary failed (...); mirror ... also failed (...)`.
`sources.json` `base_url` remains `https://kinozal.tv` (the default origin when primary is healthy) —
do not configure the mirror there.

**Links follow the effective origin (#247):** `Kinozal.fetch_listing` returns
`(html, effective_base_url)` — `kinozal.tv` on primary success, `kinozal.guru` on mirror fallback.
The pipeline resolves listing-relative `url`/`image_url` against this base host (a per-fetch override
of static `base_url`), so a mirror run produces **`.guru` links** — live for the logged-in recipient,
not dead `.tv` links. The canonical-origin approach (“`base_url` is always `.tv`”) is deliberately
rejected here: the recipient is logged into `.guru`, so its login wall is irrelevant
(#227, #241, #247). A mixed run (some tops from `.tv`, some from the mirror) gives each item the
correct host; deduplication is stable (key is clean title, host is not included → no migration of
old `.tv` rows in the Sheet is needed).

**Genre-filter details fetch on mirror runs (#317):** because links follow the effective origin, on
mirror days `item.url` = `kinozal.guru/details.php?...`. `Kinozal.fetch_details` for a mirror-host
URL uses the **authorized** session (as listing does), not anonymous primary: `.guru` gates
`details.php` behind login too (see ⚠️ above), so anonymous GET would return a `200` login page
without the `Жанр:` block — a false success that exception-triggered `fetch_listing` failover does
not catch, and the genre filter silently goes blind (`_parse_genre`=="" for all → fail-open → all
are notified). The mirror serves `/i/poster/` anonymously (verified), so `fetch_poster` is not
affected by this path.

The sole consumer is production cron (`run-script.yml` / `kinozal_pipeline.py`). E2E
`tests/test_e2e_kinozal_titles.py` is unconditionally skipped while `kinozal.tv` returns 522 (#136).

## Macro expansion

Handled by `pipeline_config.py` before the pipeline runs.
Supported macros: `{{TODAY}}`, `{{DATE_MINUS_7_DAYS}}`, `{{GH_TOP_LIMIT}}`, `{{GH_TRENDING_LIMIT}}`, `{{STEAM_TOP_LIMIT}}`.
