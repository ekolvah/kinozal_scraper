# Issue #4: resilient Telegram notifier queue

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/4

## Summary

Add a reusable Telegram notification layer that can send queued items safely,
handle Telegram rate limits, and report exactly which items were confirmed sent.

The notifier should be usable by all future declarative sources, while the
existing bot behavior remains unchanged until integration issues opt in.

## Implementation changes

- Add a notifier module, for example `telegram_notifier.py`.
- Keep current `BOT_TOKEN` and `BOT_CHATID` environment variables.
- Add a queue-oriented API:
  - accept normalized items and a message template;
  - send sequentially;
  - return `sent_items` and `failed_items`.
- Add field-aware formatting:
  - `text` fields are MarkdownV2 escaped;
  - `url` fields are validated and not blindly escaped;
  - `number` fields are stringified without Markdown emphasis semantics.
- Handle Telegram HTTP 429:
  - read `retry_after` from JSON response when present;
  - fall back to `Retry-After` header;
  - sleep and retry within a bounded retry limit.
- Surface HTTP 400 formatting errors as item failures, not as successful sends.

## Reliability rules

- Sheets persistence must use only `sent_items`.
- Failed items stay out of Sheets so they can retry on the next run.
- Message sending should include a small configurable delay between items.
- Error alerts should be compact enough not to create alert spam during source
  outages.

## Test plan

Use fake HTTP/session objects; do not call Telegram in tests.

Cover:

- MarkdownV2 escaping for text fields;
- preserving valid URLs;
- rejecting invalid URLs;
- successful queue accounting;
- HTTP 429 retry with JSON `retry_after`;
- HTTP 429 retry with header fallback;
- HTTP 400 item failure;
- mixed success/failure queue results.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- Existing movie poster sending can remain in `scraper.py` until Kinozal is
  migrated.
- This issue introduces the reusable notifier but does not force scheduled runs
  to use it yet.
- MarkdownV2 is the target parse mode for new declarative text notifications.
