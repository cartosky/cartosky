# NWS Anchor City Modal Implementation Plan

## Summary

This plan adds NWS (National Weather Service) current observations, 7-day forecast, and Area Forecast Discussion (AFD) data to CartoSky, surfaced via a modal triggered by clicking any anchor point city on the map.

The repo-specific decisions are:
1. NWS data is fetched through the FastAPI backend as a proxy, not directly from the browser.
2. The backend owns all NWS API logic: point resolution, station selection with fallback, unit conversion, caching, and retry.
3. The frontend never sees WFO codes, grid coordinates, or station IDs. It requests data by anchor ID only.
4. Pre-computed NWS metadata (WFO, grid coordinates) is treated as a warm cache, not permanent truth. The backend can revalidate from `/points` on expiry or error.
5. Two backend endpoints: one fast path for obs+forecast, one lazy path for AFD.
6. The modal uses a tabbed interface matching the existing glassmorphism UI pattern.

This is the cleanest fit for the current codebase because:
1. Anchor cities already exist as a first-class concept with IDs, coordinates, and DOM markers on the map.
2. The backend already proxies external APIs via `httpx` (see `twf_oauth.py`) with structured error handling.
3. The existing TWF Share Modal provides a proven modal pattern (lazy-loaded, glassmorphism, escape-to-close).
4. The telemetry and Prometheus infrastructure already supports new event types and counters.
5. The `DATA_ROOT` pattern already supports loading data files at startup.

## Non-Goals for V1

These are explicitly out of scope to prevent scope creep:
- No hourly forecast toggle (12-hour periods only)
- No parsed/summarized AFD sections (raw text only)
- No Canadian city support (NWS API is CONUS only; matches existing anchor set)
- No browser-side offline caching of modal data
- No active NWS alerts in the modal
- No forecast icons or weather imagery
- No background revalidation workers or schedulers

## NWS API Call Chains

All NWS data flows start from the `/points/{lat},{lon}` discovery endpoint. No API key is required. All requests must include a `User-Agent` header: `(CartoSky, contact@email)`.

### Point Metadata (Discovery)

```
GET https://api.weather.gov/points/{lat},{lon}
```

Returns WFO code (`cwa`), grid coordinates (`gridX`, `gridY`), and links to forecast and station endpoints. This is the entry point for all subsequent calls.

Key response fields:
- `properties.cwa` / `properties.gridId` — WFO identifier (e.g., `"OKX"`)
- `properties.gridX`, `properties.gridY` — grid coordinates (e.g., `33`, `35`)
- `properties.forecast` — URL for 7-day forecast
- `properties.forecastHourly` — URL for hourly forecast
- `properties.observationStations` — URL for nearby station list
- `properties.forecastOffice` — URL for office details

### Current Observations (3 requests without cache)

```
Step 1:  GET /points/{lat},{lon}
            → extract properties.observationStations URL

Step 2:  GET /gridpoints/{wfo}/{gridX},{gridY}/stations
            → stations sorted by proximity; first is nearest
            → extract features[N].properties.stationIdentifier

Step 3:  GET /stations/{stationId}/observations/latest
            → current conditions (metric units)
```

Observation values arrive in metric (Celsius, km/h, Pascals, meters, mm). The backend converts to US customary before responding.

NWS notes observations may be delayed up to 20 minutes due to upstream MADIS QC.

### 7-Day Forecast (2 requests without cache)

```
Step 1:  GET /points/{lat},{lon}
            → extract properties.forecast URL

Step 2:  GET /gridpoints/{wfo}/{gridX},{gridY}/forecast
            → 14 periods (7 days x day/night)
            → US customary units by default
```

### Area Forecast Discussion (3 requests without cache)

```
Step 1:  GET /points/{lat},{lon}
            → extract properties.cwa (WFO code, e.g. "OKX")

Step 2:  GET /products/types/AFD/locations/{wfo}
            → @graph array in reverse chronological order
            → take @graph[0].id for latest product

Step 3:  GET /products/{productId}
            → full AFD text in properties.productText
```

The WFO-to-AFD relationship: each Weather Forecast Office covers a County Warning Area (CWA). AFDs are authored per-WFO, issued multiple times per day (typically every 3-6 hours). The issuing office uses ICAO format (`K` + WFO code, e.g., `KOKX` for `OKX`).

## Staleness Thresholds

These thresholds are used for both backend station fallback logic and frontend display indicators.

### Observations

| Age Range | Status | Frontend Display |
|-----------|--------|-----------------|
| < 30 min | Fresh | "Observed at {time} from {station}" (default text color) |
| 30-90 min | Warning | "Observed at {time} from {station}" (amber text) |
| > 90 min | Stale | Triggers station fallback in backend. If shown: "Observation may be outdated" (amber text) |

### Forecast

| Age Range (from `generatedAt`) | Status | Frontend Display |
|--------------------------------|--------|-----------------|
| < 6 hours | Fresh | "Generated at {time}" (default text color) |
| 6-12 hours | Warning | "Generated at {time}" (amber text) |
| > 12 hours | Stale | "Forecast may be outdated" (amber text). Still displayed. |

### AFD

| Age Range (from `issuedAt`) | Status | Frontend Display |
|-----------------------------|--------|-----------------|
| < 12 hours | Fresh | "Issued at {time} by NWS {office}" (default text color) |
| 12-24 hours | Warning | "Issued at {time} by NWS {office}" (amber text) |
| > 24 hours | Stale | "This discussion may be outdated" (amber text). Still displayed. |

## Station Fallback Rules

The backend uses these rules when selecting an observation station for a given anchor city:

```
1. Fetch station list:
   GET /gridpoints/{wfo}/{gridX},{gridY}/stations
   NWS returns stations sorted by proximity (nearest first).

2. Try station at index 0:
   GET /stations/{stationId}/observations/latest

3. ACCEPT the observation if ALL of:
   a. HTTP response is 200
   b. Observation timestamp is < 90 minutes old
   c. At least one of (temperature, textDescription) is non-null

4. REJECT and try next station if ANY of:
   a. HTTP error (404, 500, timeout)
   b. Observation timestamp > 90 minutes old
   c. Both temperature AND textDescription are null

5. Cap attempts at 3 stations.

6. If all 3 fail acceptance criteria:
   Return the "best available" observation (most recent timestamp
   among those attempted, preferring non-null temperature).
   Set response flag: "observationDegraded": true

7. Never return null observation data.
   Always return the best we found with metadata about quality.
```

## Retry Policy

Applies to all NWS API calls made by the backend:

- **Retry on**: timeout, HTTP 502, 503, 504
- **Do NOT retry on**: 400, 401, 403, 404, or any successful-but-empty response
- **Max retries**: 1
- **Backoff**: 1 second delay before retry
- **Timeout per request**: 10 seconds

## Backend Anchor Lookup Source

The backend resolves anchor IDs from a dedicated backend-side JSON index, not from the frontend's GeoJSON file.

### Generation

The existing `scripts/generate_anchors_conus.py` script is extended to produce two outputs:

1. `frontend/public/data/anchors_conus.geojson` — existing frontend GeoJSON (unchanged structure, optionally enriched with `wfo`/`gridX`/`gridY` for future use)
2. `backend/data/anchor_index.json` — new backend-side index keyed by anchor ID

### Backend Index Format

```json
{
  "generated_at": "2026-04-02T00:00:00Z",
  "anchors": {
    "NY_1": {
      "city": "New York",
      "state": "New York",
      "st": "NY",
      "lat": 40.7128,
      "lon": -74.0060,
      "wfo": "OKX",
      "gridX": 33,
      "gridY": 35
    },
    "CA_1": {
      "city": "Los Angeles",
      "state": "California",
      "st": "CA",
      "lat": 34.0522,
      "lon": -118.2437,
      "wfo": "LOX",
      "gridX": 154,
      "gridY": 44
    }
  }
}
```

### Loading

The backend loads `anchor_index.json` once at startup from `DATA_ROOT / "anchor_index.json"`. This follows the existing pattern where `DATA_ROOT` is used for published data and manifests. Loaded into a module-level dict via `@lru_cache` or equivalent.

### Rationale

- Backend does NOT read from `frontend/public/`. No coupling between frontend assets and backend logic.
- The generation script already writes files to disk; adding one more output is trivial.
- The `wfo`/`gridX`/`gridY` values in the index are a warm cache. The NWS service revalidates from `/points` on TTL expiry or grid endpoint 404.

## `/points` Refresh Strategy

Simple TTL expiry. No background revalidation, no scheduler.

```
1. On first request for an anchor city:
   Use pre-computed wfo/gridX/gridY from anchor_index.json.

2. Cache /points responses in a TTL dict (24h TTL), keyed by anchor ID.

3. On cache expiry:
   Next request fetches /points synchronously inline.
   Updates the cache with fresh values.

4. On NWS error during revalidation:
   Fall back to pre-computed values from anchor_index.json.
   (They are almost certainly still correct.)

5. On grid endpoint 404 (indicates possible /points drift):
   Force a synchronous /points refresh regardless of TTL.
   Update the cached wfo/gridX/gridY.
   Retry the original request with new coordinates.
```

No scheduler, no background threads. This matches the existing codebase pattern where `twf_oauth.py` does per-request `httpx` calls without background workers.

## AFD "Latest" Selection Logic

```
1. GET /products/types/AFD/locations/{wfo}
2. Read @graph array (reverse-chronological order)
3. If @graph is empty or missing:
   Return { "afd": null, "reason": "no_afd_available" }
4. Take @graph[0].id (the newest product ID)
5. GET /products/{productId}
6. If 404:
   Return error with the product ID for debugging
7. Extract productText, issuanceTime, issuingOffice
```

## Execution Phases

### Phase 1: Enrich Anchor Data with NWS Metadata

**Files modified:**
- `scripts/generate_anchors_conus.py`
- `frontend/public/data/anchors_conus.geojson` (regenerated)
- `frontend/src/lib/anchor-labels.ts`

**Work:**
- Add NWS `/points/{lat},{lon}` resolution to the generation script for each anchor city.
- Rate-limit script calls (~0.5s delay between requests, NWS courtesy).
- Store `wfo`, `gridX`, `gridY` on each GeoJSON feature (optional frontend enrichment).
- Generate `backend/data/anchor_index.json` as a new output.
- Update `AnchorFeatureProperties` type to include optional `wfo?: string`, `gridX?: number`, `gridY?: number`.

**Validation:**
- Verify all anchor cities resolve to valid WFO/grid combinations.
- Spot-check a sample of cities across different WFOs.
- Confirm the backend index loads correctly.

### Phase 2: Backend NWS Service Layer

**New files:**
- `backend/app/services/nws.py`

**Work:**

NWS service module with the following internal functions:

`resolve_point_metadata(anchor_id: str) -> PointMetadata`
- Returns `wfo`, `gridX`, `gridY` for an anchor.
- First checks 24h TTL cache, then falls back to anchor index, then fetches `/points` live.
- On grid 404: forces `/points` refresh.

`get_station_list(wfo: str, gridX: int, gridY: int) -> list[str]`
- Fetches `/gridpoints/{wfo}/{gridX},{gridY}/stations`.
- Returns ordered list of station IDs (nearest first).
- 1-hour TTL cache.

`get_observation_with_fallback(station_list: list[str]) -> ObservationResult`
- Implements the station fallback rules defined above.
- Tries up to 3 stations.
- Returns the best available observation with quality metadata.
- Converts metric units to US customary:
  - Temperature: C to F
  - Dewpoint: C to F
  - Wind speed: km/h to mph
  - Wind gust: km/h to mph
  - Pressure: Pa to inHg
  - Visibility: m to mi
  - Precipitation: mm to in
  - Wind chill / Heat index: C to F

`get_forecast(wfo: str, gridX: int, gridY: int) -> ForecastResult`
- Fetches `/gridpoints/{wfo}/{gridX},{gridY}/forecast`.
- Returns 14 periods (7 days x day/night).
- NWS returns US customary by default for this endpoint.

`get_latest_afd(wfo: str) -> AfdResult | None`
- Implements the AFD selection logic defined above.
- Returns full product text, issuance time, and issuing office.

All functions use `httpx.AsyncClient` with:
- 10-second timeout per request
- `User-Agent: (CartoSky, contact@email)` header
- Retry policy: 1 retry on timeout/502/503/504 with 1s backoff
- Structured logging on all NWS calls

### Phase 3: Backend API Endpoints

**Files modified:**
- `backend/app/main.py` (add new route handlers)

**Two endpoints:**

#### `GET /api/v4/anchors/{anchor_id}/weather`

Returns current observations and 7-day forecast bundled together.

Cache TTL: observations 3 min, forecast 15 min. Combined endpoint returns the freshest of the two (re-fetches whichever is expired).

Response shape:
```json
{
  "city": "New York",
  "state": "New York",
  "st": "NY",
  "wfo": "OKX",
  "observation": {
    "stationName": "New York City, Central Park",
    "stationId": "KNYC",
    "observedAt": "2026-04-02T23:51:00+00:00",
    "tempF": 42,
    "dewpointF": 39,
    "relativeHumidity": 89,
    "windDirection": "ENE",
    "windSpeedMph": 7,
    "windGustMph": null,
    "windChillF": 37,
    "heatIndexF": null,
    "pressureInHg": 30.47,
    "visibilityMi": 4.0,
    "textDescription": "Overcast",
    "precipLastHourIn": null
  },
  "forecast": {
    "generatedAt": "2026-04-02T18:27:44+00:00",
    "periods": [
      {
        "number": 1,
        "name": "Tonight",
        "isDaytime": false,
        "tempF": 44,
        "windSpeed": "8 to 12 mph",
        "windDirection": "E",
        "shortForecast": "Chance Very Light Rain",
        "detailedForecast": "A slight chance of rain and a slight chance of drizzle...",
        "precipProbability": 29
      }
    ]
  },
  "meta": {
    "anchorId": "NY_1",
    "resolvedFromCache": true,
    "observationDegraded": false,
    "observationStationFallbackUsed": false,
    "stationsAttempted": 1
  }
}
```

#### `GET /api/v4/anchors/{anchor_id}/afd`

Returns the latest Area Forecast Discussion for the anchor city's WFO.

Cache TTL: 30 min.

Response shape:
```json
{
  "wfo": "OKX",
  "officeName": "NWS New York NY",
  "issuedAt": "2026-04-02T19:49:00+00:00",
  "productText": "000\nFXUS61 KOKX 021949\nAFDOKX\n\nArea Forecast Discussion\nNational Weather Service New York NY\n349 PM EDT Thu Apr 2 2026\n\n.SYNOPSIS...\n...",
  "meta": {
    "anchorId": "NY_1",
    "productId": "740489d4-0eeb-4d6b-808e-d05de38b8365",
    "resolvedFromCache": true
  }
}
```

**Error responses** follow the existing `_error_response` pattern:
```json
{
  "error": {
    "code": "NWS_UPSTREAM_ERROR",
    "message": "NWS API temporarily unavailable.",
    "upstream_status": 503
  }
}
```

**Anchor ID validation:**
- If `anchor_id` is not found in the anchor index: 404 with `code: "ANCHOR_NOT_FOUND"`.

### Phase 4: Frontend API Client

**File modified:**
- `frontend/src/lib/api.ts`

**Two new fetch functions:**

```typescript
export async function fetchAnchorWeather(
  anchorId: string,
  signal?: AbortSignal,
): Promise<AnchorWeatherResponse | null>

export async function fetchAnchorAfd(
  anchorId: string,
  signal?: AbortSignal,
): Promise<AnchorAfdResponse | null>
```

Both use `fetch()` against `${API_V4_BASE}/anchors/{anchorId}/weather` and `${API_V4_BASE}/anchors/{anchorId}/afd` respectively. Both support `AbortSignal` for cancellation when the modal closes.

**New TypeScript types** for the response shapes defined above.

### Phase 5: Click Handler on Anchor Markers

**Files modified:**
- `frontend/src/components/map-canvas.tsx`
- `frontend/src/styles/globals.css`

**map-canvas.tsx changes:**

In `syncAnchorMarkers`, add a `click` event listener to the anchor chip element alongside the existing `mouseenter`/`mouseleave` handlers:

```typescript
chip.addEventListener("click", (e) => {
  e.stopPropagation();
  onAnchorClick?.(feature);
});
```

Add a new callback prop to the component:
```typescript
onAnchorClick?: (feature: AnchorFeature) => void;
```

The callback receives the full feature (including `id`, `city`, `state`, coordinates, and optional NWS metadata).

**globals.css changes:**

```css
.map-anchor-marker__chip {
  cursor: pointer;  /* changed from cursor: default */
}
```

**Mobile tap target:**

Expand the chip's touch target to minimum 44x44px (WCAG) using a transparent pseudo-element:

```css
.map-anchor-marker__chip::before {
  content: "";
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  min-width: 44px;
  min-height: 44px;
}
```

This expands the tappable area without changing the visual chip size.

### Phase 6: NWS City Detail Modal

**New file:**
- `frontend/src/components/nws-city-modal.tsx`

**Lazy-loaded** in `App.tsx` following the TWF Share Modal pattern:

```typescript
const NwsCityModal = lazy(() =>
  import("@/components/nws-city-modal").then((m) => ({ default: m.NwsCityModal }))
);
```

**Props:**

```typescript
type NwsCityModalProps = {
  open: boolean;
  onClose: () => void;
  anchor: {
    id: string;
    city: string;
    state: string;
    st: string;
  };
};
```

**Modal structure:**

- **Backdrop**: Fixed fullscreen overlay matching existing glass pattern (`bg-slate-950/46 backdrop-blur-sm`)
- **Dismiss**: Click backdrop, press Escape, or click X button
- **Body scroll lock**: `document.body.style.overflow = "hidden"` when open
- **Container**: Responsive sizing:
  - Desktop (>= 640px): `max-w-2xl`, centered, `rounded-2xl`, `max-h-[calc(100dvh-2rem)]`
  - Mobile (< 640px): Full screen, no border radius, `100dvh`

**Header:**
- City name and state abbreviation (e.g., "New York, NY")
- WFO badge (e.g., "NWS New York - OKX") shown after weather data loads
- Close (X) button

**Tab bar:**
- Three tabs: **Current** | **Forecast** | **AFD**
- Active tab indicated with underline or background highlight
- Default active tab: Current

**Current Observations tab:**
- Fetched on modal open (part of `/weather` endpoint)
- Shows key-value grid:
  - Temperature, Dewpoint, Humidity
  - Wind (speed + direction), Gusts
  - Wind Chill or Heat Index (whichever is non-null)
  - Pressure (inHg), Visibility (mi)
  - Conditions (textDescription)
- Footer: "Observed at {time} from {stationName}" with relative time ("12 min ago")
- Staleness indicator per thresholds defined above
- Loading state: skeleton loader for the grid
- Error state: inline message with "Retry" button

**Forecast tab:**
- Fetched on modal open (part of `/weather` endpoint, same request as obs)
- Shows 14 periods as a vertical list:
  - Period name (e.g., "Tonight", "Friday", "Friday Night")
  - Temperature with unit
  - Short forecast text
  - Wind speed and direction
  - Precipitation probability (if > 0%)
  - Expandable: click/tap period row to reveal `detailedForecast` text
- Footer: "Generated at {time}"
- Staleness indicator per thresholds defined above
- Loading state: skeleton loader
- Error state: inline message with "Retry" button

**AFD tab:**
- Fetched **lazily** when the AFD tab is first clicked (separate `/afd` endpoint)
- AFD result cached in component state until modal closes
- Full AFD text in a scrollable `<pre>` block with monospace font
- Reasonable max-height with overflow scroll
- Footer: "Issued at {time} by NWS {officeName}"
- Staleness indicator per thresholds defined above
- Loading state: skeleton loader
- Error state: inline message with "Retry" button
- If no AFD available: "No Area Forecast Discussion available for this office."

### Phase 7: State Wiring in App.tsx

**File modified:**
- `frontend/src/App.tsx`

**New state:**

```typescript
const [selectedAnchorCity, setSelectedAnchorCity] = useState<{
  id: string;
  city: string;
  state: string;
  st: string;
} | null>(null);
```

**Callback wiring:**

Pass `onAnchorClick` callback to `MapCanvas`:

```typescript
<MapCanvas
  ...existing props...
  onAnchorClick={(feature) => setSelectedAnchorCity({
    id: feature.id,
    city: feature.properties.city,
    state: feature.properties.state,
    st: feature.properties.st,
  })}
/>
```

**Modal rendering:**

```typescript
{selectedAnchorCity && (
  <Suspense fallback={null}>
    <NwsCityModal
      open={!!selectedAnchorCity}
      onClose={() => setSelectedAnchorCity(null)}
      anchor={selectedAnchorCity}
    />
  </Suspense>
)}
```

## Loading Behavior (Explicit)

This defines the exact loading sequence when a user clicks an anchor city:

1. **Click**: Modal opens immediately. Header shows city name. Tabs render. Current tab is active. Body shows skeleton loader.
2. **Weather request**: `fetchAnchorWeather(anchorId)` fires on modal open.
3. **Weather response arrives**: Both Current and Forecast tabs populate from the same response. Skeleton loaders replaced with data.
4. **User clicks AFD tab**: `fetchAnchorAfd(anchorId)` fires on first AFD tab click. AFD tab shows skeleton loader.
5. **AFD response arrives**: AFD tab populates. Text rendered in monospace block.
6. **AFD cached**: If user switches away from AFD tab and back, cached result is shown immediately (no re-fetch until modal closes).
7. **Modal closes**: All in-flight requests aborted via `AbortController`. Component state cleared. Next open starts fresh.

## Cache TTL Summary

| Data | Backend Cache TTL | Notes |
|------|------------------|-------|
| `/points` metadata | 24 hours | Revalidated synchronously on expiry. Fallback to anchor index on error. |
| Station list | 1 hour | Per WFO/grid combo. Stations rarely change. |
| Observations | 3 minutes | NWS obs delayed ~20 min by MADIS QC; aggressive refresh has diminishing returns. |
| Forecast | 15 minutes | NWS forecasts update every ~6 hours. |
| AFD | 30 minutes | AFDs issued every 3-6 hours. |

## Telemetry

### Usage Events (via existing `admin_telemetry` pattern)

| Event Name | When | Metadata |
|-----------|------|----------|
| `nws_modal_opened` | Modal opens | `anchorId`, `city`, `st` |
| `nws_afd_tab_opened` | AFD tab first clicked | `anchorId` |
| `nws_weather_fetch_error` | Weather endpoint fails | `anchorId`, `errorCode`, `upstreamStatus` |
| `nws_afd_fetch_error` | AFD endpoint fails | `anchorId`, `errorCode` |
| `nws_observation_fallback_used` | Backend used non-primary station | `anchorId`, `stationsAttempted`, `selectedStation` |

### Prometheus Counters (via existing `prometheus_metrics` pattern)

```
cartosky_nws_requests_total{endpoint="weather|afd", result="success|error|timeout"}
cartosky_nws_upstream_requests_total{nws_endpoint="points|stations|observations|forecast|afd_list|afd_product", result="success|error|timeout"}
cartosky_nws_observation_fallback_total{result="primary|fallback|degraded"}
```

### Perf Events

| Event Name | When | Value |
|-----------|------|-------|
| `nws_weather_fetch_duration_ms` | Weather endpoint completes | Total ms including all NWS calls |
| `nws_afd_fetch_duration_ms` | AFD endpoint completes | Total ms including all NWS calls |

## File Change Summary

| File | Change Type | Description |
|------|------------|-------------|
| `scripts/generate_anchors_conus.py` | Modify | Add NWS `/points` resolution; output backend anchor index |
| `frontend/public/data/anchors_conus.geojson` | Regenerate | Optionally enriched with `wfo`/`gridX`/`gridY` |
| `backend/data/anchor_index.json` | **New** | Backend-side anchor index keyed by ID |
| `frontend/src/lib/anchor-labels.ts` | Modify | Update `AnchorFeatureProperties` type |
| `backend/app/services/nws.py` | **New** | NWS service: point resolution, station fallback, caching, unit conversion |
| `backend/app/main.py` | Modify | Add `/api/v4/anchors/{anchor_id}/weather` and `/api/v4/anchors/{anchor_id}/afd` endpoints |
| `frontend/src/lib/api.ts` | Modify | Add `fetchAnchorWeather` and `fetchAnchorAfd` functions with types |
| `frontend/src/components/map-canvas.tsx` | Modify | Add click handler and `onAnchorClick` callback prop |
| `frontend/src/styles/globals.css` | Modify | `cursor: pointer` on chips, mobile tap target pseudo-element |
| `frontend/src/components/nws-city-modal.tsx` | **New** | Modal component with 3 tabs, loading/error states |
| `frontend/src/App.tsx` | Modify | Add `selectedAnchorCity` state, wire callback and modal |

## Implementation Order

This is the recommended build sequence. Each phase is independently testable.

### Phase 1: Backend anchor index + generation script
- Modify `generate_anchors_conus.py`
- Generate `backend/data/anchor_index.json`
- Regenerate frontend GeoJSON
- **Test**: Verify all anchors resolve to valid WFO/grid combos

### Phase 2: Backend NWS service layer
- Create `backend/app/services/nws.py`
- Implement point resolution, station fallback, forecast fetch, AFD fetch
- Implement caching, retry, unit conversion
- **Test**: Unit tests for service functions, mock NWS responses

### Phase 3: Backend API endpoints
- Add routes to `backend/app/main.py`
- Wire up NWS service to endpoints
- Add error handling, Prometheus counters
- **Test**: Integration tests via `httpx.AsyncClient` (following existing test patterns)

### Phase 4: Frontend API client + types
- Add fetch functions and types to `api.ts`
- **Test**: TypeScript compilation

### Phase 5: Click handler on anchor markers
- Modify `map-canvas.tsx` and `globals.css`
- **Test**: Visual verification that click opens callback, cursor changes

### Phase 6: NWS modal component
- Create `nws-city-modal.tsx`
- Wire up in `App.tsx`
- **Test**: End-to-end with live backend

### Phase 7: Telemetry
- Add usage events, perf events, Prometheus counters
- **Test**: Verify events appear in admin telemetry
