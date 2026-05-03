# Issue #8: GitHub Action and README for new sources

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/8

## Summary

Wire the declarative pipeline into project documentation and scheduled checks.
This issue is the final rollout-oriented task: it should make behavior visible,
configurable, and safe to operate.

## Implementation changes

- Update GitHub Actions variables used by the workflow:
  - `GITHUB_TOP_LIMIT`
  - `STEAM_TOP_LIMIT`
- Add unit-test execution before production script execution:
  - `python -m unittest discover`
  - then `python scraper.py`
- Update `README.md` with:
  - source overview;
  - Google Sheets tab names;
  - required secrets and variables;
  - feature flags;
  - Telegram alert behavior;
  - difference between GitHub new popular repos and exact GitHub Trending.
- Document operational defaults:
  - GitHub limit default `10`;
  - Steam limit default `10`;
  - new sources disabled until intentionally enabled;
  - Kinozal generic path gated until verified.

## Rollout rules

- Keep scheduled production deployable after this PR.
- If this PR enables any new source by default, it must be a tiny explicit
  switch with prior verification from earlier PRs.
- Do not mix README/workflow cleanup with unrelated scraper refactors.
- If tests fail, the scheduled run should stop before sending Telegram messages.

## Test plan

Cover:

- workflow command order is tests first, script second;
- documented env vars match config macros;
- README mentions all tabs and feature flags;
- scheduled behavior remains clear for a maintainer changing only GitHub Actions
  variables.

Manual verification before enabling new sources:

- run unit tests;
- run the script with new-source flags disabled;
- run a controlled manual workflow with one new source enabled;
- confirm Telegram and Sheets behavior before enabling all sources.

## Assumptions

- This issue may be the final production switch, but only after earlier issues
  are merged and manually verified.
- Documentation must tell the truth about GitHub source semantics: official
  Search API, not exact Trending.
- The workflow should remain simple and understandable for scheduled operation.
