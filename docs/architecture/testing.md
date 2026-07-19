# Testing philosophy

> **Question this document answers:** How do we plan to guarantee product quality — the
> levels, the taxonomy, what we mock, and which coverage gaps we consciously accept.
>
> Navigation «which tests touch module X» is `grep` by module name, not a hand-curated
> table. The one thing grep can't answer — *why we deliberately don't test Y* — is the
> [Consciously-accepted coverage gaps](#consciously-accepted-coverage-gaps) ledger below.

## Rule: no mocks of internal functions

> **Canon:** the binding statement is [principles.md §II](principles.md) (Protocol
> Boundaries with Dependency Injection). This section is the project-specific
> elaboration: which boundaries count as external here, and the concrete pattern to follow.

In this repo the external boundaries are Sheets, Telegram, YouTube and HTTP — substitute a
Fake (`InMemoryStorage`, `InMemoryNotifier`) or a saved HTML/JSON fixture. Everything else
(`_extract_kinozal_items`, `run_kinozal_pipeline`, …) is internal and is never mocked (§II).

**Correct pattern (as in `test_github_popular_pipeline.py`):**
Call `run_*_pipeline()` directly. Pass `InMemoryStorage` and `InMemoryNotifier`.
Assert on doubles' state after the call.

## Test levels

**Integration-first (primary level):**
- Call production pipeline with saved HTML/JSON fixtures and Protocol doubles.
- Fixtures: saved HTML dumps from kinozal.tv, JSON responses from GitHub/Steam.
  Update dumps manually when site structure changes.
- Covers full business logic without flakiness (no network, no rate limits).
- When: on every PR, in CI.

**Unit (pure functions):**
- Isolated test of a single pure function.
- Fixes the function contract (not "catches bug X", but guarantees given
  this input always this output).
- When: for transformation logic (parsing, formatting, normalization).

**E2E smoke (real HTTP / real Telegram):**
- Minimal run against the real site and real API.
- Verifies the external resource hasn't changed structure or blocked us.
- When: before PR merge (PRs in this project are infrequent); the production
  script already runs daily on schedule and acts as an E2E smoke test itself.
- Failure blocks merge (site structure broken → update fixture/parser).

## Bug taxonomy

| ID | Category | Examples |
|---|---|---|
| A | Structure drift | kinozal changes CSS selector; GitHub changes response key |
| B | Network failures | timeout; 5xx; unavailable; gzipped body |
| C | Auth & quota | Sheets 401/429; YouTube quota; Gemini quota; Telegram 401 |
| D | Config errors | bad CSS selector; macro not expanded; limit ≤ 0 |
| E | Data integrity | dedupe_key drift → duplicates; write-vs-notify race |
| F | Message rendering | size >4096; HTML escape; broken image → fallback |
| G | Trailer enrichment | YouTube no-result; year mismatch; quota exhausted |
| H | Pipeline orchestration | partial failure isolation; write-before-notify order |
| I | URL resolution | relative→absolute; base_url drift; broken url field |
| J | Concurrent state | rerun after crash; partially written rows |

## Bug → Test type mapping

Choose the cheapest reliable test for each category.

| Category | Integration + fixtures | Unit | E2E smoke |
|---|---|---|---|
| A. Structure drift | ✅ update fixture | ❌ | ✅ before PR merge |
| B. Network failures | ✅ raise in fake HTTP | ❌ | ⚠ |
| C. Auth & quota | ✅ fake raises exception | ❌ | ❌ no credentials in CI |
| D. Config errors | ❌ | ✅ pure validation | ❌ |
| E. Data integrity | ✅ InMemoryStorage state | ❌ | ❌ |
| F. Message rendering | ✅ InMemoryNotifier | ✅ pure format | ⚠ test-channel |
| G. Trailer | ✅ _FakeYoutube | ❌ | ❌ |
| H. Orchestration | ✅ Protocol doubles | ❌ | ❌ |
| I. URL resolution | ✅ | ✅ pure | ❌ |
| J. Concurrent state | ✅ InMemory with state | ❌ | ❌ |

## What gets tested

- All pure transformation logic: macro expansion, field mapping, normalization,
  row construction, deduplication key lookups, schema validation.
- Protocol contract: `InMemoryStorage` tests verify the `Storage` interface.

## Eval harness — trailer selection (#139)

`scripts/eval_trailers.py` measures trailer-pick quality against a **frozen golden-set**
(`tests/fixtures/trailer_golden.json`): each film carries a hand-annotated `correct` — a
single video_id, an **accept-set** (`list[str]` of equally-valid RU dubs, since a real film
often has several), or `null` (no trailer exists) — plus a recorded `candidates` snapshot. The
harness replays candidates through a `TrailerStrategy` (`trailer_strategy.py`) offline — no
network/quota — classifies each film Hit (pick ∈ accept-set) / Wrong / Miss **relative to
`correct`**, and scores it (Hit +1 / Miss 0 / Wrong −2: a wrong film's trailer is worse than an
honest §IV "not found" marker). The set mixes synthetic seed cases with ≥10 **real** retrieved
pools (dirty candidates + honest per-id-justified accept-sets, `note`-annotated) so the metric is
grounded in reality, not self-fulfilling (#327).

- **`correct` vs `candidates` are separate on purpose.** `correct` is durable ground truth
  (retrieval-independent); `candidates` is a regenerable snapshot. This lets the harness
  *attribute* a miss to retrieval (correct id not even in the pool → #140) vs selection (in
  the pool but not picked → #141) — the measurement, not a declaration.
- **Fixtures are frozen.** `--record` (dev-only, live, needs `API_KEY`; fail-fast without it)
  reseeds the `candidates` snapshot — for initial seeding / a *conscious* refresh, not a
  routine run: re-recording can silently drift a hand-annotated `correct` out of a new YouTube
  result set (Hit → retrieval-miss). The loader is fail-loud (§IV/§VI): a broken entry (empty
  set, missing field, duplicate `video_id`, `correct` of a wrong type, empty accept-set, or an
  accept-set id absent from **both** the candidate pool and the TMDB snapshot) raises
  `GoldenSetError`, never degrades to a silent Miss. (Legacy single-`str` `correct` may still point
  outside the pool — the miss-branch idiom "ideal id not retrieved → Miss" — so the cross-check
  applies to accept-sets only.) The harness is deliberately **not** in `ci_check` — no green
  strategy exists yet to gate; the known-gap guard below carries the RED signal instead.

- **TMDB dual-source measure (#329).** Beside the `TrailerStrategy` (YouTube-retrieval) column the
  harness prints a second scorecard: `evaluate_tmdb` replays a frozen per-film `tmdb_videos`
  snapshot through the pure `pick_trailer` (`tmdb_trailer.py`) — TMDB `/movie/{id}/videos` gives
  `iso_639_1`/`type`/`official`/`site` directly, so language+officialness are metadata, not a
  YouTube-title heuristic. Same accept-set, so the columns compare side-by-side. `--record-tmdb`
  (dev-only, live, needs `TMDB_TOKEN`) reseeds snapshots for the **real** cases only (accept-set /
  `correct: list` form); synthetic HeuristicStrategy logic fixtures (`str`/`null` `correct`,
  placeholder ids a real YouTube id can't hit) are blanked → out of TMDB scope, and `evaluate_tmdb`
  skips empty-snapshot cases. A real "TMDB found nothing" is a **non-empty** snapshot with no
  eligible Trailer/Teaser → `pick_trailer`→None→Miss (distinct from out-of-scope).
  - **Honest accept-set expansion (B1, #329).** The #327 accept-sets are YouTube-retrieval-derived,
    so TMDB's *valid* RU dubs (different video_id, same film) scored Wrong against them. Fix:
    per-id **content-verified** additions (the video name identifies the correct film + RU dub),
    hard-coded — never "trust TMDB output wholesale". The non-circular control is TMDB measured
    against the **pre-expansion** #327 set (a conservative floor); expansion is only for ground-truth
    completeness, symmetric — the set holds both the YouTube-surfaced and TMDB-surfaced valid dubs,
    so neither source is unfairly penalised.

## What does NOT get tested in this repo

- `SheetsStorage` gspread wiring — call order, worksheet creation.
  (Its **retry on transient errors (429 + 5xx)** and **schema validation** *are* tested — see
  `test_sheets_storage.py::TestSheetsStorageRetryTransient` / `TestSchemaValidation` — because
  those are correctness logic mocked at the `gspread.Client` boundary, not internal call order.)
- `http_fetch` live curl_cffi transport — real network / TLS handshake.
  (Its **retry on transient HTTP responses (403 anti-bot + 429 + 5xx)** *is* tested — see
  `test_http_fetch.py::TestFetchRetry`, incl. a reality-anchor over a real curl_cffi
  `HTTPError` — because that is correctness logic mocked at the `requests.get` boundary,
  the HTTP-transport sibling of the `SheetsStorage` retry above (#306).)
- `TelegramChannelSummarizer` / Telethon calls.
- Any code path that requires live credentials.

> **Scope-skip vs cost-skip.** The list above is a *scope* skip — those paths can't run
> without live credentials. The rule below is a *cost* skip — the code is perfectly
> testable, but a test wouldn't pay for itself.

## Rule: when a test is NOT worth writing

Not every regression deserves a test. Decide by what the regression actually breaks:

- **Correctness or safety regression → write the test.** A wrong row, a dropped item, a
  leaked secret, a broken import — the test guards a real failure mode (e.g.
  `test_repo_layout` guards import correctness, `test_settings_deny` guards a security
  invariant).
- **Resource-only regression (CI minutes, tokens) → no guard test; use a forcing-function
  instead** (a doc note, a deny-list, a config gate). A test here costs maintenance plus CI
  time to guard something that, if it regresses, only ever wastes CI time — net negative
  (goal-function priority (2), [mindset.md](../../.claude/rules/mindset.md)).

**Precedent (#207):** a duplicate CI run (one `quality` job fired by both `pull_request`
and a `push: issue-*` event for the same commit) wasted CI minutes. The fix was a one-line
trigger removal; a guard test asserting "no duplicate trigger" was added, then removed as
work-for-work — the regression it guarded cost only CI minutes, not correctness. The
forcing-function lives in [ci.md](ci.md) ("do not re-add `issue-*` to push") instead.

## Rule: reading mutation-test output

Mutation testing (a *survived* mutant = behaviour no test guards) is the only systematic way to
catch a test that passed RED→GREEN but later rotted into a for-show test. It is a **one-shot
diagnostic, never a per-PR CI gate** — a survival-% gate breeds for-show tests (the exact failure
mode it's meant to find) and burns CI minutes (priority (2)). When you do run it:

- **Filter equivalent mutants before triaging.** PEP-604 union-type annotations (`X | None`,
  `str | Path`) are real expressions whose result is only `__annotations__` metadata — never
  checked at runtime — so every `|`-operator mutant on them *survives* without being a gap. They
  typically dominate the raw survivor count, making the raw survival-% misleading. Triage the
  operator, not the count.
- **Pin the test-command to the deterministic offline subset** (`--ignore-glob=tests/test_e2e_*.py`):
  e2e-smoke / credential-gated tests flake → uninterpretable survivors.
- **Tooling:** `mutmut` refuses on Windows (wants WSL); `cosmic-ray` runs natively. Run it from an
  ephemeral venv (no `requirements*.in` edit — one-shot, not infra). Set `PYTHONUTF8=1` or
  cosmic-ray crashes decoding non-ASCII (cp1252) test output.

## Rule: test behaviour, not implementation

Test through the public entry point (`run_*_pipeline()`) and assert on observable **state**,
never on which internal methods were called in which order. A test that mirrors the
implementation is a *change-detector*: it breaks on every refactor without catching a bug —
**negative value**. The aim is an *unchanging* test that fails only when behaviour actually
changes. This is the positive framing of [§II no-internal-mocks](principles.md): mocking an
internal function is the most common way a test ends up asserting interaction instead of
state.

### Change type → test response

| Change | Test response |
|---|---|
| Pure refactor (behaviour identical) | Tests unchanged — if they break, they were change-detectors |
| New feature | Add new tests only; existing tests stay green |
| Bug fix | Add a case reproducing the bug, then fix |
| Behaviour change | Change the tests deliberately (this is the signal, not noise) |

The "behaviour change needs a test" half is canon in [principles.md §I](principles.md)
(Test-First) — see its exceptions for what legitimately skips a test (rename/move,
docs-only, one-line non-behavioural). This table is the refactor-vs-feature companion to §I,
not a restatement of it.

## Test runner

```bash
python -m pytest          # via pyproject.toml config
python scripts/ci_check.py  # full CI mirror: format + lint + tests + mypy
```

## Consciously-accepted coverage gaps

Every bug category in the [taxonomy](#bug-taxonomy) is covered by tests today (navigate to
them with `grep` by module/feature name — there is no hand-curated per-category index, it
only drifts). What `grep` *can't* tell you is where we **deliberately don't test** and why —
that ledger lives here so a rejected-as-negative-ROI decision isn't silently re-opened as
work-for-work (goal-function priority (2)).

**Rejected as negative-ROI (a test would only ever guard CI minutes, not correctness):**

- **A. Structure drift — no *live* E2E for GitHub `new_popular` / Steam JSON.** Integration
  tests cover parsing with saved fixtures; the daily cron is the E2E smoke (zero-row drift →
  red CI next run). A dedicated live-E2E was rejected per the «cron = E2E smoke» doctrine
  ([Test levels](#test-levels)). Live E2E *does* exist where structure drift is silent and
  frequent: `test_e2e_kinozal_titles.py`, `test_e2e_github_trending.py`.
- **C. Auth & quota — GitHub 401 not tested.** The token rarely 401s and a downstream
  zero-row → red CI catches the outage; a dedicated 401 guard is negative-ROI.
- **K. Sheets 5xx retry — dup-write / already-exists races not tested (#288).** Broadening
  `SheetsStorage` retry from 429-only to transient 5xx (500/502/503/504) raised the odds of a
  `append_rows` **duplicate row** on retry: a 5xx that lands *after* the batch partially wrote
  re-appends on the next attempt (429 usually rejects *before* writing, so this is genuinely
  newer/likelier than the prior behaviour). **Accepted** — next-run read-dedup (`get_existing_keys`)
  drops the dup; a test would need live/ambiguous-timing conditions to reproduce (§V documented
  mitigation, not silent). Same class on `add_worksheet` (5xx after server-side create → retry
  hits a non-transient "already exists" 4xx → aborts, *doesn't* self-heal) — rarer still (once
  per tab, ever) and left untested for the same reason. Behaviour is correct; only the timing
  race is uncovered, recorded here so it isn't re-litigated as work-for-work.
- **L. `fetch_bytes` image-`Accept` header / impersonate-profile merge — verified live only (#296).**
  The fix makes `fetch_bytes` send an `<img>`-style `Accept: image/*` so content-negotiating hosts
  (imageban.ru, fastpic) serve the JPEG instead of an HTML landing page. The unit test
  (`test_sends_image_accept_header`) asserts the header is *passed* to `requests.get`, but **cannot**
  observe curl_cffi's real behaviour: that `headers=` merges by key over the impersonate profile
  (so UA / Sec-Ch-Ua / TLS fingerprint — the #217/#225 403-avoidance — survive) and that the target
  host actually returns image bytes. Both were verified live against imageban/fastpic + a
  header-echo endpoint; the standing gate is the daily cron E2E (a fingerprint regression → 403 on
  posters → §IV-visible next run), same «cron = E2E smoke» doctrine as **A**. Recorded so the
  live-only verification isn't re-opened as a mock-the-network work-for-work test.
- **M. `http_fetch` retry deliberately scoped to HTTP-status errors only (#306).** The retry layer
  (`_retry_transient_http`) fires on transient HTTP *responses* (403/429/5xx) but **not** on network
  errors (`Timeout` / `ConnectionError` — curl_cffi `RequestException` subclasses that never reach
  `raise_for_status`, so the `isinstance(HTTPError)` predicate skips them by construction). **Accepted**
  — no reproduced incident (§V: don't retry what wasn't observed), symmetric with the `SheetsStorage`
  sibling which covers `APIError` status only. The asymmetry «503 retries, a DNS blip crashes the source»
  is real and conscious; a broadening waits for an actual network-error incident. Separately, the raw
  `requests.get` calls in `github_popular_pipeline.py` (GitHub API) and `steam_pipeline.py` still have
  **no** retry — a different transport (stdlib `requests`, not curl_cffi via `http_fetch`) — deferred to
  a follow-up issue so `_retry_transient_http` can be reused there. Recorded so neither is re-opened as
  work-for-work.

- **N. LLM / embedding / TMDB trailer-picker strategies built but deliberately NOT in the prod
  hot path (#144/#315).** Прод `enrich_with_trailer` отбирает детерминированным language-aware
  `HeuristicStrategy` (#141) — RU-предпочтение (#315) закрыто и измерено на golden-set
  (`eval_trailers.py`: hit=26, **wrong=0**, n=28). `LLMTrailerStrategy` (#142),
  `EmbeddingTrailerStrategy` (#143) и `tmdb_trailer.pick_trailer` (#329) остаются eval-only:
  вплетение любой в cron 04:00 добавляет Gemini-вызов на item ради **нулевого измеримого
  Hit-выигрыша** (heuristic уже wrong=0; 2 промаха — retrieval-дыры, A/B их не чинят) — ровно та
  квота-дыра, которую #144 закрывал. Их чистые selection-слои **покрыты** unit-тестами; без покрытия
  только живые Gemini-движки (строки ниже). Записано, чтобы «почему LLM-picker не в проде?» не
  переоткрывали. **Open-world caveat:** wrong=0 доказан на 28 curated-кейсах; success-path breadcrumb
  (`reason`/`confidence` INFO-лог в `enrich_with_trailer`) вскроет прод-ambiguity — пересмотреть,
  если в проде всплывут ничьи, которых нет в golden-set.

**Scope-skip (can't run without live credentials) — see [What does NOT get tested](#what-does-not-get-tested-in-this-repo):**

- **J. Concurrent state — true *parallel* execution is a non-target** (serial daily cron, no
  overlap → a crash/concurrency simulation would be work-for-work). Realistic failure modes
  *are* covered: rerun-after-crash idempotency (dedupe index re-read) and notify-then-store
  ordering (a failed-notify item isn't stored → retried next run, no silent loss).
  Cell-level partial `gspread` writes are scope-skip (live credentials).

### Modules without dedicated tests

| Module | Reason | Mitigation |
|---|---|---|
| `youtube.py::Youtube` (live-client wrapper: `__init__` + `search_candidates` method) | Requires live YouTube API (`build()` + `API_KEY`) | Pure retrieval `search_candidates(client, profile)`/`_search_one` **is** directly tested (`test_youtube.py::TestSearchCandidates` via an injected fake `client`, the DI boundary, #140); only the thin live-`build()` wrapper is untested. `get_trailer_url`/`_search_youtube` удалены в #144 (прод перешёл на `search_candidates` + `HeuristicStrategy`) |
| `tmdb_trailer.py::TmdbClient` (`resolve`/`_get`/`_find_movie_id`) | Requires live `TMDB_TOKEN` + network — retrieval boundary (DI, mirror of `youtube.py`) | Pure selection `pick_trailer` **is** directly tested (`test_tmdb_trailer.py`, 7 cases); only the network boundary is untested, same §II precedent as `youtube.py`'s live-client wrapper (#329) |
| `text_utils.py` | Small utility | Indirect coverage via `test_kinozal_pipeline.py::TestTitleYearMatches` |
| `*_pipeline.py` `if __name__ == "__main__"` blocks | CLI wiring of live `gspread`/env — needs live credentials | **Scope-skip**, guarded two ways since the package migration ([#237](https://github.com/ekolvah/kinozal_scraper/issues/237)): (1) **mypy is load-bearing** — `pip install -e .` + native package resolution means mypy type-checks the `__main__` block (incl. its `from kinozal_scraper.X import …`), catching a mis-wired/mis-renamed import that the import-only `test_package_importable.py` cannot; (2) the daily cron as §IV «cron = E2E smoke». The large uncovered blocks in `coverage.py` are these runners, not logic gaps |
| Package import-resolution & repo layout | A module failing to resolve as `kinozal_scraper.X`, or source drifting back to a flat `src/*.py` layout | `test_package_importable.py::TestPackage` (all modules import as `kinozal_scraper.X`); `test_repo_layout.py::TestLayout`. (The #237 B1 empty-/nested-scan guard moved off the retired `test_check_headers.py` — [#253](https://github.com/ekolvah/kinozal_scraper/issues/253) replaced `check_headers.py` with ruff `D100`/`D104`/`D419`; the "mis-pointed/empty `src/` scanned nothing" failure mode is now subsumed by these two guards, which fire strictly harder — 17 hard-coded imports + layout-drift — than the old zero-file check) |
| `crypto.py` (`save_/load_encrypter_session`) | File-IO glue around the **tested** pure helpers `encrypt_bytes`/`decrypt_bytes` | **Cost-skip**: mocking the filesystem to guard trivial glue is negative-ROI; failure is loud (`KeyError`/`InvalidToken` crashes cron start immediately, §IV-visible) |
