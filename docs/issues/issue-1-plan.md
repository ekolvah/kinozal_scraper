# Issue #1: sources.json schema and macro engine

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/1

## Summary

Add the first declarative configuration layer for the scraper pipeline: a
repo-root `sources.json` file and a pure Python module that loads, expands, and
validates it.

This issue intentionally does not integrate the config into the existing
runtime in `scraper.py`. It only establishes the stable interface that later
issues can build on.

## Implementation changes

- Add `pipeline_config.py` using only the Python standard library.
- Add these public functions:
  - `load_sources_config(path="sources.json")`
  - `build_macro_context(today=None, env=None)`
  - `expand_macros(value, context)`
  - `validate_sources_config(config)`
- Add a `ConfigError` exception for fail-fast validation errors.
- Add `sources.json` with this top-level shape:
  - `version`
  - `sources`
- Each source requires:
  - `id`
  - `type`
  - `url`
  - `limit`
  - `sheet_tab`
  - `dedupe_key`
  - `fields`
  - `message_template`
- Each source may define:
  - `params`
  - `headers`
  - `enabled`
- Macro expansion is recursive and happens after JSON parsing and before
  validation.
- `limit` is converted to `int` after macro expansion and must be a positive
  integer.

## Supported macros

- `{{TODAY}}`
- `{{DATE_MINUS_7_DAYS}}`
- `{{GITHUB_TOP_LIMIT}}`, default `10`
- `{{STEAM_TOP_LIMIT}}`, default `10`

Date macros must render as ISO date strings. Env macros must support override
through environment variables and documented defaults when variables are absent.

## Initial config shape

Add placeholder or disabled source entries for the future pipeline work:

- `github_new_popular`
  - `type: json`
  - `limit: "{{GITHUB_TOP_LIMIT}}"`
  - query contains `created:>={{DATE_MINUS_7_DAYS}}`
- `steam_top_games`
  - `type: json`
  - `limit: "{{STEAM_TOP_LIMIT}}"`
- `kinozal_movies`
  - `type: html`
  - config-compatible skeleton for the current Kinozal top flow

The placeholders make the schema concrete, but this issue must not cause the
current bot to execute these configured sources.

## Test plan

Add `tests/test_pipeline_config.py` using `unittest`.

Cover these scenarios:

- successful config load;
- recursive macro expansion in URL, params, and limit;
- date macros with an injected fixed `today`;
- env macro defaults when env vars are absent;
- env macro overrides when vars are present;
- invalid JSON or top-level shape;
- missing required source field;
- non-integer, zero, or negative `limit`;
- unsupported top-level `version`.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- Config format is JSON, not YAML.
- No new dependencies are added.
- The macro engine only supports `{{NAME}}` replacement. It does not support
  conditions, loops, arithmetic expressions, or source-specific logic.
- Integration with `scraper.py`, Telegram, Google Sheets, and real source fetches
  belongs to later issues.
