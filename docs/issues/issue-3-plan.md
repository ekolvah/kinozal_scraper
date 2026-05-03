# Issue #3: Google Sheets tab storage and batch append

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/3

## Summary

Centralize Google Sheets state handling for all declarative sources. The storage
layer reads existing dedupe keys, creates missing tabs, and appends confirmed
notifications in batches instead of writing after every item.

This issue should be implemented as a reusable storage layer without switching
the scheduled bot to the new pipeline yet.

## Implementation changes

- Add a storage module, for example `sheets_storage.py`.
- Keep Google authorization compatible with the current `CREDENTIALS` secret and
  spreadsheet URL.
- Add tab-aware operations:
  - get or create worksheet by tab name;
  - ensure header row exists;
  - read full dedupe key column `A:A`;
  - append rows for successfully sent items.
- Use a stable v1 row schema:
  - `dedupe_key`
  - `title`
  - `url`
  - `metric`
  - `source_id`
  - `notified_at`
- Use one append operation per source tab, or one grouped batch operation if it
  stays simpler and reliable with `gspread`.
- Preserve old Kinozal worksheet behavior until a later integration issue moves
  it to named tabs.

## Failure handling

- If a tab is missing, create it and write headers before appending.
- If reading keys fails, fail that source before Telegram sending starts.
- If appending sent rows fails, surface a clear error so the run can alert
  without pretending persistence succeeded.
- Never write rows for items that were not confirmed sent by Telegram.

## Test plan

Use fake worksheet/client objects; do not call Google APIs in tests.

Cover:

- empty tab handling;
- header creation;
- reading `A:A` into a normalized `set`;
- existing key lookup;
- row construction from normalized items;
- batch append payload grouping by tab;
- append failure propagation.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- Full-column key reads are acceptable and simpler than bounded recent-window
  reads.
- Google Sheets is the source of notification state for v1.
- This issue creates infrastructure only; production behavior remains unchanged
  until the final feature-flagged switch.
