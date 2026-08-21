# Coverage gaps: runtime behavior

**Question this document answers:** Which accepted test gaps concern runtime orchestration and configured source behavior.

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
