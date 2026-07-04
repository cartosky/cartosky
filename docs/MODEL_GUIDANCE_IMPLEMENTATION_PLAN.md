# Model Guidance Implementation Plan

> **Audience:** AI agents executing discrete phases. Each phase is self-contained and must pass its verification checklist before the next phase begins.
>
> **Target:** Complete, polished Model Guidance on `/forecast` before October (TWF busy season).
>
> **Phases:** 1A → 1B → 2 → 3. Each phase is independently executable and verifiable.
>
> **Status (2026-07-04):** Phases 1A, 1B, and 2 are **complete and deployed to production**. Additional mean meteogram charts may be added in the future, but the Models and Ensembles tabs as specified here are done. Phase 3 is **blocked on `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md`**, which supersedes this document's original member-pipeline and sizing-spike material (see revision note).
>
> **Revision note (2026-07-04):** This document predated the value-COG → binary-sampling migration (`docs/VALUE_COG_TO_BINARY_SAMPLING_MIGRATION_PLAN.md`). Sampling-substrate claims have been corrected in place: models on the `CARTOSKY_BINARY_SAMPLING_MODELS` allowlist publish **no value COGs** — sampling reads grid binaries via the binary sampler. The original Phase 3 storage schema and sizing spike were COG-based and are superseded. **Do not execute Phase 3 instructions from this document's git history.**

---

## 1. Overview

### What this is

The Forecast page (`/forecast`) currently exposes a single **Models** top-level tab that renders a placeholder (`ModelsTab` in `frontend/src/pages/forecast.tsx`). This plan replaces that placeholder and adds a sibling top-level tab:

| Top-level Forecast tab | Purpose |
|------------------------|---------|
| **Models** | Deterministic / multi-model comparison charts (ECMWF, GFS, AIFS, NBM, etc.) |
| **Ensembles** | EPS and GEFS ensemble guidance — mean products in Phase 2, per-member products in Phase 3 |

**Locked decision:** Models and Ensembles are **separate top-level tabs** in `forecast.tsx` (alongside Hourly, 7-day, Extended, Discussion). They are not nested sub-tabs inside a single Model Guidance panel. Phase 1A ships the Models tab; the Ensembles top-level tab is added in Phase 2 (disabled or "Coming soon" until then).

All charts sample from **already-published** grid artifacts, using the same sampling service as `/api/v4/sample` and `/api/v4/sample/batch`. The sampling substrate is per-model, governed by the `CARTOSKY_BINARY_SAMPLING_MODELS` allowlist (see the migration plan): allowlisted models publish **grid binaries only** (`published/{model}/{run_id}/{runtime_var}/fh{NNN}.l0.u16.bin` + meta sidecar) and are sampled via the binary sampler; non-allowlisted models still publish and sample `fh{NNN}.val.cog.tif`. The meteogram service is substrate-agnostic — it calls the shared sampling layer, which resolves the substrate per model. No request-time Herbie fetches, ever.

### Why it matters for TWF

TWF forecasters need a single location-centric view that answers: *What do the models say for temperature, precip, wind, and snow at this point?* and *How much spread / agreement is there across the ensemble?* Today they must open the map viewer and scrub frame-by-frame. Model Guidance consolidates that into meteogram-style charts tied to the Forecast page location search.

### Phase relationship

```text
Phase 1A ──► Meteogram endpoint + shared design system + temperature chart + pill filter + loading/error/empty states
              │
              ▼
Phase 1B ──► Cumulative precip + 6-hr precip detail + wind chart + daily high/low toggle (completes Models tab)
              │
              ▼
Phase 2 ──► Ensembles tab (mean-only products derivable from existing artifacts)
              │
              ▼  (blocked on docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md — spike + member publish phases)
Phase 3 ──► Per-member spaghetti, precip/snow distributions, probability thresholds
```

Phase 1A is the prerequisite for Phase 1B. Phase 1B completes the Models tab. Phase 2 can ship after Phase 1B without Phase 3. Phase 3 is explicitly blocked on new scheduler/storage work for individual ensemble members — that work is now fully specified in `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` (it serves map products in addition to meteograms, so it is no longer scoped as a sub-section of this plan).

### Chart catalog

| Chart | Tab | Phase | Models / Ensembles | Variables |
|-------|-----|-------|-------------------|-------------|
| Multi-model hourly temperature | Models | 1A | ECMWF, GFS, AIFS, NBM | `tmp2m` |
| Model pill filter (tab-wide) | Models | 1A | ECMWF, GFS, AIFS, NBM | — |
| Multi-model cumulative precipitation | Models | 1B | ECMWF, GFS, NBM, AIFS | `precip_total` |
| Per-model 6-hr precipitation detail | Models | 1B | ECMWF, GFS, NBM, AIFS (one sub-chart each) | `precip_total` (derived 6-hr steps) |
| Multi-model 10 m wind speed | Models | 1B | ECMWF, GFS, NBM | `wspd10m` |
| Daily high/low temperature toggle | Models | 1B | ECMWF, GFS, AIFS, NBM | `tmp2m` (client aggregation) |
| Ensemble temperature spread | Ensembles | 2 (placeholder) / 3 | EPS, GEFS | `tmp2m` — **requires per-member data** |
| Ensemble precip probability thresholds | Ensembles | 2 (placeholder) / 3 | EPS, GEFS | `precip_total` — **requires per-member data** |
| Ensemble mean temperature plume | Ensembles | 2 | EPS, GEFS | `tmp2m` (mean) |
| Ensemble mean cumulative precip | Ensembles | 2 | EPS, GEFS | `precip_total` (mean) |
| Temperature plume (spaghetti) | Ensembles | 3 | EPS (50 members), GEFS (30 members) | `tmp2m` |
| Precipitation plume (spaghetti) | Ensembles | 3 | EPS, GEFS | `precip_total` |
| Snowfall member distribution (histogram) | Ensembles | 3 | EPS, GEFS | `snowfall_total` |
| Snowfall member detail panel | Ensembles | 3 | EPS, GEFS | `snowfall_total` |

---

## 2. Architecture & Constraints

### Backend data reality

**EPS** (`backend/app/models/eps.py`):
- Herbie: `model=ifs`, `product=enfo`, aggregation `ecmwf_pf_mean` for mean fields.
- `EPS_CAPABILITIES.ensemble.supported_views` and every `EPS_VARIABLE_CATALOG` entry: `"supported_views": ["mean"]` only.
- Runtime artifacts use `__mean` suffix (e.g. `tmp2m__mean`, `precip_total__mean`, `wspd10m__mean`).
- Individual EPS perturbation members are **not stored or served** — but they **are downloaded**: `_fetch_ecmwf_pf_mean_variable` (`builder/fetch.py`) byte-range-downloads a subset GRIB containing **all perturbed member bands** per `(var, fh)`, streams them to compute the mean, and discards the member fields. This matters for Phase 3 economics — see the member pipeline plan.

**GEFS** (`backend/app/models/gefs.py`):
- Herbie: `model=gefs`, `product=atmos.5`, `herbie_kwargs["member"] = "mean"` for all published variables.
- `GEFS_CAPABILITIES.ensemble.supported_views`: `["mean"]` only.
- Runtime artifacts use `__mean` suffix.
- Individual GEFS member files (`atmos.5` with `member=1..30`) are **not** fetched, stored, or served — the mean comes from the upstream **precomputed `geavg` product** (`member="mean"`), so per-member GEFS is net-new fetch load, unlike EPS. See the member pipeline plan.

**Deterministic models** (Phases 1A/1B): ECMWF (`ecmwf`), GFS (`gfs`), AIFS (`aifs`), NBM (`nbm`) publish single-member artifacts with no `__mean` suffix. All have `supports_sampling: True` in their capabilities.

**Sampling substrate (post-migration):** models on `CARTOSKY_BINARY_SAMPLING_MODELS` publish grid binaries only (no value COGs) and are sampled via the binary sampler; remaining models still use COGs. The meteogram service is substrate-agnostic through the shared sampling layer. When GEFS/EPS flip to the allowlist, their `__mean` artifacts become binary-only with no behavior change to any Phase 1–2 chart.

**Implication:** Any chart requiring spread, probability of exceedance, spaghetti plots, or member-ranked snowfall **cannot** be built from current data. Phase 2 must show placeholders for those charts or ship mean-only alternatives.

### Existing sampling endpoints

**`GET /api/v4/sample`** (`main.py` ~5436):
- One point, one model, one run, one variable, one forecast hour.
- Substrate-branched per the allowlist: binary path (binary-frame resolver + `_decode_values`-backed sampler) for allowlisted models; COG path (`_resolve_val_cog` → `_get_cached_dataset` → `_read_sample_value`) otherwise. `sampling_source` is part of the cache keys.
- Resolves ensemble models to runtime var via `_runtime_var_id_for_request` (e.g. `tmp2m` + `ensemble_view=mean` → `tmp2m__mean`).

**`POST /api/v4/sample/batch`** (`main.py` ~5599):
- One model × one run × one variable × one forecast hour × many points.
- Request body (`SampleBatchIn`): `{ model, run, variable, forecast_hour, points[], ensemble_view?, region? }`.
- Returns `{ units, values: { [pointId]: number | null } }`.
- Partial nulls for OOB/nodata; 404 if COG missing; 500 on internal error.

The meteogram endpoint must **not** expose N separate HTTP round-trips to the client. It fans out internally and returns one payload.

### Meteogram endpoint specification

**Route:** `POST /api/v4/forecast/meteogram`

**Also wire:** Update `GET /api/v4/model-guidance` and `GET /api/model-guidance` to delegate to the new service (or redirect clients to meteogram). The placeholder `get_model_guidance_placeholder()` in `forecast_page.py` is replaced by `get_forecast_meteogram()`.

#### Request

```typescript
interface MeteogramRequest {
  lat: number;          // WGS84, -90..90
  lon: number;          // WGS84, -180..180
  models: string[];     // e.g. ["ecmwf","gfs","aifs","nbm","eps","gefs"]
  variables: string[];    // canonical var keys, e.g. ["tmp2m","precip_total","wspd10m"]
  run_policy: RunPolicy;
  include_members?: boolean;  // Phase 3 only; default false
  region?: string | null;   // optional; defaults per model canonical_region
}

type RunPolicy = { type: "latest_per_model" };
// Future enhancement: synchronized run_policy (align all models to anchor model init time) — not in v1.
```

| Field | Rules |
|-------|-------|
| `lat`, `lon` | Required. Rounded to 3 decimal places (~110 m) for cache key only; sampling uses full precision. |
| `models` | 1–8 entries. Normalized lowercase. Entitlement-checked per model via `require_product_access`. If a requested model is unauthorized, include it in `series` with `"status": "not_entitled"` and no variable data — never 403 for the whole request. Frontend hides unentitled model pills and does not include them in `models[]`. |
| `variables` | 1–6 entries. Normalized via model plugin `normalize_var_id`. |
| `run_policy` | `latest_per_model` (default, only supported value in v1): each model uses its own `LATEST.json` run. |
| `include_members` | When `true`, response includes `members` arrays (Phase 3). Rejected with 400 if any requested model lacks `members` in `supported_views`. |
| `ensemble_view` | Implicit: use `"mean"` for `eps` and `gefs`; omit for deterministic models. |

#### Response

```json
{
  "location": { "lat": 45.123, "lon": -93.456 },
  "generated_at": "2026-06-23T18:00:00Z",
  "run_policy": { "type": "latest_per_model" },
  "series": {
    "ecmwf": {
      "run_id": "20260623_12z",
      "run_time": "2026-06-23T12:00:00Z",
      "status": "ok",
      "variables": {
        "tmp2m": {
          "units": "F",
          "points": [
            { "fh": 0, "valid_time": "2026-06-23T12:00:00Z", "value": 72.1 },
            { "fh": 3, "valid_time": "2026-06-23T15:00:00Z", "value": 74.3 }
          ]
        },
        "precip_total": {
          "units": "in",
          "points": [
            { "fh": 6, "valid_time": "2026-06-23T18:00:00Z", "value": 0.12 }
          ]
        }
      }
    },
    "gfs": {
      "run_id": "20260623_12z",
      "status": "partial",
      "variables": {
        "tmp2m": { "units": "F", "points": [] },
        "wspd10m": { "units": "mph", "points": null, "error": "artifact_not_found" }
      }
    }
  }
}
```

Not-entitled model (no `variables` key):

```json
"ecmwf": { "status": "not_entitled" }
```

| Response rule | Behavior |
|---------------|----------|
| Per-model status | `ok` (all requested vars have ≥1 point), `partial` (some vars null/empty), `unavailable` (run not found), `not_entitled` (user lacks product access for this model). |
| Per-variable `points` | Sorted by `fh` ascending. `null` + `error` string when COG/manifest missing for all frames. Never raises 500 for missing data. |
| Per-variable `members` | Phase 3: `{ "m01": { "points": [...] }, ... }` when `include_members=true`. |
| HTTP status | `200` always when request is valid, even if all series are partial. `400` invalid body. `422` validation. `429` rate limit via dedicated `_meteogram_rate_limit_allow` (see Section 4.2). |

#### Internal fan-out logic

```python
async def get_forecast_meteogram(
    *,
    lat: float,
    lon: float,
    models: list[str],
    variables: list[str],
    run_policy: dict[str, Any],
    include_members: bool = False,
    region: str | None = None,
) -> dict[str, Any]:
    ...
```

Implementation steps per `(model, variable)` pair:

1. Resolve `run_id` from `run_policy` + `LATEST.json` / manifest.
2. Load manifest; read `variables[var].frames[].fh` list (same source as `list_frames`).
3. For each `fh`, call the internal sampler from `backend/app/services/sampling.py` — the substrate-aware entry point that routes to the binary sampler for allowlisted models and the COG path (`_resolve_val_cog` → `_get_cached_dataset` → `_read_sample_value`) otherwise — **do not** HTTP-loop to self. *(Historical note: as originally written for Phase 1A this step was COG-only; the binary migration made the sampling layer substrate-branched.)*
4. Apply unit conversion from variable capability catalog if sidecar units differ from display units.
5. Catch per-fh failures; omit point or set null — never abort sibling models.
6. Batch dataset/frame opens: group by `(model, run, runtime_var)` so each COG dataset (or decoded binary frame, per substrate) is opened once per request.

**Function location:** `backend/app/services/forecast_page.py` (new `get_forecast_meteogram`). Route handler in `backend/app/main.py`. Sampling helpers live in `backend/app/services/sampling.py` — **extract from `main.py` as Phase 1A task 0** before writing meteogram code (required; avoids circular imports).

**Replace placeholder route:**

```python
# main.py — update model_guidance_placeholder_v4 to POST body or keep GET with query params
# Recommended: new POST route only; deprecate GET placeholder response shape
@app.post("/api/v4/forecast/meteogram")
async def forecast_meteogram(body: MeteogramRequestIn, ...):
    return await forecast_page_service.get_forecast_meteogram(...)
```

### Caching strategy

**Goal:** Cloudflare-cacheable responses; zero cold COG reads on cache hit at the edge (origin may still hit in-process COG cache).

| Layer | Key | TTL | Notes |
|-------|-----|-----|-------|
| CDN (`Cache-Control`) | — | `public, max-age=300, stale-while-revalidate=60` | 5 min; matches `model_guidance_placeholder` today. |
| Cache key (CF + origin) | `meteogram:v1:{lat_3dp}:{lon_3dp}:{models_hash}:{vars_hash}:{run_policy_hash}:{include_members}:{run_ids_hash}` | — | **Locked:** include resolved `run_id` per model in `run_ids_hash`. Cache misses on new cycle publish are expected and correct — do not omit run_id to avoid serving stale previous-cycle data for up to 5 minutes. |
| Origin in-process | Same key | 300 s | Mirror `_sample_cache` pattern in `forecast_page.py` or `main.py`. |
| COG dataset cache | Existing `_ds_cache` | Existing | Meteogram must not bypass `_get_cached_dataset`. |

**TTL reasoning:** Model cycles update every 1–6 hours; 5-minute CDN TTL balances freshness vs. fan-out cost. A single meteogram for 4 models × 3 variables × ~60 forecast hours = ~720 point samples — caching is mandatory.

**Never:** Fan out to Herbie or unpublish'd paths. If the frame artifact for the model's substrate (`fh{NNN}.l0.u16.bin` for allowlisted models, `fh{NNN}.val.cog.tif` otherwise) is absent, return null for that point.

### Chart library selection

| Library | Pros | Cons |
|---------|------|------|
| **Recharts** | Already in `package.json`; React-native; adequate for ≤6 series | SVG DOM; poor perf with 50+ series × 60+ points; heavy re-renders on toggle |
| **uPlot** | Canvas; handles 80+ series smoothly; small bundle (~45 KB); built for dense time-series | Imperative API; needs thin React wrapper |
| **D3** | Maximum control | Most boilerplate; reinvents scales/axes; slower agent execution |

**Recommendation (approved): uPlot** for all Model Guidance charts (Models and Ensembles tabs).

Justification: Phase 3 requires 50 EPS + 30 GEFS member lines without degradation. Recharts cannot meet that bar. uPlot handles Phase 1A's 4-line temperature chart equally well with one abstraction (`UplotTimeSeriesChart`). Add dependency: `uplot` + `uplot-react` (or a ~80-line wrapper in `frontend/src/components/charts/UplotChart.tsx`) in **Phase 1A**.

Recharts remains in the repo for admin analytics only; do not use for Model Guidance.

### Individual member data pipeline (Phase 3 prerequisite) — SUPERSEDED

**This subsection is superseded by `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` (2026-07-04).** The version that previously lived here was written before the binary-sampling migration: its sizing spike measured member **value COGs** (an artifact allowlisted models no longer produce), its storage schema used `.val.cog.tif` paths, and its footprint heuristics were COG-inclusive. It also scoped the member pipeline as meteogram-only; the pipeline now has three consumer families (meteogram members, derived stats grids for maps, and potential served per-member maps), which is why it graduated to its own plan.

What the member pipeline plan owns: sizing spike protocol (binary edition), member/stats naming and manifest schema, packing resolution for member ids, member build profile, EPS/GEFS fetch strategy, retention decision, storage/RAM budgets, and all rollout phases through member publish. What **this** document still owns for Phase 3: the meteogram `include_members` contract (Section 2 above), the Ensembles-tab chart specifications (Section 7), and their verification checklist.

The original spike gate is unchanged in substance: **no member scheduler work of any kind until the sizing spike in the member pipeline plan is complete and Brian has signed off.**

---

## 3. Shared Design System

Establish in **Phase 1A before any chart component**.

### File: `frontend/src/lib/chart-constants.ts`

```typescript
export const MODEL_COLORS = {
  ecmwf: "#E85002",   // ECMWF orange
  gfs: "#1E6BB8",     // NOAA blue
  aifs: "#9467BD",    // purple (ECMWF AI)
  aigfs: "#17BECF",   // cyan — reserved; omitted from Phase 1A/1B/2/3 charts (NOMADS reliability)
  nbm: "#BCBD22",     // olive
  hrrr: "#FF7F0E",    // amber — reserved; no chart work Phase 1–3 (short-range; future separate section)
  eps: "#E85002",     // same family as ECMWF
  gefs: "#1E6BB8",    // same family as GFS
} as const;

export const ENSEMBLE_COLORS = {
  eps_member: "rgba(232, 80, 2, 0.12)",
  eps_member_stroke: "rgba(232, 80, 2, 0.35)",
  eps_mean: "#E85002",
  eps_control: "#FFFFFF",
  eps_spread_fill: "rgba(232, 80, 2, 0.18)",
  gefs_member: "rgba(30, 107, 184, 0.12)",
  gefs_member_stroke: "rgba(30, 107, 184, 0.35)",
  gefs_mean: "#1E6BB8",
  gefs_spread_fill: "rgba(30, 107, 184, 0.18)",
} as const;

export const CHART_THEME = {
  background: "hsl(222 22% 8%)",        // matches .dark --background
  cardBackground: "hsl(222 22% 11%)",   // matches .dark --card
  axisLabel: "hsl(215 14% 55%)",       // matches --muted-foreground
  gridline: "hsla(0, 0%, 100%, 0.08)",
  tickFontSize: 11,
  titleColor: "hsl(210 20% 92%)",
  nowMarker: "#F59E0B",
  dayBoundary: "hsla(0, 0%, 100%, 0.15)",
} as const;
```

Colors must be imported from this file in every chart. **Never** hardcode hex in chart components.

### Axis conventions

| Element | Rule |
|---------|------|
| X-axis | Valid time (`valid_time` UTC from API) converted to **location timezone** (prop from Forecast page Open-Meteo geocoding). Do not add timezone to meteogram response. Format: `EEE h a` for <48 h; `MMM d` for multi-day. |
| Y-axis | Variable units suffix: `°F`, `in`, `mph`. Auto-scale with 5% headroom. |
| Now marker | Vertical dashed line at current time; color `CHART_THEME.nowMarker`; label "Now". |
| Day boundaries | Vertical gridlines at 00:00 local; style `CHART_THEME.dayBoundary`; slightly stronger than hourly gridlines. |
| Tooltip | Show valid time, model name, value + units; for precip also show fh. |

### `ChartContainer` component

**File:** `frontend/src/components/charts/ChartContainer.tsx`

| Slot | Prop |
|------|------|
| Title | `title: string` |
| Subtitle | `subtitle?: string` |
| Model pill filter | `filterSlot?: React.ReactNode` |
| Loading | `isLoading: boolean` — skeleton pulse |
| Error | `error?: string | null` — inline message, chart area preserved |
| Children | chart canvas |

Chart export (PNG/CSV download) is **out of scope** until Phase 2+.

Padding: `p-4 md:p-5`; background `bg-white/[0.03]`; border `border-white/10`; rounded `rounded-xl` — matches Forecast page cards.

### `ModelPillFilter` component

**File:** `frontend/src/components/charts/ModelPillFilter.tsx`

- Row of toggleable pills; each pill shows model short name + color dot from `MODEL_COLORS`.
- Props: `models: string[]`, `activeModels: Set<string>`, `onChange: (next: Set<string>) => void`, `entitledModels?: Set<string>`.
- **CONUS coverage:** When `lat/lon` is outside CONUS bbox (`-134, 24, -60, 55`), exclude `nbm` from pills **and** from the meteogram `models[]` request — no greyed-out pill, no tooltip. Silent omission.
- **Entitlements:** Hide pills for models the user is not entitled to (source: existing capabilities/entitlements data the frontend already consumes). Do not show pills that will never return data.
- State lives in parent tab (`ModelsTabContent` / `EnsemblesTabContent`); passed to all charts as `visibleModels`.

---

## 4. Phase 1A — Meteogram Endpoint + Temperature Chart

### Scope

Ship the meteogram backend, shared chart infrastructure, **Models top-level tab** content, model pill filter, multi-model temperature chart (hourly view only), and loading/error/empty states. Precipitation and wind sections are not rendered in this phase.

**Deliverables (ordered):**

0. Extract sampling logic to `backend/app/services/sampling.py` (**first task** — before any meteogram code; avoids circular imports with `main.py`)
1. Meteogram endpoint (`POST /api/v4/forecast/meteogram`)
2. Multi-model temperature chart (uPlot — **approved**, add dependency in this phase)
3. Model pill filter
4. Basic loading / error / empty states

### Backend

#### 4.0 Extract `sampling.py` (Phase 1A — task 0, before all other backend work)

Move COG sampling helpers from `backend/app/main.py` to `backend/app/services/sampling.py`. Update `/api/v4/sample` and `/api/v4/sample/batch` routes to import from the new module. `get_forecast_meteogram()` imports the same functions — no HTTP round-trips.

**Verification:** Existing `pytest backend/tests/test_sample_batch_api.py` passes unchanged after refactor.

#### 4.1 `get_forecast_meteogram()` in `forecast_page.py`

Implement the full contract in Section 2. Phase 1A must support any requested variable, but the frontend initially requests `variables: ["tmp2m"]` only.

| Model | API id | Runtime var (1A) | Region default | Forecast hours (native) |
|-------|--------|------------------|----------------|------------------------|
| ECMWF | `ecmwf` | `tmp2m` | `na` | 0–144 step 3; 150–360 step 6 |
| GFS | `gfs` | `tmp2m` | `na` | 0–240 step 3; 246–384 step 6 |
| AIFS | `aifs` | `tmp2m` | `na` | 0–360 step 6 (native 6-hourly points; no interpolation) |
| NBM | `nbm` | `tmp2m` | `conus` | 1–36 hourly; then 6-hourly to 264 |

**Coverage gating:** When `lat/lon` is outside CONUS bbox (`-134, 24, -60, 55`), backend may still accept `nbm` in the request but the frontend must omit it. Backend returns `unavailable` if requested for out-of-coverage locations.

#### 4.2 Route wiring in `main.py`

```python
class MeteogramRequestIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    models: list[str] = Field(..., min_length=1, max_length=8)
    variables: list[str] = Field(..., min_length=1, max_length=6)
    run_policy: dict[str, Any] = Field(default_factory=lambda: {"type": "latest_per_model"})
    include_members: bool = False
    region: str | None = None

@app.post("/api/v4/forecast/meteogram")
def forecast_meteogram(request: Request, body: MeteogramRequestIn, principal: ...):
    entitlements per model
    allowed, retry_after = _meteogram_rate_limit_allow(client_id)  # dedicated bucket, NOT _sample_rate_limit_allow
    # Recommended limit: 20 requests/min/IP
    payload = get_forecast_meteogram(...)
    return JSONResponse(content=payload, headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"})
```

Implement `_meteogram_rate_limit_allow` in `main.py` as a separate sliding-window counter from the sample endpoints. Do not share the sample rate-limit bucket — meteogram fan-out is heavier per request and must not starve map viewer sampling.

Leave `GET /api/v4/model-guidance` placeholder alive through Phase 1A. Return **`410 Gone`** after Phase 1B ships (more informative than `404` for a deliberately retired endpoint).

#### 4.3 Tests

**File:** `backend/tests/test_forecast_meteogram_api.py`

- Fixture: published artifacts for `gfs` + `ecmwf` (mirror `test_sample_batch_api.py` pattern).
- Assert: multi-model `tmp2m` response shape; partial failure returns null not 500; cache headers present.

### Frontend

#### 4.4 Forecast page structure (Phase 1A)

Replace `ModelsTab` placeholder with full Models tab content. Add `ensembles` to `TabId` in `forecast.tsx` only if needed for routing stubs — the Ensembles top-level tab body ships in Phase 2.

```text
forecast.tsx TABS (updated)
├── hourly | 7day | extended | models | ensembles | discussion
│
Models tab (Phase 1A)
├── ModelPillFilter
└── Section
    └── #temperature — MultiModelTemperatureChart

Ensembles tab — not rendered in Phase 1A (Phase 2)
```

Precipitation (`#precipitation`) and Wind (`#wind`) section anchors are **not rendered** in Phase 1A.

**Files to create/modify:**
- `frontend/src/lib/chart-constants.ts` (Section 3)
- `frontend/src/components/charts/ChartContainer.tsx`
- `frontend/src/components/charts/ModelPillFilter.tsx`
- `frontend/src/components/charts/UplotChart.tsx` (uPlot wrapper)
- `frontend/src/components/model-guidance/ModelsTabContent.tsx`
- `frontend/src/components/model-guidance/MultiModelTemperatureChart.tsx`
- `frontend/src/hooks/useMeteogram.ts` — requests `variables: ["tmp2m"]`; builds `models[]` from entitled + in-coverage set (excludes NBM outside CONUS)
- `frontend/src/pages/forecast.tsx` — replace `ModelsTab` body with `ModelsTabContent`; add `ensembles` to `TabId` when Phase 2 begins

#### 4.5 Multi-model hourly temperature (Phase 1A)

| Property | Value |
|----------|-------|
| Models | ECMWF, GFS, AIFS, NBM |
| Variable | `tmp2m` |
| Y-axis | °F |
| Lines | One per visible model; color from `MODEL_COLORS` |
| X-axis | Valid time; day-boundary gridlines; Now marker |
| View mode | **Hourly only** (native points per model; no interpolation) |
| AIFS cadence | 6-hourly native points only — fewer points on chart than GFS/ECMWF; no special treatment |
| Missing data | Gap in line; no interpolation |
| Empty state | `ChartContainer` message when all models return no points: "No temperature guidance available for this location." |

**Daily high/low toggle:** deferred to Phase 1B.

#### 4.6 Model pill filter (Phase 1A)

- Renders above the temperature chart.
- Default: all eligible models active (ECMWF, GFS, AIFS, plus NBM when inside CONUS and entitled).
- Toggling a pill shows/hides that model's line on the temperature chart.
- State: `activeModels: Set<string>` in `ModelsTabContent`.
- `useMeteogram` must not include `nbm` in `models[]` when location is outside CONUS bbox.

#### 4.7 Loading / error / empty states (Phase 1A)

| State | Behavior |
|-------|----------|
| Loading | `ChartContainer` skeleton while `useMeteogram` is in flight |
| Error | Network or 429: inline message in `ChartContainer`; retry button |
| Partial data | Chart renders available models; subtitle notes "Some models unavailable" when any series has `status: partial` |
| Empty | All models unavailable: empty-state copy in chart area (not a blank canvas) |

#### 4.8 State management (Phase 1A)

| State | Location | Type |
|-------|----------|------|
| `activeModels` | `ModelsTabContent` | `Set<string>` |
| Meteogram data | `useMeteogram` | React Query or `useEffect` + `useState` |

### Phase 1A verification checklist

**Status: COMPLETE — shipped to production (recorded 2026-07-04).** Checklist retained for regression reference.

- [ ] `pytest backend/tests/test_sample_batch_api.py` passes after `sampling.py` extract.
- [ ] `curl -s -X POST http://localhost:8000/api/v4/forecast/meteogram -H 'Content-Type: application/json' -d '{"lat":45.5,"lon":-93.2,"models":["gfs","ecmwf","aifs"],"variables":["tmp2m"],"run_policy":{"type":"latest_per_model"}}' | jq '.series | keys'` returns `["aifs","ecmwf","gfs"]` (or subset with partial status).
- [ ] Same request with invalid model returns 200 with omitted model or 400 — not 500.
- [ ] Response includes `Cache-Control: public, max-age=300`.
- [ ] `pytest backend/tests/test_forecast_meteogram_api.py -v` passes.
- [ ] Meteogram endpoint returns `429` when `_meteogram_rate_limit_allow` exceeded (20/min/IP).
- [ ] `/forecast` → **Models** top-level tab shows model pill filter and Temperature section only.
- [ ] Temperature chart renders ≥3 model lines with distinct colors from `chart-constants.ts`.
- [ ] Toggling model pill hides/shows series on temperature chart.
- [ ] Location change triggers new meteogram fetch.
- [ ] Loading skeleton, error message, and empty state each render correctly inside `ChartContainer`.
- [ ] Location outside CONUS: NBM pill absent and omitted from meteogram `models[]`.
- [ ] No precipitation or wind charts visible.

---

## 5. Phase 1B — Precipitation, Wind, and Daily Toggle

### Scope

Complete the **Models** top-level tab. Builds on Phase 1A meteogram endpoint and chart infrastructure. No new backend routes — extend the frontend meteogram request to include additional variables.

**Deliverables (ordered):**

5. Cumulative precip chart
6. Per-model 6-hr precip detail panel
7. Wind chart
8. Daily high/low toggle on temperature chart

**Prerequisite:** Phase 1A verification checklist fully passed.

### Backend

No new endpoints. Meteogram already supports `precip_total` and `wspd10m`; Phase 1B frontend expands the `useMeteogram` request:

```json
{
  "models": ["ecmwf", "gfs", "aifs", "nbm"],
  "variables": ["tmp2m", "precip_total", "wspd10m"],
  "run_policy": { "type": "latest_per_model" }
}
```

| Model | Variables added in 1B | Notes |
|-------|----------------------|-------|
| ECMWF, GFS, AIFS | `precip_total` | Cumulative from run init; respect manifest `min_fh` |
| ECMWF, GFS, NBM | `wspd10m` | Derived from u/v components in builder |
| NBM | `precip_total` | CONUS only |

**6-hour step derivation (client-side):** `step_6h[i] = cumul[fh_i] - cumul[fh_{i-6}]` at 6-hourly boundaries from cumulative series in meteogram response. No extra API call.

**Daily high/low (client-side):** Group `tmp2m` points by local calendar day in location timezone; `high = max`, `low = min`. Native points only for all models (AIFS remains 6-hourly).

### Frontend

#### 5.1 Layout additions

```text
ModelsTabContent (updated)
├── ModelPillFilter
└── Sections (anchor nav)
    ├── #temperature — MultiModelTemperatureChart (+ daily toggle)
    ├── #precipitation — MultiModelCumulativePrecipChart + PrecipDetailPanel
    └── #wind — MultiModelWindChart
```

**Files to create:**
- `frontend/src/components/model-guidance/MultiModelCumulativePrecipChart.tsx`
- `frontend/src/components/model-guidance/PrecipDetailPanel.tsx`
- `frontend/src/components/model-guidance/MultiModelWindChart.tsx`

**Files to modify:**
- `frontend/src/components/model-guidance/MultiModelTemperatureChart.tsx` — add hourly/daily toggle
- `frontend/src/hooks/useMeteogram.ts` — request all three variables
- `frontend/src/components/model-guidance/ModelsTabContent.tsx` — precip/wind sections, `precipDetailExpanded` state

#### 5.2 Multi-model cumulative precipitation

| Property | Value |
|----------|-------|
| Models | ECMWF, GFS, NBM, AIFS (same coverage rules as Phase 1A) |
| Variable | `precip_total` |
| Y-axis | inches (cumulative from run init) |
| Lines | One per visible model (pill filter applies) |
| Subtitle | Show per-model init times, e.g. `ECMWF init 00z · GFS init 06z` — different `latest_per_model` inits can make cumulative lines diverge for reasons unrelated to QPF |

#### 5.3 Per-model 6-hr precipitation detail

| Property | Value |
|----------|-------|
| Layout | Collapsible 'Radix Collapsible' element below cumulative chart; default collapsed |
| Content | One sub-chart per model (ECMWF, GFS, NBM, AIFS) |
| Bars | 6-hr QPF from cumulative differences |
| Line overlay | Cumulative precip (same meteogram data) |
| Data | No additional fetch |

#### 5.4 Multi-model 10 m wind speed

| Property | Value |
|----------|-------|
| Models | ECMWF, GFS, NBM |
| Variable | `wspd10m` |
| Y-axis | mph |
| Lines | Native temporal resolution per model |

#### 5.5 Daily high/low temperature toggle

| Property | Value |
|----------|-------|
| Location | `MultiModelTemperatureChart` header |
| Toggle | **Hourly** (Phase 1A behavior) vs **Daily high/low** |
| Daily view | Two series per model (high line solid, low line dashed) OR paired markers — use distinct stroke dash per series type, same model color |
| Data | Client-side aggregation from existing `tmp2m` meteogram data; no new network request |

#### 5.6 State management (Phase 1B additions)

| State | Location | Type |
|-------|----------|------|
| `precipDetailExpanded` | `ModelsTabContent` | `boolean` |
| `tempViewMode` | `MultiModelTemperatureChart` | `"hourly" \| "daily"` |

Pill filter state from Phase 1A applies to all three chart sections simultaneously.

Deprecate `GET /api/v4/model-guidance`: return **`410 Gone`** after Phase 1B ships.

### Phase 1B verification checklist

**Status: COMPLETE — shipped to production (recorded 2026-07-04).** Checklist retained for regression reference.

- [ ] Meteogram request includes `tmp2m`, `precip_total`, `wspd10m`; all three return data for at least one model at a CONUS test point.
- [ ] `/forecast` → **Models** top-level tab shows Temperature, Precipitation, and Wind section headers (anchor nav works).
- [ ] Cumulative precip chart renders ≥2 model lines.
- [ ] Expanding precip detail panel shows per-model bar + cumulative line sub-charts without a second network request.
- [ ] Wind chart renders ECMWF, GFS, NBM.
- [ ] Daily high/low toggle changes temperature chart without new network request.
- [ ] Toggling model pill hides/shows series on **all three** chart sections.
- [ ] Cumulative precip chart subtitle shows per-model init times when models differ.
- [ ] `GET /api/v4/model-guidance` returns `410 Gone`.

---

## 6. Phase 2 — Ensemble Mean, Spread, and Probability Charts (Ensembles Top-Level Tab)

### What can be derived from mean-only data

| Product | Derivable from mean? | Phase 2 action |
|---------|---------------------|----------------|
| Ensemble mean temperature line | **Yes** — `tmp2m` + `ensemble_view=mean` → `tmp2m__mean` | Ship chart |
| Ensemble mean cumulative precip | **Yes** — `precip_total__mean` | Ship chart |
| Temperature spread envelope (min/max, P10/P90) | **No** — requires per-member or precomputed spread fields | Placeholder card |
| Precip probability thresholds P(>0.10"), etc. | **No** — requires per-member counts | Placeholder card |
| Mean ± 1σ band from single mean field | **No** — mathematically invalid | Do not ship |

### Backend (Phase 2)

No new artifacts. Extend meteogram calls to include:

```json
{
  "models": ["eps", "gefs"],
  "variables": ["tmp2m", "precip_total"],
  "run_policy": { "type": "latest_per_model" }
}
```

Sampler uses `ensemble_view=mean` implicitly for `eps` and `gefs` (existing `_runtime_var_id_for_request` behavior).

**Optional Phase 2 enhancement:** Add `spread` variable type to meteogram response computed at sample time if spread COGs are added later — **out of scope** unless Brian publishes spread artifacts.

### Frontend — Ensembles top-level tab

Add `ensembles` to `TabId` and `TABS` in `forecast.tsx`. Render `EnsemblesTabContent` when active.

```text
EnsemblesTabContent (forecast.tsx activeTab === "ensembles")
├── ModelPillFilter (scoped to EPS + GEFS only)
├── EnsembleMeanTemperatureChart
├── EnsemblePrecipProbabilityCard (placeholder)
├── EnsembleTemperatureSpreadChart (placeholder)
└── EnsembleMeanPrecipChart
```

**File:** `frontend/src/components/model-guidance/EnsemblesTabContent.tsx`

### Chart specifications

#### Ensemble temperature spread — **Phase 3 dependency**

Show `ChartContainer` with `error` slot:

> "Temperature spread requires per-member ensemble data. Coming soon."

Do not render fake spread from mean.

#### Ensemble precipitation probability thresholds — **Phase 3 dependency**

Placeholder card with banner: **"Coming in a future update"** (requires per-member data). Table structure may appear, but cells are **blank** — not dashes, not fabricated values.

| Threshold | 24 hr (fh 24) | 7 day (fh 168) | 15 day (fh 360) |
|-----------|---------------|----------------|-----------------|
| P(>0.10") | | | |
| P(>0.25") | | | |
| P(>0.50") | | | |
| P(>1.00") | | | |

**Locked window definitions (Phase 3):** Model calendar time from run init, not rolling windows. Both EPS and GEFS use **fh 360** for the 15-day column (cap GEFS at 360 despite 384h extent — consistent label).

Data source when available (Phase 3): count of members where cumulative `precip_total` at window fh exceeds threshold, divided by member count.

#### Ensemble mean temperature plume

| Property | Value |
|----------|-------|
| Models | EPS mean, GEFS mean on shared chart |
| Variable | `tmp2m` (mean) |
| Lines | `ENSEMBLE_COLORS.eps_mean`, `ENSEMBLE_COLORS.gefs_mean` |
| Envelope | None in Phase 2 |

#### Ensemble mean cumulative precip

| Property | Value |
|----------|-------|
| Models | EPS mean, GEFS mean |
| Variable | `precip_total` (mean) |
| Y-axis | inches cumulative |

### Phase 2 verification checklist

**Status: COMPLETE — shipped to production (recorded 2026-07-04).** Additional mean charts may be added later, but the tab as specified here is done. Checklist retained for regression reference.

- [ ] `curl` meteogram with `models=["eps","gefs"]` returns `tmp2m` and `precip_total` mean series with `__mean` artifacts.
- [ ] Ensembles **top-level tab** visible; pill filter shows EPS and GEFS only.
- [ ] Mean temperature chart shows two lines (EPS, GEFS).
- [ ] Mean cumulative precip chart shows two lines.
- [ ] Spread chart shows placeholder message (not empty crash).
- [ ] Probability card shows banner and blank table cells (not dashes or fabricated values).
- [ ] `pytest backend/tests/test_api_eps_ensemble_contract.py` and `test_api_gefs_ensemble_contract.py` still pass.

---

## 7. Phase 3 — Per-Member Ensemble Charts

**Blocked on:**
1. Phase 2 verified — **done (2026-07-04)**
2. GEFS and EPS on `CARTOSKY_BINARY_SAMPLING_MODELS` with COG writes off (migration plan Phase F for both models)
3. `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` Phases 0–4 complete: sizing spike (binary edition) with Brian's recorded sign-off, scheduler design approval, and member publish live for both models

Do not start any chart work in this section until gate 3 passes. Do not start scheduler work from this document at all — the scheduler extension, storage schema, fetch strategy, packing resolution, and retention policy previously specified here are owned by the member pipeline plan (member artifacts are **grid binaries, not value COGs**).

### Scheduler extension and storage schema — moved

See `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md`. Summary of what changed relative to the original version of this section: member artifacts are binary-only (`fh{NNN}.l0.u16.bin` + meta sidecar under `published/{model}/{run}/{var}__m{NN}/`); the member build runs a profile-parameterized slim path; naming (`__m{NN}`, `__control`, zero-padded) is unchanged and locked there.

### Meteogram extension

When `include_members: true`:

```json
"variables": {
  "tmp2m": {
    "units": "F",
    "points": [{ "fh": 0, "value": 45.2, "valid_time": "..." }],
    "members": {
      "mean": { "points": [...] },
      "control": { "points": [...] },
      "m01": { "points": [...] },
      ...
    }
  }
}
```

Reject `include_members` for models without member artifacts (400 with clear error).

### Chart specifications

#### Temperature plume (spaghetti)

| Property | EPS | GEFS |
|----------|-----|------|
| Member lines | 50 thin lines, `ENSEMBLE_COLORS.eps_member_stroke`, opacity 0.35 | 30 lines, `gefs_member_stroke` |
| Mean | Bold `eps_mean` | Bold `gefs_mean` |
| Control | White / dashed `eps_control` | White / dashed (GEFS control via `tmp2m__control`) |
| Performance | uPlot canvas; downsample to pixel width if >2000 points | Same |

#### Precipitation plume

Same spaghetti pattern for cumulative `precip_total` per member.

#### Snowfall member distribution

| Property | Value |
|----------|-------|
| Type | Sorted bar histogram by accumulation bucket: 0–2", 2–4", 4–6", 6–8", 8–12", 12"+ |
| X-axis | Bucket (not member number) |
| Y-axis | Member count |
| Data | Final `snowfall_total` per member at max available fh |
| Value | Highest TWF-value chart — prioritize polish |

#### Snowfall member detail panel

Expandable panel: table of member id + total accumulation, sorted descending.

#### Seasonal gating

```typescript
const SNOW_SEASON_MONTHS = new Set([10, 11, 12, 1, 2, 3, 4]); // Oct–Apr
```

If current month ∉ `SNOW_SEASON_MONTHS`: hide snowfall charts OR show card with badge "Snowfall season (Oct–Apr)" and muted placeholder.

### Phase 3 verification checklist

- [ ] All gates in `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` Phases 0–4 passed (spike + sign-off, scheduler design approval, member publish live for both models, retention verified). Pipeline-level checks (disk, inodes, RSS, retention sweep) live in that plan — do not duplicate them here.
- [ ] Meteogram with `include_members: true` returns member arrays for EPS/GEFS, sampled from member **grid binaries** via the shared binary sampler.
- [ ] Spaghetti chart renders 50+ lines without frame drops on M1 MacBook / Chrome.
- [ ] Snowfall histogram shows bucket counts matching manual member tally at test point.
- [ ] Snowfall detail panel sorts members by accumulation.
- [ ] Snowfall charts hidden or gated outside Oct–Apr.
- [ ] Probability threshold card populated with real percentages at fh 24 / 168 / 360 (spot-check vs manual member count).
- [ ] Temperature spread chart shows P10/P90 envelope from members.

---

## 8. Open Questions (Deferred — Phase 3 Only)

All product decisions for Phases 1A, 1B, and 2 are **locked** and documented inline in Sections 2–7. The former deferred items 1–4 (EPS control member ID, GEFS upstream member count, EPS `snowfall_total` derivation complexity, storage budget approval) have **moved to `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md`** (its open-decisions section), where they are tracked alongside the new binary-era decisions (member retention count, display-prep resolution for served member maps). This document has no remaining open pipeline questions of its own — its only open Phase 3 work is chart implementation once the member pipeline plan's gates pass.

### Locked decisions reference (not open)

For agent convenience, these are resolved — do not re-litigate:

| Topic | Locked decision |
|-------|-----------------|
| AIFS / 6-hourly models | Native points only; no interpolation; fewer points on chart is expected |
| NBM outside CONUS | Hide pill; omit from `models[]` request silently |
| AIGFS | Omitted Phase 1A–3; color constant only |
| HRRR | No chart work Phase 1–3; color constant only; future short-range section |
| `run_policy` | `latest_per_model` only in v1 |
| `GET /api/v4/model-guidance` | `410 Gone` after Phase 1B |
| Sampling extract | `backend/app/services/sampling.py` first task in Phase 1A |
| Precip probability windows | fh 24 / 168 / 360 (both ensembles); Phase 3 |
| Entitlements | Per-model `not_entitled`; no whole-request 403; hide pills |
| Timezone | Client-side from Forecast page context; API returns UTC only |
| Cumulative precip subtitle | Per-model init times in Phase 1B |
| uPlot | Approved; Phase 1A |
| Phase 2 probability UI | Banner + blank cells, not dashes |
| Cache key | Include `run_id` per model; accept miss on cycle update |
| Meteogram rate limit | `_meteogram_rate_limit_allow`, 20/min/IP, separate from sample |

---

*Document version: 2026-07-04 (binary-era revision: Phases 1A/1B/2 marked complete; sampling-substrate claims corrected for the value-COG → binary migration; member pipeline, storage schema, and sizing spike superseded by `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md`). Previous version: 2026-06-23.*
