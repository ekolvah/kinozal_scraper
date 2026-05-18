# Quickstart: verify GitHub Trending source end-to-end

**Feature**: 001-github-trending  
**Date**: 2026-05-18  
**Audience**: developer verifying the feature locally before merge.

This is the manual happy-path + failure-mode verification. Automated coverage lives in `tests/test_github_trending_pipeline.py` and `tests/test_e2e_github_trending.py`; run those first (`python scripts/ci_check.py`). The steps below cover what the test suite can't easily prove: real network, real Sheets, real Telegram, ordering between separate Python processes.

## Prerequisites

- Branch `codex-issue-60-github-trending` checked out and the full PR applied locally.
- `.env` with: `CREDENTIALS`, `SPREADSHEET_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GITHUB_TOKEN`, `GH_TOP_LIMIT`.
- A scratch Google Sheets spreadsheet (do not use the production one). Set `SPREADSHEET_URL` to it.
- A scratch Telegram bot + chat to receive messages.

## Step 1 — config validates at startup

```bash
python pipeline_config.py
```

Expect: silent success (no traceback). This proves `validate_sources_config()` accepts the new `github_trending` entry.

Now break it deliberately:

```bash
# Edit sources.json, remove the "row_selector" line from the github_trending entry.
python -c "from pipeline_config import load_sources_config; load_sources_config()"
```

Expect: `ConfigError: Source 'github_trending' has type='html' but no 'row_selector' field`. **Restore the file** afterwards. (Satisfies FR-006, SC-005, US3 #3.)

## Step 2 — fixture-based unit run

```bash
pytest tests/test_github_trending_pipeline.py -v
```

Expect: all tests green. Each test corresponds to one acceptance scenario in `spec.md`. If a test is red, do not proceed — the rest of this quickstart will not produce reliable signal.

## Step 3 — live extraction smoke test

```bash
pytest tests/test_e2e_github_trending.py -v
```

Expect: passes (network-dependent). Asserts ≥1 row with non-empty title and `https://github.com/...` URL. If it fails with `requests.RequestException`, you're offline — skip and rerun later. If it fails with `0 items` and you're online, the GitHub trending HTML has likely drifted — open an issue.

## Step 4 — end-to-end: write-before-notify

This is the hardest path to prove automatically; do it by hand once per PR.

1. Empty the `github_projects` tab of your scratch Sheets (manually delete all rows except the header).
2. Run: `python github_trending_pipeline.py`.
3. Observe the Telegram bot: should receive up to 25 messages (one per trending repo), each formatted via the template.
4. Observe Sheets: `github_projects` tab populated with one row per sent message. **Row count must equal Telegram message count.**
5. Pick one repository's URL from a Telegram message and grep for it in the Sheets row's URL column — they must match exactly.

If row count > message count → write-before-notify is broken (storage wrote, notifier failed silently). Open a bug.
If row count < message count → notify-before-write is broken (messages sent that aren't deduped). Open a bug; this is a Principle III violation.

## Step 5 — cross-source dedupe (the headline requirement)

This proves FR-005 (shared dedupe between `github_new_popular` and `github_trending`).

1. Empty the `github_projects` tab again.
2. Run **only** `python json_pipeline.py`. Wait for it to finish. Note how many Telegram messages arrived (call it `N`).
3. Note the `dedupe_key` column of the rows it wrote (they look like `"owner/repo"`).
4. Now run `python github_trending_pipeline.py` immediately after. 
5. Count Telegram messages from step 4 (call it `M`).
6. Inspect: any repo that appeared in both `github_new_popular`'s output (step 2) AND on the trending page during step 4 — should produce zero new messages in step 4. The dedupe key is the same string.
7. If step 4 produced a duplicate message for a repo already notified in step 2, FR-005 is broken — the most likely cause is a mismatch in dedupe key format (look for leading `/`).

## Step 6 — visibility on zero-row extraction (Principle IV)

Force a zero-row extraction:

1. Temporarily edit `sources.json`'s `github_trending.row_selector` to a bogus value like `"article.NotAClass"`.
2. Run: `python github_trending_pipeline.py`; capture the exit code with `echo $?` (POSIX) or `$LASTEXITCODE` (PowerShell).
3. Expect exit code `1`. Expect an `ERROR` line in the output. Expect zero Telegram messages.
4. Restore `sources.json`.

If exit code is `0`, US3 #1 / FR-007 is broken — failures will hide in production cron logs.

## Step 7 — partial row visibility

This step requires a fixture with a partial row, because the real trending page rarely has them. The fixture-based test `test_github_trending_partial_row_still_emitted` covers it; if you want a manual check:

1. Add a temporary row to `tests/fixtures/github_trending/trending_daily.html` that has no `<p>` description.
2. Run a small Python snippet that loads the fixture and calls `extract_from_html(...)`, then the new pipeline's normalisation.
3. Verify the item is **still in `result.items`**, its `description` is `""`, and a `WARNING` is logged.

Restore the fixture afterwards.

## Cleanup

- Empty the scratch `github_projects` tab.
- Restore `sources.json` if you modified it.
- Revert any temporary fixture edits.
- (Do NOT commit any scratch credentials or modified fixtures.)

## Acceptance summary

| Spec item | Verified by |
|---|---|
| FR-001 / US1 #1 | Step 3 (live), Step 4 (run end-to-end) |
| FR-002 | Step 3 (live row content) |
| FR-003 | Step 4 (Telegram delivery) |
| FR-004 / Principle III | Step 4 (row count = message count) |
| FR-005 / US2 #2 | Step 5 (cross-source dedupe) |
| FR-005a / US2 #3 | Step 5 (order: json then trending) |
| FR-006 / SC-005 / US3 #3 | Step 1 (config validation) |
| FR-007 / US3 #1 / Principle IV | Step 6 (non-zero exit) |
| FR-008 / US3 #2 | Step 7 (partial row emit) |
| FR-009 | Step 4 (visual review of Telegram message) |
| FR-010 / US1 #3 | Step 4 (Telegram message count ≤ 25) |
