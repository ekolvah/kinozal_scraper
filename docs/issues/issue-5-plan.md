# Issue #5: port Kinozal top movies to the generic pipeline

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/5

## Summary

Move the existing Kinozal top movie workflow to the declarative pipeline while
preserving current user-visible behavior. Kinozal remains an HTML source because
its RSS feed shows latest releases, not top items.

This is the first risky integration issue, so it must be guarded by a feature
flag and keep the old path as the default until verified.

## Implementation changes

- Add a Kinozal source entry in `sources.json` with `type: html`.
- Move current Kinozal URL handling and selectors into config.
- Preserve current prefix behavior for relative Kinozal URLs.
- Preserve movie title cleanup before YouTube trailer lookup.
- Preserve poster/photo notifications for movies.
- Add an environment flag:
  - `USE_GENERIC_KINOZAL=false` by default.
- In `scraper.py`, choose:
  - old `MovieScraper` path when flag is false;
  - generic pipeline path when flag is true.

## Compatibility rules

- Default scheduled behavior must remain old Kinozal until the flag is enabled.
- Existing Google Sheets movie state must keep deduping correctly.
- A Kinozal mapping failure must not stop events scraping or Telegram channel
  summaries.
- HTML fixture tests should be minimal synthetic examples, not false confidence
  snapshots of live Kinozal pages.

## Test plan

Cover:

- config-driven Kinozal item mapping from minimal synthetic HTML;
- relative URL prefixing;
- title cleanup for trailer search;
- old-path selection when `USE_GENERIC_KINOZAL` is false;
- generic-path selection when `USE_GENERIC_KINOZAL` is true;
- source failure isolation.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- Kinozal RSS is not used for top movies.
- This issue may touch `scraper.py`, but only behind a default-off feature flag.
- The final production switch happens in a later PR after manual verification.
