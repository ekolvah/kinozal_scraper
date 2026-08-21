# Coverage gaps: ingestion and retrieval

**Question this document answers:** Which accepted test gaps concern source ingestion, retrieval, and transport.

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
