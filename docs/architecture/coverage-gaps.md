# Consciously-accepted coverage gaps

> **Question this document answers:** where in this repo we deliberately do **not** test, and
> why — so that a rejected-as-negative-ROI decision is not silently re-opened as work-for-work
> (goal-function priority (2)). Strategy — levels, taxonomy, what we mock — is
> [`testing.md`](testing.md); this file is the case-by-case ledger it refers to.
>
> Records carry stable letter IDs (`A`…`AI`); a state doc links to the letter instead of
> retelling the reasoning next to itself.

Every bug category in the [taxonomy](testing.md#bug-taxonomy) is covered by tests today (navigate to
them with `grep` by module/feature name — there is no hand-curated per-category index, it
only drifts).

**What belongs here:** "we didn't cover X with a test". The other branches of the "which home a
decision goes to" route — and the rule itself — live in
[`project-map.md`](project-map.md) §Canonical-home.

**Rejected as negative-ROI (a test would only ever guard CI minutes, not correctness):**

- **A. Structure drift — no *live* E2E for GitHub `new_popular` / Steam JSON.** Integration
  tests cover parsing with saved fixtures; the daily cron is the E2E smoke (zero-row drift →
  red CI next run). A dedicated live-E2E was rejected per the «cron = E2E smoke» doctrine
  ([Test levels](testing.md#test-levels)). Live E2E *does* exist where structure drift is silent and
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
- **M. HTTP retry deliberately scoped to HTTP-status errors only (#306).** The shared layer
  (`http_retry`) fires on transient HTTP *responses* but **not** on network errors (`Timeout` /
  `ConnectionError` — `RequestException` subclasses that never reach `raise_for_status`, so the
  `isinstance(HTTPError)` predicate skips them by construction). **Accepted** — no reproduced
  incident (§V: don't retry what wasn't observed), symmetric with the `SheetsStorage` sibling which
  covers `APIError` status only. The asymmetry «503 retries, a DNS blip crashes the source» is real
  and conscious; a broadening waits for an actual network-error incident.
- **M2. 403/429 are NOT retried on the JSON-API transport (#365).** `github_popular_pipeline` and
  `steam_pipeline` run on `API_TRANSIENT_CODES` (5xx only), unlike the Cloudflare-fronted HTML
  transport whose 403 is a proven-transient anti-bot challenge. **Accepted**, but the two hosts
  rest on different evidence and the record must not blur them:
  - **GitHub — from the source.** The REST API documents the reset window
    (`x-ratelimit-reset` / `retry-after`) and warns that continuing to request while limited may
    get the integration banned. The 1/2/4 s backoff cannot close that window.
  - **Steam Store — by analogy, unverified.** `appdetails` has no public contract: no documented
    reset window, no stated ban policy, and no measurement of our own. The rule was carried over
    from GitHub because the shape of the failure looks the same — that is a judgement, not
    evidence, and it covers `_fetch_appdetails`, the one call site whose failure does not
    self-heal (#437). If a 429 is ever observed there, this is the entry to revisit first.

  Honouring `Retry-After` is the real fix for both and stays unbuilt while each source makes
  one-two calls per run.
- **M3. `success: false` on a 200 from Steam appdetails is not covered by retry (#365).** It is a
  second route to the same `⚠️ Game #` placeholder, and the predicate — keyed off `HTTPError` —
  skips it by construction. **Accepted**: no measurement separates it from the 5xx route today, and
  its cost comes from the placeholder being persisted as delivered and never re-resolving (#437),
  not from the missing retry. Recorded so it isn't re-opened as «retry doesn't work».

- **N. LLM / embedding / TMDB trailer-picker strategies built but deliberately NOT in the prod
  hot path (#144/#315).** Production `enrich_with_trailer` selects with deterministic `HeuristicStrategy`
  (#141); `LLMTrailerStrategy` (#142), `EmbeddingTrailerStrategy` (#143), and `tmdb_trailer.pick_trailer`
  (#329) remain eval-only. **The selection rationale (negative ROI, wrong=0 on the golden set) is
  canonical in
  [pipeline.md § Trailer retrieval and selection](pipeline.md#trailer-retrieval-and-selection)**,
  and is not duplicated here. Coverage consequence (its home is here): the pure selection layers
  of these strategies **are covered** by unit tests; only live Gemini engines are uncovered (rows
  below). Recorded to prevent reopening "why is the LLM picker not in production?".

  **A related conclusion recorded here: do not add `confidence` selection — the hypothesis was
  tested twice, both tests reject it.** Production ties are common (in run `30066249488`, 5 of 6
  picks were `ambiguous (conf=0.3)`), and the hypothesis "tie → arbitrary selection → wrong link"
  can be implemented by suppressing picks with `confidence < 0.5` to a miss marker. Measurement
  disproves it (#359): on 28 golden cases, 26 hit → 16, 2 miss → 12, wrong 0 → 0 — all 10 suppressed
  picks were **hits**, because `confidence=0.3` means "several equally good trailers for one film"
  (dub #1 vs #2), exactly what accept sets model. That set had no `wrong`, so it could physically
  show no policy benefit; on a set with a verified foreign candidate (`trap`, #380), the policy
  yields 26 → 14 and **does not change `wrong` at all** — the real wrong pick has
  `confidence=0.9`, a unique top rank. The confidence threshold is orthogonal to the observed error
  class. Use diagnostics instead: `video_id` in the success breadcrumb. Cast as a tie breaker is
  wontfix (#377). No golden record for "Vanity Fair" was added: the captured pool has no verifiably
  wrong candidate (all five are trailers for the same series), and a guess in the reference poisons
  evaluation.

  **Open-world caveat:** 3 `wrong` cases were found among ~150 checked live picks — the class is
  rare (~1%), so the set represents it thinly; it grows from real incidents (the production log
  carries `video_id` → `videos.list` → manual verification).

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
  `--threshold` gates it; the [harness section](testing.md#eval-harness--summarizer-faithfulness) documents
  the seam split. Recorded so «why isn't the RAGAS score in CI?» isn't re-opened as a mock-the-judge
  work-for-work test.
- **R. Dedupe-key edges where the year anchor can't disambiguate (#363).** The kinozal dedupe key is
  the `RU / Original / Year` prefix of the raw title, located by scanning for the first bare-year
  segment (`text_utils.YEAR_SEGMENT_RE`) and dropping the format tail. Two edges are **consciously
  accepted**, characterized but not "fixed": (1) a film whose **RU title IS a bare year** (`2012`,
  `1917` → raw `2012 / 2012 / 2009 / …`) keys on that first-segment year → `"2012"` — a no-op vs. the
  plain first-segment behaviour the year anchor replaced, same class as `original_title`'s
  numeric-original edge (#138 Out of scope), pinned by `test_year_titled_film_collapses_to_year`. (2) a **yearless** raw title (no bare-year
  segment at all) falls back to the clean first segment, so yearless namesakes could still collapse — but
  a top-page title without a year is anomalous, and any collapse is already visible in aggregate via the
  `_dedup_and_log_coverage` INFO line (`N extracted (M after dedup-collapse)`), so it isn't a silent §IV
  loss. Both accepted because the disambiguating signal (a distinct year) is genuinely absent; recorded so
  a year-titled namesake collision isn't re-opened as a regression.
- **S. Anti-bot 403: root cause is the *egress IP*, and it can only be observed in prod (#358).**
  Measurement on 2026-07-25 with the same `curl_cffi==0.15.0` as production: locally (residential
  IP), `impersonate="chrome"` → 200, **without** impersonation → 403, and
  `chrome124`/`chrome131` → the same 200; in CI (a GitHub Actions datacentre IP) → 403 for seven
  consecutive days (18–24 July), while that same CI received 200 on 14 and 17 July. The block body
  contains neither `cdn-cgi/challenge-platform` nor turnstile. Conclusions recorded to prevent
  reopening: (1) the TLS fingerprint (#217) is **correct** — pinning a newer `impersonate` target
  fixes nothing; (2) **Playwright is useless** — there is nothing to solve; this is a flat WAF
  denial, not a JS challenge; (3) the cause is datacentre-IP reputation (a probabilistic bot score),
  remediable only by other egress (proxy / self-hosted runner) — an operator decision in a separate
  issue. **Why this is not a test:** it can be reproduced only with a CI IP and the result depends
  on external scoring — any "test" would measure Cloudflare weather. The safety net here is
  **visibility** (§IV): `describe_block` logs a per-attempt WARNING (`cf-ray`, `cf-mitigated`,
  Cloudflare error code, `<title>`, body size), allowing the next incident to be diagnosed from the
  log rather than another manual measurement. The formatter itself is pure and unit-covered
  (`TestBlockDiagnostics`) against the **real** block page
  `tests/fixtures/cloudflare_block_403.html` (IP/Ray-ID anonymised): the reality anchor holds the
  contract "signal in `<title>`, not in the body prefix" — the first ~200 characters of the real
  page are `<!DOCTYPE html> <!--[if lt IE 7]>…`.

- **T. YouTube throttle/retry: rejected by measurement, so there are and will be no tests for it (#384).**
  The tempting `tenacity` (`wait_exponential`, three attempts, global give-up) was rejected by a
  2026-07-26 measurement (Service Usage API): `search.list` has **100 requests per day**, the quota
  is default and cannot be raised (billing is disabled), while a 170-film run requests 340. The
  limit is counted **in requests per day**, so pauses cannot fix it: pacing merely distributes the
  same 100 more evenly and retry takes quota from the next film. Instead, enrichment stops at the
  first quota failure (`YoutubeQuotaExhausted`, covered by `TestQuotaStop` + `TestQuotaDetection`);
  rationale is in [`pipeline.md`](pipeline.md#trailer-retrieval-and-selection). This is recorded so
  `tenacity`+`sleep` is not reopened as an "obviously missing retry": it is not a coverage gap but
  code absent by measurement. The only path to full coverage is a source change (TMDB), not retry.
  **What is covered, not skipped:** the quota-error predicate is needed and exists —
  `_is_quota_error` is pinned by a reality anchor on real `googleapiclient.errors.HttpError` (429
  legacy `errors[]`, 403 `quotaExceeded`, ErrorInfo `details[]` in SCREAMING_SNAKE), because
  `.reason` is human text while the machine code lives in `error_details`. A fixed per-run request
  budget (`_TRAILER_RUN_BUDGET = 45`) was rejected for the same reason: it is a guessed number,
  fails on the second run of the day, and undercovers one-branch items.

- **U. Trailer-selection quality for games is measured by ONE case (#385, #412).**
  The game class is represented in the golden set by one **live** case `Marvel Человек-Паук 2`
  (pool recorded on 2026-07-29 through production `search_candidates`; accept set — four official
  PlayStation/Marvel Entertainment trailers; `trap` — four trailers for the 2026 namesake film).
  Do not add synthetic data: a guess in the reference poisons evaluation (#359). One case is a
  pole, not a class metric: §III forbids promising "game trailers are selected well"; it merely
  establishes that this particular miss class does not pass silently.
  **Why it pays for itself:** the design "base title part — fallback when relevant is empty" enters
  the scorecard as `WRONG` — the full edition title occurs consecutively in a costume-compilation
  title, so selection never reaches real trailers; without this case, such a change would reach
  production green. **There is no per-item ground-truth "this is a game" label:** the discriminator
  lives in title grammar, not listing category, so a future game sub-metric reconstructs the label
  from raw-title form (`x64`/`RU` in the second segment + `PC (Windows)` suffix), not category (#412).
  **Four consciously open boundaries (#412):**
  1. **A parenthetical part in the second segment** (`… / Dune (Part Two) / …`) would collapse the
     base to the franchise, and `_title_tokens_in` with its numeric skip would accept another work.
     The class is measured and nearly empty: 1 of 238 titles has a parenthesis in the second segment
     (`Heroes of Might and Magic IV (4) (Complete)`), and its part number is already in the title,
     so no collapse occurs. No guard test is written — there is nothing to fix; if the class appears,
     it becomes a bug with a live example.
  2. **The query still includes the edition** (`Marvel's Spider-Man 2 (Digital Deluxe Edition)
     2025 trailer`): trimming exists only in relevance. Measurement shows a normal pool (the case is
     HIT), so we depend on YouTube tolerating extra tokens; if that changes, the miss appears as an
     ordinary miss marker.
  3. **The baseline gate does not guard `html.unescape`:** the golden fixture migrated to decoded
     form (as `--record` produces it through production `search_candidates`), so removing unescape
     from `_search_one` leaves the scorecard green. Only
     `tests/test_youtube.py::TestSearchCandidates::test_html_entities_decoded` catches the regression.
  4. **A game's year is the repack year, not release year.** The game profile carries the Kinozal
     listing year (2025 for the PC port), while official trailers were made in 2023–2024. The case
     passes the year filter only because these trailer titles contain no year; a title like
     `… (2023) Launch Trailer` would be rejected by `title_year_matches`. The effect predates this
     work, but has been **reachable** precisely since games reach retrieval with their real title
     (#412).
  **Known bias this hides:** `HeuristicStrategy._rank` (`trailer_strategy.py`) primarily ranks by
  Cyrillic in a candidate title — a rule derived for films with Russian dubbing (#141/#315). A game
  has a Latin-script name and no Russian dub, so a Russian let's-play can outrank an official trailer.
  The effect **predates this work**, not a regression; it is recorded here to prevent reopening it as
  a new bug or "fixing" ranking without a metric that does not yet exist (#385). The bias does not
  fire on the only game case: the Russian candidates in the pool are trailers for the 2026 namesake
  film and the year rejects them. Thus one case neither disproves nor confirms it.

- **V. Secret gate: captured HTML fixtures are outside the scan, and hooks that left with
  `pre-commit` are not replaced (#389).** The `ci_check` `secrets` step is covered by
  `tests/test_secrets_gate.py` (planted key → non-zero, clean file → 0, `git ls-files` failure and
  empty list → visible `exit 1`). **Consciously outside the scan:** `tests/fixtures/**/*.html` —
  captured third-party markup where asset hashes produce high-entropy false positives by construction
  (15 findings in two fixtures). Exclude by file, not baseline: on a match the baseline rewrites
  itself and returns rc=3, while regeneration is a button to make the gate green for a genuinely
  leaked key (rationale — [`ci.md`](ci.md#secret-scan-secrets)). The cost is that a key written
  **inside** such a fixture is not caught by this gate; the server-side layer remains (GitHub push
  protection). **The second item is not a gap but absent code:**
  `check-yaml`/`check-toml`/`check-json`/`trailing-whitespace`/`end-of-file-fixer` left with
  `.pre-commit-config.yaml`; they ran **zero times** (`core.hooksPath` = `.githooks`), so there is
  no regression and this PR does not add replacements. Recorded so "where is YAML validation?" is
  not reopened as a coverage gap: it is conscious non-scope, a separate unit
  (`agent-process.md`, Governance conventions).

- **W. Reviewer prompts: form is guarded, semantics are not (#374, #392).** Neither reviewer —
  cloud (`.github/workflows/agent-review.yml`) nor local
  (`.claude/agents/architect-reviewer.md`) — contains a severity filter *at the discovery stage*:
  the model follows such a filter literally and a finding silently never reaches the PR. Guards
  catch **known forms**, and the two guards keep different shapes:
  `tests/test_agent_review_workflow.py` checks a suppression imperative at line
  start, presence of `severity` **and** `confidence`, and absence of the gag line
  `no blocking issues`; `tests/test_agent_frontmatter.py` denies removed wording verbatim —
  `do not inflate` / `ruthless` / `brevity by default` — and requires `confidence` and `blocking`.
  **The verbatim denylist covers only the English return path** (#470): the phrasings actually
  removed were Russian, and they are now kept out transitively by `check_language.py`, which
  covers `.claude/**` Markdown prose. Narrowing or dropping the language gate therefore silently
  reopens that hole — the dependency is recorded here because it is invisible in either test.
  The frontmatter guard
  applies **only** to agents declaring the findings contract (#407); other agents do not need these
  tokens. **Semantic paraphrase is consciously NOT covered** ("be selective", "only report what
  matters"): checking prompt meaning would require an LLM call for every suite run, therefore cost
  more and be less deterministic than the subject under test; while a regex over an open set of
  phrasings creates a change detector tailored to current text (the carve-out "allowed if `ruff` is
  nearby" is exactly such a detector, rejected for architect review, #374). Residual protection is
  the prose in [`ci.md`](ci.md#coverage-first-prompt-no-filtering-at-the-search-stage) and the plan
  reviewer itself. Recorded so "why is there no prompt test?" is not reopened: the test exists;
  only its semantic half was rejected.

- **X. Subprocess encoding: the guard protects the parent side, not the child (#364).**
  `tests/test_subprocess_encoding.py` (AST over `scripts/**`, `src/**`, `tests/**`) requires explicit
  `encoding` on a call that captures text-mode output — without it, Windows decodes with the OS code
  page and loses all output at the first Cyrillic byte. **The child half is consciously uncovered:**
  child Python writes to the pipe in its ANSI encoding until it receives `PYTHONUTF8=1`/`-X utf8`,
  and the call-site guard marks such a case green. This cannot be checked statically: the necessary
  environment is assembled at runtime (`ci_check` passes `-X utf8` to detect-secrets;
  `test_github_trending_pipeline` puts `PYTHONUTF8` in constructed `env`), while requiring a flag on
  **every** Python launch would create false positives where output is known ASCII. A shared
  `run_text()` helper was **rejected, not deferred (#410)** for a technical reason: the repository
  root is **never on `sys.path`** under documented CLI `python scripts/foo.py`
  (`sys.path[0]` = `scripts/`; editable install adds only `src/`) — mechanics already documented in
  `scripts/issue_branch.py`. Every script would need an importlib bootstrap (~8 lines), more
  boilerplate than removed code, while `python -m scripts.foo` would break the CLI, `settings.json`,
  pre-push, and documentation. In addition, three call sites cannot use a helper in principle:
  `ci_check._run` and `new_branch._run(capture=False)` deliberately **do not** capture output, while
  `ci_check._tracked_files` is deliberately **binary**. Instead of a helper, the invariant is held
  by a **rule in the guard itself** — unlike a helper, it also prevents reintroducing the default.
  `PYTHONUTF8=1` as the **sole remedy was also rejected**: it fixes both halves at once, but lives in
  environment state, is invisible in a fresh clone, and does not protect a call site launched
  differently; its guarantee is weaker than a source gate. Recorded so "why not a helper / environment
  variable?" is not reopened as work-for-work.

  **Boundaries of the "output default is forbidden" rule (#410).** It recognises
  `<expression>.stdout or …` / `.stderr or …` by the **attribute left of `or`**, and
  consciously does NOT catch: (a) reassignment to an intermediate variable
  (`out = proc.stdout` → `out or ""`), (b) `getattr(proc, "stdout") or ""`,
  (c) the equivalent `if proc.stdout is None: proc.stdout = ""`. Expanding to value
  tracing is data flow, not syntax: its cost rises qualitatively while catching the same one class.
  The rule guarantees that the **direct** idiom cannot return; the repository has no indirect form
  today, and a human catches one in review. It is deliberately narrow also because a broad rule
  ("any `or ""`") would flag legitimate defaults (`os.environ.get(...) or ""`) and would have to be
  weakened — a pytest assertion has no `noqa` with which to silence it.

  **Not every new branch is covered — consciously (#410).** Tests pin three **distinguishing**
  decisions where confusing outcomes is costly: `check_red` → code 2 ("gate broken"), not 1
  ("tests are not red") — `/implement` step 3 treats them differently; `hooks._run_ruff` →
  `setup_broken` signal, not exception (otherwise stderr reaches the user but not the agent);
  `ci_check._tracked_files` → "file set is unknown", not misleading "no files to scan". Branches
  in `open_pr`/`set_issue_priority`/`issue_branch`/`validate_issue_sections`/`verify_pr_link` remain
  **without dedicated tests**: they have the same outcome ("visible error instead of emptiness"),
  no distinguishing decision, and five copies of one test would be change detectors. The guard rule
  protects them: the default cannot return without making `test_no_output_defaults` red. Recorded so
  the omission is a decision, not forgetfulness.

- **Z. Relative-link integrity between `.md` files is not guarded (#418).** Moving the runtime half
  of `ci.md` to `operations.md` retargeted eight incoming pointers, half of which were prose and
  code comments rather than Markdown links. There is **no** "file exists + anchor resolves" gate,
  and it is consciously not introduced here: it is a separate logical unit (a `CHECKS` entry +
  parity row in `ci.yml` + tests + cost on every run), not an add-on to a documentation PR. More
  importantly, **it would not have caught the discovered incident**: a comment in
  `test_kinozal_pipeline.py` linked to `ci.md:435`, i.e. **by line number**; the file existed, there
  was no anchor at all, and the link silently went stale. The root cause for that class is line-number
  links themselves; it was removed by replacing both such links with section anchors. Recorded so a
  future link checker is not justified by this incident — it concerns another class.

- **AA. "The document must not grow again" is not guarded (#419).** Compacting `ci.md`
  (618 → 417 lines) removed accumulated decision archaeology that already has a home — the relevant
  issue bodies (#235, #255, #396). The tempting anti-recurrence gate "file must have no more than N
  lines" was rejected as **Goodhart**: below a threshold, wording is compressed rather than
  archaeology removed, so the gate is green precisely when the defect is hidden. The semantic
  judgement "how much is rationale prose here and how much is rule" is the same class as the
  semantic-duplicate detector the repository consciously does not build (`project-map.md`); such a
  detector would provide false coverage (§IV). **The real anti-recurrence here is format, not rule:**
  a post-mortem cannot physically fit in a table or ledger row but does fit in a free section.
  Format > prose > gate. Recorded so "why is there no documentation-size gate?" is not reopened as
  work-for-work. **Recording boundary:** this concerns documentation read on demand, where size is
  only a *proxy* for quality. For the always-load set, conversely, a gate exists
  (`tests/test_always_load_budget.py`, #375): there bytes are not a proxy but the charge in every
  session, and the threshold acts as a ratchet rather than a quality norm. The question that
  distinguishes the cases is "is the metric a proxy or the cost itself?", not "size cannot be gated".

- **AB. The always-load budget measures a narrower set than the session preamble (#375).**
  `test_always_load_budget` counts `CLAUDE.md` + `.claude/rules/*.md` without `paths:`, but the
  preamble also includes subagent and slash-command `description:` fields and the `MEMORY.md`
  index. One threshold over this heterogeneous sum would impede diagnosis — a red test would not say
  where growth occurred — so **the gate consciously does not catch cost shifting there** (nor moving
  text into `docs/architecture/*`, which the agent still reads on demand). Growth specifically in
  agent/command frontmatter is a reason to add a **second** counter, not extend this one.

- **AC. A date in documentation is not guarded by a marker for dated material (#428).** The
  link-form guard (`tests/test_doc_narrative.py`) took two of three branches announced in the issue;
  the third — "`20\d\d-\d\d-\d\d` outside an explicit measurement marker" — was **not taken**.
  There are zero violations, no recurrence precedent, and the canon (`project-map.md` §"What
  documentation describes") says nothing about dates, so the predicate itself would be the only
  rule definition. Its closed marker vocabulary (`замер`, `проверено`, `measured`, …) would have to
  be inferred from seven live lines, fitting the text: the first legitimate "as of 2026-08-01" would
  make CI red for a correct document, and maintenance would become "it turned red → add a word".
  Recorded so the branch is not reopened as forgotten: revisit when there is a **measured** recurrence
  and a date rule in the canon, not vice versa.

- **AD. The network half of branch-protection verification is not run in CI (#436).**
  `scripts/check_branch_protection.py` compares the declared composition of required contexts with
  the actual one. Unit tests cover everything except one step — real `gh api` for configuration:
  `GITHUB_TOKEN` lacks `administration` scope, and classic branch protection is not visible through
  the ruleset endpoint (it returns `[]`), so a CI run would require a separate admin token in
  secrets. **Rejected for cost, not impossibility:** a long-lived secret requires rotation, while an
  expired token turns the job red without real drift and teaches people to ignore the detector.
  Compensation is `.githooks/pre-push` on every push (more frequent than a plausible cron), plus
  offline guard `tests/test_branch_protection.py`, which keeps the in-repository half
  (declaration ↔ workflow jobs) in CI. Revisit when enforcement moves to rulesets that make the
  configuration readable with ordinary repository read access.
- **AE. Do not add a detector for a lost Soldout poster.** The notification goes without an image,
  Sheets dedup records it as sent, and there will be no second attempt. Previously this almost never
  occurred (the page itself was unreachable); patient retry raises the share of days that deliver,
  and with it the frequency of this outcome. Measurement: 3 successes out of 8 on the image path vs
  1 out of 4 on the page, meaning posters are blocked **separately**, so "they will be fixed by the
  same workaround" is a hypothesis. Patient policy is deliberately not applied to them: it
  multiplies by item count and would consume the entire run (guard:
  `test_http_fetch.py::TestPatientHtml::test_fetch_bytes_stays_on_the_fast_transport`). The
  degradation is **visible** — a `WARNING` from `telegram_notifier._send_one`, not a silent skip —
  so a separate detector would be a second signal for what is already stated. Revisit trigger: the
  first complaint about a notification without an image; the workaround itself is tracked by a
  separate task (#441). Full decision: [ADR-0002](../adr/0002-soldout-cloudflare-spread-retries.md).
- **AF. "There has been no Soldout success for N days" is not detected.** The alert is tied to a
  run, not source state: an empty day is normal, so "we could not reach it today" and "the source
  died a week ago" are externally indistinguishable. The only remedy is state between runs (a
  "last success" cell + staleness rule), and its cost currently exceeds benefit: there is one run
  per day, hence an alert no more than once a day — the same noise volume as before the fix. The
  observable revisit trigger is the **first genuinely missed failure** (the source was down and we
  did not learn it from the alert), not "when alerts become annoying".

- **AH. Wiring `publish_run_summary` in `__main__` is not covered (#459).** The function and
  formatter are tested (`test_alerting.py::TestPublishRunSummary`), but the fact that "both GitHub
  `__main__` blocks call it, and call it *before* `sys.exit(1)`" is part of the general scope skip
  for `if __name__ == "__main__"` (see the table below): mypy holds the import and cron holds the
  smoke test. A separate static guard for call order would guard two code lines.

- **AI. An empty config `url` passes validation, and `soldout` skips green on it (#459).**
  `validate_sources_config` checks **presence** of the key (`_REQUIRED_SOURCE_FIELDS - source.keys()`),
  not non-emptiness, so `"url": ""` reaches runtime intact. For `soldout`, the URL is
  `{{SOLDOUT_URL}}`, and `build_macro_context` defaults the macro to `""`: with unset
  `vars.SOLDOUT_URL`, the run is green, there is no delivery, and the only trace is a WARNING in the
  step log. This is exactly the silence against which the operator summary was created, established
  while working on it (#459). **Why it was not fixed there:** the obvious fix (non-empty `url` →
  `ConfigError`) would fail config loading on **every run** in current production configuration —
  `run-script.yml` sets `KINOZAL_URLS` and never `KINOZAL_TOP_URL`, so `sources.json` always expands
  the Kinozal URL to `""`. (Kinozal itself does not suffer: it does not read a config URL at all,
  and missing URL gives it a red result with a reason.) Thus it must be fixed together with
  decoupling "URL in config vs URL in environment", a separate work unit. Observable revisit trigger:
  **any work on `SOLDOUT_URL`/the URL config schema**, not "someday"; until then, the gate is ordinary
  code review of `sources.json`.
- **AJ. Token-consumption metric is fundamentally not gated in `ci_check`/CI (#464).**
  `ci_check.py` has one `CHECKS` registry for local runs and CI, but metric data are Claude Code
  transcripts on the maintainer machine, absent from CI. An entry in `CHECKS` would either make CI
  always red or skip for missing data — exactly the silence against which the metric exists. The
  `SessionStart` hook takes the gate role: it runs itself every session and prints **only** an anomaly;
  `tests/test_token_trend.py::TestHookRegistration` guards against losing hook registration (without
  it, the script would repeat eval's fate from #361 — a metric that nobody runs). Tests cover pure
  logic (parsing, aggregation, ledger, detector), **both output formats**, and `main()` in both modes
  on a substitute directory; only `transcript_dir()` remains uncovered — an upstream slug rule
  testable only by actual run. Its failure is not silent: if `~/.claude/projects` exists but lacks
  our directory, the hook prints `transcripts_not_found` rather than remaining silent. Revisit if a
  shared development-telemetry carrier appears that CI can read.

- **AK. The second review-gate carrier has not been verified by a live run (#478).** Carrier 2 —
  Codex code review through the GitHub integration — is structurally covered (`TestFallbackCarrier`:
  step order, launch condition, verdict tied to head SHA, bounded wait, output name, producer
  attribution, red gate for a missing carrier) and behaviourally covered by its adapter
  (`tests/test_request_codex_review.py`: whose review counts as a verdict, selection by head SHA,
  state conversion to the outcome dictionary, round-trip payload through the enforcement script).
  Guards **do not** prove two things, both on the other side of the contract: (1) that Codex answers
  an `@codex review` posted by `github-actions[bot]`, not a human, at all; and (2) that it sets the
  review state requested by `AGENTS.md` § Code Review Rules — public documentation says only that it
  raises P0/P1 findings in GitHub, so its bar is already above our coverage-first policy. **The skip
  is conscious, not silent:** both failures look like "no verdict" → empty payload → red
  `agent-review` with an explicit `::warning::` identifying who did not answer. The unverified branch
  cannot weaken the gate; it can only not work, which appears as a red check rather than a green PR
  without review. It is the same class as **AD** (the network half of branch protection): testable
  only by a live run against an external service. **Closure trigger: the first run where Codex leaves
  a review on the head SHA**; a link to the run and the review goes into the `## Agent record` issue,
  and this entry is removed. Full decision:
  [ADR-0003](../adr/0003-second-carrier-for-the-required-review-gate.md).

- **AL. Guards do not prove that carrier-1 review actually runs under the workflow token (#483).**
  Only the input (`github_token: ${{ github.token }}`) and removal of the carve-out are structurally
  pinned; whether upstream then does not validate the workflow and whether the action has rights to
  its PR records under that token is the other side of the contract, testable by a live run. It is
  the same class as **AK** and **AD**. **The skip is not silent, but the signal is not a red check:**
  if former behaviour returns, an empty carrier-1 outcome gives `valid=false`, which launches carrier
  2 (#478), and its `clean` makes the check green. Regression is visible because
  `Classify review outcome` prints `valid=false` and `Codex review` **runs** (log and
  `## Agent record`); `agent-review` becomes red only if carrier 2 also does not answer. Missing
  permissions appear as a write error in the step log. **Closure trigger: a run on the PR that makes
  this change**; it is controller-shaped by construction, and its log (`valid=true`, executed
  `Enforce Claude review outcome`, summary with `Reviewed head SHA:`) enters `## Agent record`.
  Full decision: [ADR-0004](../adr/0004-controller-pr-review-runs-on-the-workflow-token.md).
- **AM. No guard on *which commands* the navigation policy covers (#485).**
  `scripts/navigation_policy.py` decides that a shell stage reads the filesystem and denies it
  with the replacement call named. Its **behaviour** is tested (`tests/test_navigation_policy.py`:
  file-operand forms denied, pipe stages allowed, `sh -c` unwrapped, unparseable input fails
  open), and so is its **wiring** — including the negative invariant that no `permissions.deny`
  entry shadows the hook, since a static rule matches first and would swallow the message.
  What is deliberately *not* pinned is the membership of `_RULES`: `awk` and `wc` are outside it
  (no tool replaces line counting; `awk` was never measured), and adding or dropping a command
  costs tokens and nothing else, which
  [the rule](testing.md#rule-when-a-test-is-not-worth-writing) routes to a forcing function
  rather than a guard test. Do not reopen the membership list as an anti-drift ratchet.

- **AN. Offline tests cannot prove Claude Code telemetry delivery or Grafana dashboard import
  (#471).** `tests/test_claude_otel_assets.py` guards the values-free setup template, captured
  signal references, dashboard JSON structure, required decision groups, and absence of bespoke
  automation. It cannot authenticate to the maintainer's Grafana stack, prove that Claude Code's
  bundled exporter still maps headers and metric temporality correctly, observe backend name
  translation, or execute Grafana's import/query path. Those are credentialed external contracts.
  The accepted boundary is a manual live check from
  [`operations.md`](operations.md#verify-and-import): first metrics and logs exports both succeed,
  destination queries return both signal types, content fields remain redacted/absent, and the
  dashboard imports. **Revisit trigger:** a provider changes the exporter or OTLP mapping, the
  dashboard import fails, or a captured signal/attribute disappears. Update the values-free
  catalogue only from a new live capture; never make a missing dimension pass as zero.

**Scope-skip (can't run without live credentials) — see [What does NOT get tested](testing.md#what-does-not-get-tested-in-this-repo):**

- **J. Concurrent state — true *parallel* execution is a non-target** (serial daily cron, no
  overlap → a crash/concurrency simulation would be work-for-work). Realistic failure modes
  *are* covered: rerun-after-crash idempotency (dedupe index re-read) and notify-then-store
  ordering (a failed-notify item isn't stored → retried next run, no silent loss).
  Cell-level partial `gspread` writes are scope-skip (live credentials).

## Modules without dedicated tests

| Module | Reason | Mitigation |
|---|---|---|
| `youtube.py::Youtube` (live-client wrapper: `__init__` + `search_candidates` method) | Requires live YouTube API (`build()` + `API_KEY`) | Pure retrieval `search_candidates(client, profile)`/`_search_one` **is** directly tested (`test_youtube.py::TestSearchCandidates` via an injected fake `client`, the DI boundary, #140); only the thin live-`build()` wrapper is untested. Production uses `search_candidates` + `HeuristicStrategy`; the module has no standalone `get_trailer_url` (#144) |
| `tmdb_trailer.py::TmdbClient` (`resolve`/`_get`/`_find_movie_id`) | Requires live `TMDB_TOKEN` + network — retrieval boundary (DI, mirror of `youtube.py`) | Pure selection `pick_trailer` **is** directly tested (`test_tmdb_trailer.py`, 7 cases); only the network boundary is untested, same §II precedent as `youtube.py`'s live-client wrapper (#329) |
| `text_utils.py` | Small utility | Indirect coverage via `test_kinozal_pipeline.py::TestTitleYearMatches` |
| `*_pipeline.py` `if __name__ == "__main__"` blocks | CLI wiring of live `gspread`/env — needs live credentials | **Scope-skip**, guarded two ways since the package migration ([#237](https://github.com/ekolvah/kinozal_scraper/issues/237)): (1) **mypy is load-bearing** — `pip install -e .` + native package resolution means mypy type-checks the `__main__` block (incl. its `from kinozal_scraper.X import …`), catching a mis-wired/mis-renamed import that the import-only `test_package_importable.py` cannot; (2) the daily cron as §IV «cron = E2E smoke». The large uncovered blocks in `coverage.py` are these runners, not logic gaps |
| Package import-resolution & repo layout | A module failing to resolve as `kinozal_scraper.X`, or source drifting back to a flat `src/*.py` layout | `test_package_importable.py::TestPackage` (all modules import as `kinozal_scraper.X`); `test_repo_layout.py::TestLayout`. (The #237 B1 empty-/nested-scan guard moved off the retired `test_check_headers.py` — [#253](https://github.com/ekolvah/kinozal_scraper/issues/253) replaced `check_headers.py` with ruff `D100`/`D104`/`D419`; the "mis-pointed/empty `src/` scanned nothing" failure mode is now subsumed by these two guards, which fire strictly harder — 17 hard-coded imports + layout-drift — than the old zero-file check) |
| Telethon session rotation (mint a `StringSession`, set the secret, revoke the old session in the Telegram app) | Interactive login against live Telegram, performed by an operator roughly once per incident | **Scope-skip** (#386, replaces the `crypto.py` glue entry that left with the module): there is no automatable surface — the code side *is* covered (`require_env` rejects an empty secret, `TestTelethonReaderAuth` pins StringSession-only auth and a fail-fast on a revoked session). The recipe lives in [operations.md](operations.md#minting-a-new-telethon_session); deliberately a doc snippet, not a script — a once-a-year human interactive is not the deterministic pipeline step "scripts > instructions" targets |
