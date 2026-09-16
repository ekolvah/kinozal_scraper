---
status: "accepted"
date: 2026-09-12
decision-makers: ekolvah
---

amended: 2026-09-16 (#586) — the gate changed shape (path `/verification`, cookie `challenge2=<token>`,
max-age 86400) four days after the decision; detection moved from the final URL to the body marker. The
decision itself (replay, not browser) is unchanged.

# Cross the jumpingcrab cookie gate by replaying its cookie, not by driving a browser

## Context and Problem Statement

The anonymous kinozal primary named by `KINOZAL_URLS` is `kinozal.jumpingcrab.com`: `kinozal.tv` no longer
resolves, the other fronts (`kinozal.guru` / `kinozal.me`) sit behind a Cloudflare managed challenge, and the mirror fallback of
[`pipeline.md`](../architecture/pipeline.md#kinozal-mirror-fallback) is authenticated, so it cannot replace the
anonymous top list. jumpingcrab fronts every HTML page with its own JS cookie gate:
`GET /top.php` → `302 /challenge-verification?next=/top.php` → `200` with a 741-byte page whose script runs
`document.cookie = "challenge1=1; …"` and reloads `next`. The same gate fronts `details.php`; poster bytes under
`/i/poster/` are not gated.

`fetch_html` followed the redirect, `raise_for_status()` passed the `200`, and the gate page reached the extractor
as if it were the listing. The symptom was a bare `kinozal_movies: extraction produced zero items` Telegram
alert — a false success of the class §IV forbids: the transport reported a page, the operator saw no
host, no status, no title.

## Decision Drivers

* Minimise bespoke code and future support (goal function): no new dependency, no new runtime, nothing that
  needs its own upkeep when the gate changes.
* The gate must fail **visibly** with evidence (`describe_block`), never as a `200` that looks like HTML (§IV).
* Runner-measured, not guessed (§V): both candidates were run from GitHub Actions
  (run 34680320572 on `probe/kinozal-egress`), because the gate's behaviour from datacenter egress is the
  only behaviour that matters for cron.
* [ADR-0002](0002-soldout-cloudflare-spread-retries.md) already excludes paid egress and bypass services.

## Considered Options

* **A — cookie replay via `curl_cffi`**: read the cookie name/value the gate page would set, replay the same
  GET once with that cookie.
* **B — Playwright headless browser**: let the script run and reuse the browser's cookie jar.
* Paid egress / challenge-bypass APIs — excluded by ADR-0002, not measured.

## Decision Outcome

Chosen option: **A — cookie replay**, because on the runner it crosses the gate in 1.6 s with zero new
dependencies, while B needs a 23 s browser install (656 MB) plus 10.3 s per crossing and exposes
`navigator.webdriver=True`, which the gate is free to start checking. A gives the same result at a fraction of
the cost and leaves nothing to maintain beyond one regex.

Implementation (`kinozal_pipeline._cross_gate`): anonymous primary HTML is fetched through `fetch_page`
(the `Response`-returning sibling of `fetch_html`), the gate is detected by the **body marker**
`document.cookie = "name=value"` (`_COOKIE_RE`) — the one thing both recorded shapes share and no real
listing/details page contains — the cookie is replayed **once**, and a reply that still carries the marker
raises `ChallengeGateError` with `describe_block` evidence. The final URL is the **secondary** signal: a
reply that landed on a different *path* than requested without a cookie to replay (a gate whose script we
do not recognise, a login or deleted-torrent redirect) is the same error, never a body handed to the
extractor. The original detector keyed off the final URL containing `/challenge-verification`; that path
lasted four days (see the amendment). That error is a primary failure like a timeout: `fetch_listing`
falls through to the mirror, and the alert names both hosts.

### Consequences

* Good, because the gate page cannot reach the extractor: a bare "zero items" is replaced by an error naming
  host, status, length and title.
* Good, because a healthy run pays no login — the mirror path is untouched.
* Bad, because the cookie is not kept between calls (`fetch_page` builds a fresh session each time), so
  **every** primary HTML request — the listing and each `details.php` the genre filter opens — pays the
  302 → gate → replay round trip: three wire requests per page (`GET` → `302`, `GET` gate, `GET` with cookie),
  `3 × (1 + N)` instead of `1 + N`. Accepted for now because `N` is
  the handful of new items per run; caching the cookie for the run is the first lever if the host starts
  rate-limiting the gate.
* Bad, because a gated run with a dead mirror still pays one doomed `login()` before the alert; the evidence
  is kept (`primary failed (challenge gate …); mirror … also failed (mirror login failed: …)`), the cost is
  one request.
* Bad, because the replay is tied to the current gate **mechanism** (a static cookie assigned in
  `document.cookie`). Two kinds of change, two exits. A **shape** change — path, cookie name/value, max-age —
  is a detector correction inside this ADR, exercised once on 2026-09-16 (#586: `/challenge-verification`
  + `challenge1=1` → `/verification` + `challenge2=<token>`); the detector now reads the body marker so the
  shape can move again without code. A **mechanism** change — computed cookie, Turnstile — leaves
  `_COOKIE_RE` empty (or the replay lands on the gate again) and the run fails with evidence; this ADR says
  **measure first with the probe workflow, do not retry harder or add a loop**; that is a new decision, not
  a tuning of this one.

### Confirmation

`tests/test_kinozal_pipeline.py::TestChallengeGate` (crossing, no-cookie gate at any path, gate after
replay, body-marker detection against both recorded shapes — `tests/fixtures/kinozal/jumpingcrab_*.html`
for 2026-09-12 and `jumpingcrab_*2.html` for 2026-09-16) and `tests/test_http_fetch.py::test_fetch_page_*`
(shared request kwargs, cookies forwarded only when given). `tests/test_generic_pipeline.py::
TestExtractFromHtml::test_zero_items_error_carries_page_evidence` pins the second line of defence: a gate
page that does reach the extractor is reported with `len=`/`title=`, not as a bare "zero items".
The live check is `scripts/capture_kinozal_fixture.py` against the top URL from the runner — it goes
through `Kinozal.fetch_details`, so it crosses the gate or fails with the same evidence. Note that it writes
the decoded page re-encoded as UTF-8, while the shape-1 listing fixture is the raw cp1251 bytes recorded by
the probe run (the shape-2 one is UTF-8 text); the test helpers decode each the way it was saved.

## Pros and Cons of the Options

### A — cookie replay via `curl_cffi`

* Good, because 1.6 s on the runner, no dependency, no install step, no browser binary in the job.
* Good, because the crossing is one plain GET with a cookie — observable in the same logs as every other request.
* Neutral, because it encodes one assumption (static cookie in `document.cookie`); the assumption is explicit and
  its failure is loud.
* Bad, because a smarter gate (computed cookie, Turnstile) needs a new decision.

### B — Playwright headless browser

* Good, because it runs whatever script the gate ships, including a future computed cookie.
* Bad, because 23 s install + 656 MB per job and 10.3 s per crossing for a page that needs one cookie.
* Bad, because `navigator.webdriver=True` is visible to the gate; hiding it is the bypass path ADR-0002 rejects.
* Bad, because a second HTTP stack (browser cookie jar vs `curl_cffi`) doubles the transport surface to support.

## More Information

Evidence and timings: issue #583 and the `probe/kinozal-egress` workflow runs. The operational description
lives in [`pipeline.md`](../architecture/pipeline.md#kinozal-mirror-fallback) (§ Primary cookie gate); the
soldout precedent for "measure, then change time — not egress" is [ADR-0002](0002-soldout-cloudflare-spread-retries.md).
