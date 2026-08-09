---
status: "accepted"
date: 2026-08-01
decision-makers: ekolvah
---

# Soldout overcomes Cloudflare by spreading attempts over time, not changing egress

## Context and Problem Statement

`soldoutticketbox.com` is behind Cloudflare, which blocks GitHub Actions datacenter IP ranges. Measurement found
that the same code using the same `curl_cffi` returns 200 from a residential IP and 403 from CI;
`impersonate` targets `chrome`/`chrome124`/`chrome131` are equivalent; there is no JS challenge; failures and rare
successes come from seven different datacenters. Thus **range reputation**, not what we send, is blocked; neither
changing the browser profile nor a headless browser works here.

Scheduled nightly runs from 20.07–31.07.2026 were red on this step 12 times in succession.

Question: how should this be fixed when there is nothing to change in the request itself?

## Decision Drivers

* **Paid egress is unconditionally excluded by the owner**—neither residential proxies nor a managed
  bypass API are considered, regardless of effectiveness.
* **Load on a third-party site**—news appears about weekly, and hammering the source to speed delivery is disproportionate.
* **The required SLA is not daily.** Events stay on the page until the event itself, so they cannot be missed in
  principle, only delivered later. A delay of several days is acceptable.
* **Simplicity**—the solution must not introduce state between runs, a new workflow, or alerting redesign.

## Considered Options

* Spread attempts within one daily run
* More runs per day (a separate workflow with frequent cron)
* Different egress: residential/ISP proxy or managed bypass API
* Local run from a home residential IP
* Drop the source

## Decision Outcome

Chosen: **spread attempts within one run**: 24 attempts with a fixed 720-second delay
(a ~4.6-hour window) instead of 4 attempts in ~7 seconds. The run remains once per day in the same
`run-script.yml`, with no state between runs.

The decision rests on this key observation: **attempts separated by 60 seconds behave independently**, while
attempts compressed into 7 seconds do not. Both `200→403→403` and `403→403→200` occurred from one IP during a
run, while production’s 1/2/4-second delays hit the same Cloudflare decision and cost **one** attempt. This
explains 12 consecutive red days: the source effectively rejected 12, not 48, requests.

### Where 24 and 720 come from

The intersection of three constraints, not an optimum on one:

* the delay is materially above the measured 60 seconds at which independence was established;
* the window (~4.6 hours) stays under GitHub Actions’ hard 6-hour job limit. The limit is measured from **job**
  start, not the step; a job killed by it is cancelled rather than failed, so the fallback alert’s `if: failure()`
  does not run. Therefore `timeout-minutes: 300`: above the policy’s 288-minute worst case and 60 minutes below
  the limit, so failure arrives through its own step rather than job death;
* density is 24 requests per day—the exact empirically tested measurement density. Total site load **falls**:
  the measuring tool that made its own 24 daily requests was removed.

A dense alternative (55 attempts with 60-second delays in one hour) gives a better modeled number, but
quadruples load, narrows the window, and enters an unmeasured profile—where CF-`1015` risk begins. If it
appears, it will be visible: `_CF_CODE_RE` in `http_fetch.py` extracts `cf-code` to the log.

**If the fix fails, adjust window width before density**—the observed failure mode is “an entire bad day,”
against which spread matters more than frequency.

### What is observation and what is model

This boundary is deliberate because these exact figures will be cited.

**Observations:** attempt independence at a 60-second delay; with 24 measurements daily, delivery occurred
on 4 of 6 full days; the maximum gap between successes was 50 hours; failure arrives in 0.04–0.18 s and
success in 1.3–2.6 s (the decision is made at the edge).

**Model:** the ≈7% single-attempt success probability (8 of 115) rests on an outlier—one run produced 3 of
the 8 successes, on the day production turned green; without it, ≈4.5%. Data do not support, and apparently
contradict, independence **between days**: two days gave 0 of 24 with full coverage. Thus “probability of an
empty week” estimates are an optimistic upper bound, not a forecast, and the SLA argument does not rely on
them: it relies on the observed 50-hour gap against weekly news frequency.

### Why no jitter

The grid is fixed. Start time already varies: GitHub delays cron by tens of minutes, enough to prevent
attempts on different days from landing in the same minutes. In return, a deterministic interval provides
reproducibility when reading logs: an attempt number immediately identifies its time.

### Repealed preregistered rule

Before collecting data, a decisive rule was recorded: “if any day has 0 successes out of 24, another egress
is needed.” The data produced exactly that result, and the rule was **consciously repealed**—not because the
result was inconvenient, but because it measured a requirement that does not exist. “≥1 success every day”
assumes a daily SLA, but that SLA is invented: news appears weekly and remains on the page. The source of the
change is domain knowledge received after measurement, not the data themselves. It is recorded here to distinguish it from fitting.

### Consequences

* Good: the change fits one retry policy, one invocation, and moving a step; no state, new workflow, or alerting change.
* Good: general `retry_antibot_http` policy is untouched—the conclusion comes from one host, with no basis to
  extend it to kinozal/github/steam.
* Good: third-party-site load decreases relative to the measurement period.
* Bad: the step occupies a runner for up to ~4.6 hours and delays job output and fallback alerting by those hours.
  It is therefore last; otherwise it would delay delivery from other sources.
* Bad: for part of the day the block is not penetrated at all, and red soldout remains expected. There is no
  “no success for N days” detector; the accepted gap is recorded in [`coverage-gaps.md`](../architecture/coverage-gaps.md).
* Bad: increasing the share of delivering days makes “notification sent without poster” common for the first time,
  while deduplication makes the loss irreversible. This is also recorded in `coverage-gaps.md`.
* Bad: **the policy does not retry transport errors**—by design, its predicate accepts as transient only HTTP
  responses with a code from the set, so one TCP reset or DNS flicker on the first attempt ends the entire window
  and costs a delivery day. The behavior is not new, but its cost grew 24-fold. It is consciously retained:
  measurement says Cloudflare returns exactly 403, while extending the predicate to network errors is a decision
  with a different blast radius (it would affect all sources at once) and needs a measurement we do not have.

### Confirmation

Tests pin the policy numbers rather than describing them:
`test_http_retry.py::TestPatientPolicy` (24 attempts, 720-second delays, same code set),
`test_http_fetch.py::TestPatientHtml` (24 attempts on 403, shared request kwargs, posters stay on fast
transport), `test_workflow_isolation.py::TestSoldoutStepPlacement` (step last, timeout under job limit,
daily cron unchanged).

The post-merge success criterion is **≥1 green `Run soldout pipeline` step in 7 days**, read with
`gh run list --workflow=run-script.yml`. No separate measuring tool is needed: `_get_once` logs `cf-ray`
and `cf-mitigated` for each of 24 attempts, so the production log provides the same diagnosis. If the criterion
is not met, a local run with a residential IP is opened.

## Pros and Cons of the Options

### Spread attempts within a run

* Good, because it fixes the measured cause—correlation of compressed attempts—not a symptom.
* Good, because it is free and introduces no new moving parts.
* Neutral, because it does not guarantee delivery on a particular day—but the weekly SLA does not require that.
* Bad, because it occupies a runner for hours.

### More runs per day

* Good, because it spreads attempts even wider—across all days, not one window.
* Bad, because it requires a separate workflow: the whole `run-script.yml` cannot run frequently because it has
  neighboring sources and Gemini quota, which has already reached its limit.
* Bad, because it delivers the same result as the selected option at higher infrastructure cost.

### Residential proxy or managed bypass API

* Good, because it is the only option with a proven automation 200.
* Bad, because it is paid—unconditionally excluded by the owner.
* Bad, because it introduces a long-lived secret and external dependency.

### Local run from a home IP

* Good, because the residential IP was measured to work.
* Neutral, because it remains a fallback if the selected option does not deliver.
* Bad, because delivery depends on a powered-on computer and observability falls—there are no longer Actions logs.
* Bad, because a self-hosted runner on a public repository is a known vulnerability (a fork PR executes arbitrary
  code), so the script would have to run outside Actions.

### Drop the source

* Good, because it honestly closes the question and removes noise.
* Bad, because the source works, and delivery costs runner-wait hours rather than money.

## More Information

Rejected by measurement and **do not reopen**: pinning the `impersonate` version, a Playwright/headless browser
(there is no JS challenge and profiles are equivalent), and the hypotheses “a particular datacenter,” “time of
day,” and “event type `schedule` versus `workflow_dispatch`” are all disproved by run data.

The operational side is in [`operations.md`](../architecture/operations.md); the code policy is
`retry_antibot_patient` in `http_retry.py`.
