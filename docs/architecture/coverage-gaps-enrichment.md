# Coverage gaps: enrichment and selection

**Question this document answers:** Which accepted test gaps concern enrichment, model calls, and content selection.

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
