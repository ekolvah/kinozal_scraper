# Issue #6: GitHub new popular repositories source

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/6

## Summary

Add GitHub project notifications as a declarative JSON source using the official
GitHub Search API. The feature is intentionally "new popular repositories in
the last 7 days", not exact `github.com/trending`.

The source must be configurable and default-disabled until the final production
switch.

## Implementation changes

- Add or complete `github_new_popular` in `sources.json`.
- Use endpoint:
  - `https://api.github.com/search/repositories`
- Use params:
  - `q`: `created:>={{DATE_MINUS_7_DAYS}}`
  - `sort`: `stars`
  - `order`: `desc`
  - `per_page`: `{{GITHUB_TOP_LIMIT}}`
- Default `GITHUB_TOP_LIMIT` to `10`.
- Use `github_projects` as the Google Sheets tab.
- Use normalized repository `full_name` as `dedupe_key`.
- Map useful fields when available:
  - `full_name`
  - `html_url`
  - `description`
  - `language`
  - `stargazers_count`
- Keep the source disabled by default until the final rollout issue enables it.

## Runtime behavior

- Missing optional `description` or `language` must not fail the item.
- Empty result sets after a successful response should create a data-quality
  warning, not a crash of the whole bot.
- GitHub API failures should skip this source and allow existing bot tasks to
  continue.

## Test plan

Use synthetic JSON payloads; do not call GitHub in tests.

Cover:

- macro-expanded date query;
- `GITHUB_TOP_LIMIT` default and override;
- item mapping;
- dedupe key normalization;
- missing optional fields;
- disabled-by-default behavior;
- clear documentation that this is not exact GitHub Trending.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- Official API stability is more important than exact Trending-page semantics.
- No public RSSHub or third-party GitHub trending proxy is used in production.
- GitHub Actions can provide `GITHUB_TOP_LIMIT` through repository variables.
