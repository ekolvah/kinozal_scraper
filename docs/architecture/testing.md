# Testing philosophy

> **Question this document answers:** How do we plan to guarantee product quality — the
> levels, the taxonomy and what we mock.
>
> Navigation «which tests touch module X» is `grep` by module name, not a hand-curated
> table. The one thing grep can't answer — *why we deliberately don't test Y* — is the
> [`coverage-gaps.md`](coverage-gaps.md) ledger, which lives in its own file.

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

## Eval harness — trailer selection

`scripts/eval_trailers.py` (#139) measures trailer-pick quality against a **frozen golden-set**
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
  - **Why a second column at all.** A policy change in `enrich_with_trailer` — the layer *above*
    `HeuristicStrategy.pick` — leaves the pick scorecard **bit-identical** while moving delivery by
    10 films (measured: 26→16, #359). Measuring `pick` alone is blind to exactly the class of change
    the gate exists for. Today both columns agree by construction (no policy sits between them); the
    point is that they *can* diverge.
  - **The baseline is the gate.** `tests/fixtures/trailer_baseline.json` pins the **delivery**
    outcome per case (`{"i", "film", "outcome"}` — the index rides along because `ru_title` is not
    unique: "Гладиатор 2" appears twice, and swapping two same-named cases would slip past a
    name-only check). `compare_to_baseline` is a pure function; the red comes from
    `tests/test_eval_baseline.py::TestBaselineGate::test_committed_baseline_matches_main`.
    **Why a ratchet and not an absolute threshold.** A threshold would need a strategy good enough
    to declare "this score is acceptable" — a judgement nobody can make while the strategy is still
    moving. A ratchet needs no such thing: it pins the *current fact*, so any movement becomes a
    reviewable diff line.
  - **Any divergence is red, improvements included.** "Green with a warning" would rebuild the very
    defect being fixed — a signal nobody is obliged to read (§IV). Worse, once wrong-cases land
    (#380) a net-positive delta could hide a `hit→wrong` swap; per-case comparison cannot.
    `--update-baseline` regenerates the fixture, so a deliberate improvement lands in the PR diff.
  - **Proof, not reasoning.** `TestBaselineGate::test_reverted_359_policy_fails_the_gate` runs a
    counterfactual policy (suppress `confidence < 0.5`, #359) through the same two functions the real
    gate uses and asserts the verdict is red with the moved films named — reproducing 26→16 exactly.
    The counterfactual policy lives in the test file, never in `src`.
  - **Where the gate stops.** It covers `select_trailer`. Profile derivation from the kinozal title
    (clean-title / `original_title` / year-regex / the game branch, #385, #393) is
    outside the measurement and rests on `TestEnrichWithTrailer` unit tests. This limit is
    load-bearing: a change written in that blind spot passes green.

- **Отрицательный полюс метрики: разметка `trap` (#380).** Шкала `Hit +1 / Miss 0 / Wrong −2`
  объявляет, что чужой трейлер вдвое хуже честного маркера, — и половина шкалы мертва, если в
  наборе нет ни одного `wrong`: такой набор наказывает за осторожность, но «сколько wrong
  предотвращено» показать не в состоянии (#327, #359).
  - **Что размечено.** Три кейса с живьём записанными пулами и полем `trap` — id кандидатов, про
    которых **верифицировано** (через `videos.list` → канал + описание, основание в `note`), что
    это *другая работа*: фанатский Minecraft-продакшен под названием сериала, хоррор-фильм-тёзка,
    сериал `The Rookie` под названием фильма `The Amateur`. Один из них стратегия сегодня и
    выбирает, поэтому скоркарта не `wrong=0`.
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
  - **Что метрика показывает.** Контрфактическая политика «давить `confidence < 0.5`» (#359) на
    наборе с полюсом даёт 26 → 14 и **не трогает `wrong` вообще** (как был 1, так и остаётся):
    реальный чужой pick идёт с `confidence=0.9`. Порог по уверенности ортогонален наблюдаемому
    классу ошибок — вывод, который на наборе без полюса непроверяем
    (канон — [pipeline.md](pipeline.md#trailer-retrieval-and-selection)).
  - **Дрейф пулов — не теория.** Повторная запись пула «Крайних мер» через час уже не возвращает
    пришпиленный `trap`-id. Поэтому `_record` перевалидирует свежий payload **до** `write_text`:
    иначе файл сохранился бы, а упала бы следующая *загрузка* — у всех, кто просто запустил
    `pytest`, и без намёка на причину. И поэтому же новый кейс записывается через
    `--record --golden <scratch>.json` на однокейсовом файле, а не переписыванием фикстуры целиком.
  - **Вне TMDB-колонки (сознательно).** У `trap`-кейсов `tmdb_videos: []` — единственный способ
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
  - **Honest accept-set expansion (B1, #329).** Accept-sets seeded from YouTube retrieval (#327)
    miss TMDB's *valid* RU dubs (different video_id, same film), which would score Wrong. Additions
    are per-id **content-verified** (the video name identifies the correct film + RU dub) and
    hard-coded — never "trust TMDB output wholesale". The non-circular control is TMDB measured
    against the **pre-expansion** set (a conservative floor); expansion is only for ground-truth
    completeness, symmetric — the set holds both the YouTube-surfaced and TMDB-surfaced valid dubs,
    so neither source is unfairly penalised.

## Eval harness — summarizer faithfulness

`scripts/eval_summarizer.py` (#347) measures `summary_ru` **meaning** (not just the `response_pattern`
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

The ledger moved to its own file: [`coverage-gaps.md`](coverage-gaps.md) — records `A`…`AB`
plus «modules without dedicated tests». This doc keeps the strategy, that one keeps the
case-by-case exceptions and the rule for which home a decision goes to.
