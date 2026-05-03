# Issue #2: generic fetch, extract, and normalize core

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/2

## Summary

Build the pure core of the declarative pipeline. This layer turns already
fetched source payloads into normalized item dictionaries and quality-check
results.

This issue must not call Telegram, Google Sheets, YouTube, or real production
sources from the core logic. It should be safe to merge because `scraper.py`
continues to use the existing implementation until a later feature-flagged
integration.

## Implementation changes

- Add a new module for the pipeline core, for example `generic_pipeline.py`.
- Define a normalized item shape used by later issues:
  - `dedupe_key`
  - `title`
  - `url`
  - `description`
  - `metric`
  - `image_url`
  - `source_id`
  - `raw`
- Keep fetch orchestration separate from extraction:
  - fetching may exist as a small adapter function;
  - extraction and normalization must be testable with in-memory payloads.
- Support v1 source types:
  - `json`
  - `html`
- Support declarative field mapping from issue #1 config:
  - JSON paths for JSON payloads;
  - CSS selectors and attribute extraction for HTML payloads.
- Add source-level quality checks:
  - HTTP success with zero mapped items is a quality failure;
  - missing required `dedupe_key` or `title` blocks that source from sending;
  - optional fields may be empty.
- Return structured results instead of raising for normal data-quality failures:
  - `items`
  - `errors`
  - `warnings`
  - `source_id`

## Boundaries

- Do not integrate this core into the scheduled `scraper.py` path in this issue.
- Do not implement Telegram sending or Google Sheets writes here.
- Do not depend on live GitHub, SteamSpy, or Kinozal responses in tests.
- Do not add large parsing dependencies unless the existing stack cannot support
  the needed mapping. Prefer `json`, `urllib.parse`, and existing `bs4`.

## Test plan

Add tests with `unittest` for:

- extracting items from a small synthetic JSON payload;
- extracting items from minimal synthetic HTML with CSS selectors;
- required field validation;
- optional field absence;
- normalized dedupe key trimming;
- limiting mapped items;
- quality failure when HTTP status is success but extraction returns no items;
- quality failure when most items miss required fields.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- `pipeline_config.py` from issue #1 owns config loading and macro expansion.
- This issue owns source-agnostic extraction and normalization only.
- Each source PR remains `main`-safe: merging it must not change scheduled bot
  behavior unless explicitly enabled later.
