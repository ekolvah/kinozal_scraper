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
  applies to accept-sets only.)

- **Delivery scorecard + baseline ratchet (#379).** Beside the `pick` column the harness prints a
  **delivery** scorecard: `evaluate_delivery` replays the golden-set through the production
  `kinozal_pipeline.select_trailer` (retrieval stub frozen at `case.candidates`) and parses the
  reply back into a `video_id` — a §IV miss-marker → `None`, an **error**-marker → `GoldenSetError`
  (a broken harness must never look like "the strategy found nothing"). Markers are *imported*, not
  re-spelled, so a reword in prod can't silently turn a miss into an unparsable reply.
  - **Why a second column at all.** #359 changed `enrich_with_trailer` — the layer *above*
    `HeuristicStrategy.pick` — so the pick scorecard was identical before and after a 26→16 score
    regression. Measuring `pick` alone is blind to exactly the class of change that motivated the
    gate. Today both columns agree by construction (no policy sits between them); the point is that
    they *can* diverge.
  - **The baseline is the gate.** `tests/fixtures/trailer_baseline.json` pins the **delivery**
    outcome per case (`{"i", "film", "outcome"}` — the index rides along because `ru_title` is not
    unique: "Гладиатор 2" appears twice, and swapping two same-named cases would slip past a
    name-only check). `compare_to_baseline` is a pure function; the red comes from
    `tests/test_eval_baseline.py::TestBaselineGate::test_committed_baseline_matches_main`.
    **This reverses the earlier "deliberately not in `ci_check`" stance** — that decision waited for
    a strategy good enough to pin an absolute threshold, which never arrived. A ratchet needs no
    such thing: it pins the *current fact*, so any movement becomes a reviewable diff line.
  - **Any divergence is red, improvements included.** "Green with a warning" would rebuild the very
    defect being fixed — a signal nobody is obliged to read (§IV). Worse, once wrong-cases land
    (#380) a net-positive delta could hide a `hit→wrong` swap; per-case comparison cannot.
    `--update-baseline` regenerates the fixture, so a deliberate improvement lands in the PR diff.
  - **Proof, not reasoning.** `TestBaselineGate::test_reverted_359_policy_fails_the_gate` runs the
    reverted #359 policy (suppress `confidence < 0.5`) through the same two functions the real gate
    uses and asserts the verdict is red with the moved films named — reproducing 26→16 exactly. The
    counterfactual policy lives in the test file, never in `src`.
  - **Where the gate stops.** It covers `select_trailer`. Profile derivation from the kinozal title
    (clean-title / `original_title` / year-regex / the game branch — where #385 and #393 lived) is
    outside the measurement and rests on `TestEnrichWithTrailer` unit tests. This limit is
    load-bearing: a change written in that blind spot passes green.

- **Отрицательный полюс метрики: разметка `trap` (#380).** Шкала `Hit +1 / Miss 0 / Wrong −2`
  объявляла, что чужой трейлер вдвое хуже честного маркера, но на наборе #327 `wrong` не
  встречался **ни разу**: все кейсы строились как «правильный ответ существует, найди его».
  Половина шкалы была мертва — набор мог только наказать за осторожность (#359: −10 hit), а
  «сколько wrong предотвращено» показать был не в состоянии.
  - **Что добавлено.** Три кейса с живьём записанными пулами и полем `trap` — id кандидатов, про
    которых **верифицировано** (через `videos.list` → канал + описание, основание в `note`), что
    это *другая работа*: фанатский Minecraft-продакшен под названием сериала, хоррор-фильм-тёзка,
    сериал `The Rookie` под названием фильма `The Amateur`. Один из них стратегия сегодня и
    выбирает → скоркарта перестала быть `wrong=0`.
  - **Почему отдельное поле, а не только accept-set.** `correct` отвечает «этот id — правильный»;
    он не умеет отличить «чужая работа» от «валидный дубляж той же работы, который мы не
    дозаписали». `trap` — ground truth про **пул**, а не про исход, поэтому переживает улучшение
    стратегии. Загрузчик fail-loud наравне с остальным набором: не-список / не-str / id вне пула
    **кандидатов** (не union'а с TMDB — ловушка осмысленна только среди того, что стратегия
    ранжирует) / пересечение с accept-set → `GoldenSetError`. Опечатка в id иначе тихо разоружила
    бы разметку: кейс выглядел бы размеченным, не будучи им.
  - **Гейт — инвариант фикстуры, а не утверждение о стратегии.**
    `TestWrongPole::test_golden_set_keeps_verified_traps` требует ≥3 кейсов с непустым `trap`.
    Проверять «ловушка всё ещё выбираема стратегией» было бы соблазнительно и **неверно**: такой
    гейт краснел бы ровно на том изменении, ради вознаграждения которого набор и правился, требуя
    от контрибьютора собрать новый живой кейс в самый неудачный момент — предсказуемый исход тут
    не «набор стал лучше», а «в `trap` дописали наугад, чтобы позеленело». Наблюдение «полюс стал
    слишком лёгким» приходит из диффа baseline (`wrong→hit`) и заводится как issue, а не как
    красный CI у того, кто починил прод.
  - **Что полюс сразу показал.** Откаченная политика #359 (давить `confidence < 0.5`), прогнанная
    по обновлённому набору, даёт 26 → 14 и **не трогает `wrong` вообще** (как был 1, так и
    остался): реальный чужой pick идёт с `confidence=0.9`. То есть порог по уверенности
    ортогонален наблюдаемому классу ошибок — вывод, который на наборе без полюса был непроверяем
    (канон — [pipeline.md](pipeline.md#trailer-retrieval-and-selection-140-141-144)).
  - **Дрейф пулов — не теория.** Повторная запись пула «Крайних мер» через час уже не вернула
    пришпиленный `trap`-id. Поэтому `_record` перевалидирует свежий payload **до** `write_text`:
    иначе файл сохранился бы, а упала бы следующая *загрузка* — у всех, кто просто запустил
    `pytest`, и без намёка на причину. И поэтому же новые кейсы записывались через
    `--record --golden <scratch>.json` на однокейсовом файле, а не переписыванием фикстуры целиком.
  - **Вне TMDB-колонки (сознательно).** У новых кейсов `tmdb_videos: []` — единственный способ
    записать снимок сейчас — `--record-tmdb` по всем 28, то есть та самая разморозка, от которой
    фикстуры и защищены; `evaluate_tmdb` их пропускает.

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

## Eval harness — summarizer faithfulness (#347)

`scripts/eval_summarizer.py` measures `summary_ru` **meaning** (not just the `response_pattern`
regex, which only checks the two-line *format*) against a **frozen golden-set**
(`tests/fixtures/summary_golden.json`: GitHub-project input + a recorded summary-under-eval +
`note`, ≥1 deliberately **unfaithful** case as an audible anchor). It builds RAGAS inputs
(`contexts` = title+description+language the model actually saw; `answer` = the summary;
`question` = the fixed «для кого/зачем» intent) and runs RAGAS `faithfulness` (did the summary
invent facts absent from the source?) + `answer_relevancy`. `--threshold` gates on mean
faithfulness (baseline first, tighten later — same *metric-before-optimization* discipline as the
trailer harness). The loader is fail-loud (§IV/§VI): non-list / empty / missing `input.*` / empty
`summary` → `GoldenSetError`, never a silent skip.

- **Unlike the trailer harness, the metric itself is an LLM.** RAGAS computes faithfulness /
  relevancy via an LLM-as-judge, so a *routine* run is inherently live/API-gated (like the
  trailer `--record`, not its offline scorecard). The live judge is isolated in the single seam
  `_evaluate_dataset` (the only mocked boundary); the pure logic — `build_ragas_inputs`,
  `normalize_ragas_output` (the fragile version-drift key mapping, kept out of the mock on
  purpose), `scorecard` — is unit-tested directly (`tests/test_eval_summarizer.py`). CI never
  calls the live judge; the baseline is produced by a dev run with the judge wired (see gap **Q**).
- **RAGAS is a dev-only dependency** (`requirements-dev.in`, lazy-imported): prod never pays its
  import or footprint. Landing it forced two adjacent, deliberately-scoped costs, both recorded in
  the code: prod `websockets` capped `<16` (a google-genai transitive not used in prod — we call
  only `generate_content`/`list`/`embed`, never the Live API — realigned so the shared-dep gate
  matches the ragas tree without bumping prod up), and three low-severity, unreachable-in-our-usage
  CVEs suppressed in `ci_check.check_pip_audit_dev` with an inline justification + a tracked
  follow-up to un-suppress once upstream ships compatible fixes.

## What does NOT get tested in this repo

- `SheetsStorage` gspread wiring — call order, worksheet creation.
  (Its **retry on transient errors (429 + 5xx)** and **schema validation** *are* tested — see
  `test_sheets_storage.py::TestSheetsStorageRetryTransient` / `TestSchemaValidation` — because
  those are correctness logic mocked at the `gspread.Client` boundary, not internal call order.)
- `http_fetch` live curl_cffi transport — real network / TLS handshake.
  (Its **retry on transient HTTP responses (403 anti-bot + 429 + 5xx)** *is* tested — see
  `test_http_fetch.py::TestFetchRetry`, incl. a reality-anchor over a real curl_cffi
  `HTTPError` — because that is correctness logic mocked at the `requests.get` boundary,
  the HTTP-transport sibling of the `SheetsStorage` retry above (#306). Its **block
  diagnostics** (`describe_block`, #358) are tested too — `TestBlockDiagnostics`, pure
  formatter + a real captured Cloudflare block page; what *can't* be tested is which
  block a given egress IP earns — gap **S**.)
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
  hot path (#144/#315).** Прод `enrich_with_trailer` отбирает детерминированным `HeuristicStrategy`
  (#141); `LLMTrailerStrategy` (#142), `EmbeddingTrailerStrategy` (#143) и `tmdb_trailer.pick_trailer`
  (#329) остаются eval-only. **Обоснование выбора (negative-ROI, wrong=0 на golden-set) — канон в
  [pipeline.md § Trailer retrieval and selection](pipeline.md#trailer-retrieval-and-selection-140-141-144)**,
  здесь не дублируем. Coverage-следствие (дом здесь): чистые selection-слои этих стратегий **покрыты**
  unit-тестами; без покрытия только живые Gemini-движки (строки ниже). Записано, чтобы «почему
  LLM-picker не в проде?» не переоткрывали. **Open-world caveat:** wrong=0 доказан на 28
  curated-кейсах; success-path breadcrumb (`reason`/`confidence` INFO-лог в `enrich_with_trailer`)
  вскроет прод-ambiguity — пересмотреть, если в проде всплывут ничьи, которых нет в golden-set.
  **Ревизит состоялся (#359, 2026-07-24) и дал обратный результат — записано, чтобы вывод не
  переоткрывали.** Breadcrumb сработал: в run `30066249488` 5 из 6 picks оказались
  `ambiguous (conf=0.3)`. Гипотеза «ничья → произвольный выбор → чужая ссылка» была реализована
  (подавление picks с `confidence < 0.5` в miss-маркер) и **откачена по замеру**: на 28 golden-
  кейсах 26 hit → 16, 2 miss → 12, wrong 0 → 0. Все 10 подавленных picks были **попаданиями** —
  `confidence=0.3` означает «несколько одинаково хороших трейлеров одного фильма» (дубляж №1 vs
  №2), ровно то, что и моделируют accept-set'ы. Прод-ничьи частые, но безвредные; #377 (каст как
  разрыватель ничьих) закрыт как wontfix. Golden-запись по «Суете» не добавлена: верифицируемо-
  неверного кандидата в захваченном пуле нет (все 5 — трейлеры того же сериала), а догадка в
  эталоне отравила бы eval. #359 в итоге сузился до диагностики: `video_id` в breadcrumb.
  **Остаточный пробел закрыт (#380, 2026-07-27):** в наборе появились кейсы с верифицированным
  чужим кандидатом (`trap`, блок «Отрицательный полюс метрики» выше), `wrong` больше не 0. Замер
  #359 по обновлённому набору вывод не изменил, а усилил: политика не трогает `wrong` вовсе
  (реальный чужой pick идёт с `confidence=0.9`), то есть порог по уверенности ортогонален
  наблюдаемому классу ошибок. **Что осталось открытым:** wrong-кейсов найдено 3 на ~150
  проверенных живых пиков — класс редкий (~1%), и набор его представляет тонко; пополнять из
  реальных инцидентов (прод-лог несёт `video_id` → `videos.list` → верификация вручную).

- **O. Request-side Gemini API-contract drift — caught by runtime visibility, not a unit test (#340).** When Google changes what the API accepts (e.g. 3.x models reject `thinking_budget=0`, #338), a unit test with a `_FakeClient` **cannot** catch it: the fake encodes our assumption about the request contract and can only confirm it. A live-E2E against real Gemini is a scope-skip (credentials/flake/quota). So the standing safety net is **runtime visibility, not a test**: a `400 INVALID_ARGUMENT` is classified as `ModelConfigRejected` → ERROR log + operator Telegram alert + red job (`config_rejected_models`), instead of a silent `TryNextModel` that green rotation hides. The one unit-testable guard is a **contract test on a real `google.genai.errors.ClientError`** (`test_real_client_error_invalid_argument_routes_to_config_rejected`) — it fails loudly if our `.status` detection drifts from the SDK's actual error shape (which would otherwise ship the whole fix as a green-tested no-op). Recorded so the live-E2E isn't re-opened as work-for-work.
- **P. Prompt-injection resistance of the *real model* — offline structural tests only, no live eval (#308).**
  `test_prompt_injection.py` proves our **defenses** deterministically with a `_FakeClient`: untrusted
  `$title`/`$description` are fenced, fence-sentinel breakout attempts are stripped, hijacked *output*
  is caught by `response_pattern` → marker and HTML-escaped at render. What a `_FakeClient` **cannot**
  show is whether the live Gemini actually obeys the fence under a novel jailbreak — that needs a
  live promptfoo/RAGAS red-team run against real quota (credentials/flake/cost), a negative-ROI
  scope-skip here. Justified by the honest blast radius (`docs/architecture/llm-security.md`): no
  tool-calling / exfiltration → a bypassed fence yields only cosmetic wrong text, itself HTML-escaped.
  A **semantic output-guard for the free-form Steam source** (`steam_charts_mostplayed`, no
  `response_pattern`) is likewise out — adding one changes prod behaviour, tracked as a separate unit.
  Recorded so neither is re-opened as work-for-work.
- **Q. RAGAS summarizer eval — the live LLM-judge runs dev-only, never in CI (#347).**
  `scripts/eval_summarizer.py` scores `summary_ru` faithfulness/answer_relevancy with RAGAS, whose
  metric *is* an LLM-as-judge (+ embeddings). CI unit-tests the pure seams and mocks the single
  `_evaluate_dataset` boundary, so the **baseline number** (mean faithfulness over the golden-set) is
  produced by a **dev run with the judge wired**, not by CI — no API key/quota/cost in the pipeline,
  same class as the trailer `--record`. Accepted, not silent: the harness prints the score and
  `--threshold` gates it; the [harness section](#eval-harness--summarizer-faithfulness-347) documents
  the seam split. Recorded so «why isn't the RAGAS score in CI?» isn't re-opened as a mock-the-judge
  work-for-work test.
- **R. Dedupe-key edges where the year anchor can't disambiguate (#363).** The kinozal dedupe key is
  the `RU / Original / Year` prefix of the raw title, located by scanning for the first bare-year
  segment (`text_utils.YEAR_SEGMENT_RE`) and dropping the format tail. Two edges are **consciously
  accepted**, characterized but not "fixed": (1) a film whose **RU title IS a bare year** (`2012`,
  `1917` → raw `2012 / 2012 / 2009 / …`) keys on that first-segment year → `"2012"` — a no-op vs. the
  pre-#363 first-segment behaviour, same class as `original_title`'s numeric-original edge (#138 Out of
  scope), pinned by `test_year_titled_film_collapses_to_year`. (2) a **yearless** raw title (no bare-year
  segment at all) falls back to the clean first segment, so yearless namesakes could still collapse — but
  a top-page title without a year is anomalous, and any collapse is already visible in aggregate via the
  `_dedup_and_log_coverage` INFO line (`N extracted (M after dedup-collapse)`), so it isn't a silent §IV
  loss. Both accepted because the disambiguating signal (a distinct year) is genuinely absent; recorded so
  a year-titled namesake collision isn't re-opened as a regression.
- **S. Anti-bot 403: root cause is the *egress IP*, and it can only be observed in prod (#358).**
  Замер 2026-07-25 тем же `curl_cffi==0.15.0`, что и прод: локально (residential IP)
  `impersonate="chrome"` → 200, **без** impersonate → 403, `chrome124`/`chrome131` → те же 200;
  в CI (датацентровый IP GitHub Actions) → 403 семь суток подряд (18–24.07), при этом 14.07 и 17.07
  тот же CI получал 200. В теле блока **нет** `cdn-cgi/challenge-platform` и turnstile. Выводы,
  записанные чтобы их не переоткрывали: (1) TLS-фингерпринт (#217) **исправен** — пин свежего
  `impersonate`-таргета лечит ничего; (2) **Playwright бесполезен** — решать нечего, это плоский
  WAF-отказ, а не JS-челлендж; (3) причина — репутация датацентрового IP (вероятностный bot score),
  лечится только другим egress'ом (прокси / self-hosted runner) — решение оператора, вынесено в
  отдельную issue. **Почему это не тест:** воспроизвести можно лишь с CI-IP, а исход зависит от
  внешнего скоринга — любой «тест» мерил бы погоду у Cloudflare. Стоящая на этом месте страховка —
  **видимость** (§IV): `describe_block` пишет per-attempt WARNING (`cf-ray`, `cf-mitigated`,
  Cloudflare error code, `<title>`, размер тела), по которому следующий инцидент разбирается из
  лога, а не повторным ручным замером. Сам форматтер — pure и покрыт unit'ами
  (`TestBlockDiagnostics`) поверх **реальной** блок-страницы `tests/fixtures/cloudflare_block_403.html`
  (IP/Ray-ID обезличены): reality-anchor держит контракт «сигнал в `<title>`, а не в префиксе тела»
  — первые ~200 символов настоящей страницы это `<!DOCTYPE html> <!--[if lt IE 7]>…`.

- **T. YouTube throttle/retry: механизм отвергнут замером, поэтому тестов на него нет и не будет (#384).**
  План #384 предполагал `tenacity` (`wait_exponential`, 3 попытки, глобальный give-up) и test plan
  под него. Замер 2026-07-26 (Service Usage API): `search.list` — **100 запросов в сутки**, квота
  дефолтная и поднятой быть не может (billing выключен), а прогон на 170 фильмов просит 340. Лимит
  считается **в запросах за сутки**, поэтому паузами не лечится: пейсинг раздаёт те же 100 ровнее,
  retry отбирает квоту у следующего фильма. Вместо него — остановка обогащения по первому квотному
  отказу (`YoutubeQuotaExhausted`, покрыто `TestQuotaStop` + `TestQuotaDetection`); rationale — в
  [`pipeline.md`](pipeline.md#trailer-retrieval-and-selection-140-141-144). Записано сюда, чтобы
  `tenacity`+`sleep` не переоткрыли как «очевидно недостающий retry»: это не пробел покрытия, а
  отсутствующий по замеру код. Единственный путь к полному охвату — смена источника (TMDB), не retry.
  **Что покрыто, а не пропущено:** предикат квотных ошибок нужен и существует — `_is_quota_error`
  пинится reality-anchor'ом на настоящем `googleapiclient.errors.HttpError` (429 legacy `errors[]`,
  403 `quotaExceeded`, ErrorInfo `details[]` в SCREAMING_SNAKE), потому что `.reason` — человеческий
  текст, а машинный код живёт в `error_details`. Промежуточный вариант с фиксированным бюджетом
  (`_TRAILER_RUN_BUDGET = 45`) отвергнут до мержа: угаданное число, ломается на втором прогоне в
  сутки и занижает охват на одноветочных items — в коде не осталось.

- **U. Качество подбора трейлера для ИГР не измеряется — метрики нет и синтетической не будет (#385).**
  #385 чинит **классификацию** (игровой листинг `t=7` → профиль без `original_title`, чтобы в YouTube
  не уходило `x64 2024 trailer`), и ровно это покрыто тестами. А вот *насколько хорош* подобранный
  для игры трейлер — не покрыто: golden-set (`tests/fixtures/trailer_golden.json`) игровых кейсов
  **не содержит**, поэтому `scripts/eval_trailers.py` по играм не двигается вообще. Синтетический
  кейс сознательно не добавляем — прецедент #359: догадка в эталоне отравляет eval, а «правильный»
  трейлер игры нужно устанавливать вручную, как и для фильмов. Пока игровых замеров нет, §III
  запрещает обещать качество.
  **Известное смещение, которое это скрывает:** `HeuristicStrategy._rank` (`trailer_strategy.py`)
  первично ранжирует по кириллице в заголовке кандидата — правило, выведенное для фильмов с русским
  дубляжом (#141/#315). У игры название латиницей и русского дубляжа не бывает, поэтому русский
  лец-плей может обойти официальный трейлер. Эффект **предсуществующий** (был и до #385, просто
  запрос тогда уходил с мусорным `x64`), не регрессия — записан здесь, чтобы его не открыли как
  новый баг #385 и не «починили» правку ранжирования без метрики, которой пока нет.

- **V. Секрет-гейт: захваченные HTML-фикстуры вне скана, а ушедшие с `pre-commit` хуки не
  заменяются (#389).** `ci_check` шаг `secrets` покрыт `tests/test_secrets_gate.py` (подсадной ключ →
  non-zero, чистый файл → 0, сбой `git ls-files` и пустой список → видимый `exit 1`). **Вне скана
  сознательно:** `tests/fixtures/**/*.html` — захваченная чужая разметка, где хеши ассетов дают
  high-entropy FP по построению (15 находок на две фикстуры). Исключение файлом, а не baseline'ом:
  baseline при совпадении переписывает себя и возвращает rc=3, а перегенерация — это кнопка «сделать
  гейт зелёным» для настоящего утёкшего ключа (rationale — [`ci.md`](ci.md#secret-scan-secrets-389)).
  Цена: ключ, вписанный **внутрь** такой фикстуры, гейтом не ловится — остаётся серверный слой
  (GitHub push protection). **Второе — не пробел, а отсутствующий код:** вместе с `.pre-commit-config.yaml`
  ушли `check-yaml`/`check-toml`/`check-json`/`trailing-whitespace`/`end-of-file-fixer`; они не
  исполнялись **ни разу** (`core.hooksPath` = `.githooks`), поэтому регрессии нет и замену им этот PR
  не заводит. Записано, чтобы «а где проверка YAML?» не переоткрыли как пробел покрытия: это
  осознанный не-скоуп, отдельная единица (workflow #4).

- **W. Промпты ревьюеров: форма стережётся, семантика — нет (#374, #392).** Оба
  ревьюера — cloud (`.github/workflows/claude-review.yml`) и локальный
  (`.claude/agents/architect-reviewer.md`) — раньше несли severity-фильтр *на стадии
  поиска*, и модель исполняла его буквально: находка молча не доходила до PR. Гарды
  ловят **известные формы**, и каждый — свои, потому что промпты разные:
  `tests/test_claude_review_workflow.py` (англоязычный промпт) — императив
  подавления в начале строки, наличие `severity` **и** `confidence`, отсутствие
  gag-строки `no blocking issues`; `tests/test_agent_frontmatter.py`
  (русскоязычный промпт, поэтому не regex по началу строки, а снятые формулировки
  дословно) — `не раздувай` / `беспощаден` / `краткость по умолчанию` не вернулись,
  наличие `confidence` и `blocking`. Второй применяется **только** к агентам,
  декларирующим findings-контракт (#407) — прочим агентам эти токены не нужны.
  **Сознательно НЕ
  покрыта семантическая перефразировка** («будь избирателен», «only report what
  matters»): проверка смысла промпта — это LLM-вызов на каждый прогон suite, то есть
  дороже и менее детерминированно, чем предмет проверки; а регексп по открытому
  множеству формулировок даёт change-detector, скроенный под текущий текст (первая
  версия гарда #374 именно так и выглядела — с карв-аутом «разрешено, если рядом
  слово ruff» — и была забракована на architect-review). Остаточная защита —
  проза [`ci.md`](ci.md#coverage-first-prompt-no-filtering-at-the-search-stage-374) и
  сам plan-ревьюер. Записано, чтобы «а почему нет теста на промпт» не переоткрыли:
  тест есть, отклонена именно семантическая его половина.

- **X. Кодировка subprocess: гард стережёт сторону родителя, не ребёнка (#364).**
  `tests/test_subprocess_encoding.py` (AST по `scripts/**`, `src/**`, `tests/**`)
  требует явный `encoding` у вызова, который захватывает вывод в текстовом режиме —
  без него Windows декодит кодовой страницей ОС и теряет весь вывод на первом
  кириллическом байте. **Сознательно не покрыта половина ребёнка:** дочерний Python
  пишет в pipe своей ANSI-кодировкой, пока не получит `PYTHONUTF8=1`/`-X utf8`, и
  такой call-site гард пометит зелёным. Статически это не проверить: нужный env
  собирается в рантайме (`ci_check` передаёт `-X utf8` детект-секретам,
  `test_github_trending_pipeline` — `PYTHONUTF8` в собранном `env`), а требовать
  флаг у **каждого** запуска Python дало бы ложные срабатывания там, где вывод
  заведомо ASCII. **Общий `run_text()`-хелпер ОТВЕРГНУТ окончательно (#410),** а не
  отложен, как записывала первая версия этого пункта, и причина техническая:
  репо-корень **никогда не на `sys.path`** при документированном CLI
  `python scripts/foo.py` (`sys.path[0]` = `scripts/`, editable-install добавляет
  только `src/`) — механика уже описана в `scripts/issue_branch.py`. Каждый
  скрипт получил бы importlib-бутстрап (~8 строк), то есть бойлерплейта больше,
  чем удаляемого кода, а `python -m scripts.foo` сломал бы CLI, `settings.json`,
  pre-push и доки. Плюс три call-site в хелпер не влезают в принципе
  (`ci_check._run` и `new_branch._run(capture=False)` намеренно **не** захватывают
  вывод, `ci_check._tracked_files` намеренно **бинарный**). Инвариант вместо
  хелпера держит **правило в самом гарде** — и, в отличие от хелпера, оно ещё и
  мешает написать дефолт заново. И **отклонён `PYTHONUTF8=1` как единственное
  лекарство** — он чинит обе половины сразу, но живёт в состоянии среды, невидим
  на свежем клоне и не защищает call-site, запущенный иначе; гарантия слабее, чем
  у гейта на исходнике. Записано, чтобы «а почему не хелпер / не переменная среды»
  не переоткрыли как work-for-work.

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
| Telethon session rotation (mint a `StringSession`, set the secret, revoke the old session in the Telegram app) | Interactive login against live Telegram, performed by an operator roughly once per incident | **Scope-skip** (#386, replaces the `crypto.py` glue entry that left with the module): there is no automatable surface — the code side *is* covered (`require_env` rejects an empty secret, `TestTelethonReaderAuth` pins StringSession-only auth and a fail-fast on a revoked session). The recipe lives in [ci.md](ci.md); deliberately a doc snippet, not a script — a once-a-year human interactive is not the deterministic pipeline step "скрипты > инструкции" targets |
| `scripts/probe.py` — живой запрос к soldoutticketbox.com | Замер вероятности блокировки требует настоящего датацентрового IP и настоящего Cloudflare | **Scope-skip** (#396): живой запрос из теста и есть работа самого пробника в CI — дублировать его в pytest значило бы гонять сеть на каждом прогоне ради того, что и так меряется каждые 3 часа. Покрыты именно границы, где замер может тихо испортиться: одна попытка на замер (не 4), те же request-kwargs что у прода (`TestSharedRequestKwargs`), строка на **каждый** исход включая 200, разделение «HTTP-исход → 0» / «сбой инструмента → ≠0», expiry и оба пути (HTML + постер) |
