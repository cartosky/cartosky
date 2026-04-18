# Forecast Page Backend Contract

This backend pass adds a normalized forecast-page service intended to keep the UI layer stable while upstream provider logic evolves.

## Routes

Preferred production paths:

- `GET /api/v4/locations/search?q=57104`
- `GET /api/v4/forecast-page?lat=43.55&lon=-96.73`
- `GET /api/v4/forecast-page/by-query?q=Sioux+Falls,+SD`
- `GET /api/v4/forecast-discussion?office=FSD`
- `GET /api/v4/model-guidance?lat=43.55&lon=-96.73`

Compatibility aliases are also registered under `/api/*` for local/dev use.

## Provider Routing

- Open-Meteo geocoding resolves all user-entered search queries.
- U.S. locations use a hybrid pipeline: NWS for official text, hourly, alerts, and preferred current observations; Open-Meteo for daily extended forecast and current/hourly fallback.
- Canada and other non-U.S. locations use Open-Meteo only in this beta pass.

## Normalized Forecast Payload

`GET /api/v4/forecast-page` and `GET /api/v4/forecast-page/by-query` return a single normalized object with these top-level fields:

- `location`
- `source_status`
- `current`
- `hourly`
- `daily`
- `official_text_forecast`
- `afd`
- `alerts`
- `attribution`
- `freshness`

The response is intentionally source-agnostic. The frontend should treat upstream vendor names as attribution metadata, not as layout drivers.

### Notes for UI handoff

- `location.display_name` is ready for primary page chrome.
- `current`, `hourly`, and `daily` are already normalized for card/list rendering.
- `official_text_forecast`, `afd`, and `alerts` should be treated as optional sections. They are expected to be `null` or empty for non-U.S. locations.
- `freshness` contains explicit status metadata for current observations, hourly data, AFD age, and alert checks.
- `source_status.primary_region_mode` distinguishes `us_hybrid` from `open_meteo_beta`.

## Placeholder Guidance Route

`GET /api/v4/model-guidance` currently returns a placeholder payload with planned sections for:

- ensemble charts
- guidance summary

This route exists so the future model/ensemble UI can be scoped without changing page routing or introducing temporary frontend-only contracts.