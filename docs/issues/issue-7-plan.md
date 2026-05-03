# Issue #7: Steam top games source via SteamSpy

GitHub issue: https://github.com/ekolvah/kinozal_scraper/issues/7

## Summary

Add Steam game notifications as a declarative JSON source using SteamSpy's
enriched top-games endpoint. SteamSpy is used because it returns `appid`, game
name, developer, and current user metrics in one request.

The source must be configurable and default-disabled until the final production
switch.

## Implementation changes

- Add or complete `steam_top_games` in `sources.json`.
- Use endpoint:
  - `https://steamspy.com/api.php?request=top100in2weeks`
- Default `STEAM_TOP_LIMIT` to `10`.
- Use `steam_games` as the Google Sheets tab.
- Use `appid` as `dedupe_key`.
- Map useful fields when available:
  - `appid`
  - `name`
  - `developer`
  - `ccu`
- Build the Steam app URL from `appid`:
  - `https://store.steampowered.com/app/{appid}/`
- Keep the source disabled by default until the final rollout issue enables it.

## Runtime behavior

- Do not make a second request per app id.
- Missing optional `developer` or metric data must not fail the item.
- SteamSpy API failures should skip this source and allow existing bot tasks to
  continue.
- The notification count must be controlled by `STEAM_TOP_LIMIT` without code
  changes.

## Test plan

Use synthetic JSON payloads; do not call SteamSpy in tests.

Cover:

- object/dictionary style SteamSpy payload mapping;
- `STEAM_TOP_LIMIT` default and override;
- Steam URL construction from `appid`;
- dedupe key normalization;
- missing optional fields;
- disabled-by-default behavior;
- no per-app secondary fetch path.

Tests must run with:

```bash
python -m unittest discover
```

## Assumptions

- SteamSpy is acceptable for v1 despite being a third-party API, because it gives
  enriched data in one request.
- Steam Web API is not used for this source because the charts endpoint lacks
  names/descriptions.
- GitHub Actions can provide `STEAM_TOP_LIMIT` through repository variables.
