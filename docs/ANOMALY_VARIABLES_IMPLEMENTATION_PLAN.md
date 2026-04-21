# Anomaly Variables Implementation Plan

## Summary

This plan adds anomaly-based weather variables to CartoSky, starting with ensemble-first raw climatology departures for temperature and 500 mb height.

Initial v1 scope:
1. GEFS support for temperature anomalies and 500 mb height anomalies.
2. EPS support for the same anomaly products after the shared pipeline is in place.
3. Repo-owned climatology assets stored and versioned under the CartoSky data root.
4. Raw departure from climatology as the first public anomaly type.
5. Architecture that can later support additional anomaly types such as percent-of-normal and standardized anomaly without rewriting the base pipeline.
6. ERA5 is the default baseline source archive for v1 climatology generation.

The cleanest fit for the current codebase is:
1. Treat anomaly products as first-class variable keys, not as a global UI toggle.
2. Reuse the existing model capability and runtime artifact mapping pattern already used by GEFS and EPS ensemble means.
3. Add a new derive family in the backend builder that computes `forecast - climatology` from repo-owned climatology assets.
4. Keep climatology resolution and anomaly math in the backend pipeline, not in the frontend or in ad hoc selector logic.

This is the best fit for the current repo because:
1. Supported variables are already modeled as capability-driven first-class products, with the frontend reading them from `/api/v4/capabilities` and `/api/v4/bootstrap`.
2. GEFS and EPS already distinguish public canonical variables from runtime-only ensemble artifacts via `artifact_map`.
3. Derived products already have a formal strategy system in `backend/app/services/builder/derive.py`.
4. Grid packing, legends, variable groups, and UI labels are already keyed by variable ID.
5. The current viewer model has no anomaly-mode concept, so standalone variable keys are the least disruptive path.

## Repo-Specific Decisions

These decisions are fixed for this plan unless explicitly changed later.

1. V1 anomaly products are raw departures from climatology only.
2. V1 focuses on GEFS and EPS, not deterministic GFS, ECMWF, HRRR, or NAM.
3. V1 includes temperature anomalies and 500 mb height anomalies only.
4. Repo-owned climatology assets are the source of truth. The backend does not depend on a live third-party anomaly API at request or build time.
5. Anomaly products are added as normal variable IDs in model registries, capabilities, manifests, and grid artifacts.
6. Public canonical variables remain stable even if runtime artifacts differ by ensemble view.
7. The backend architecture should allow future anomaly families such as percent-of-normal and sigma anomalies, but v1 should not expose them publicly.
8. Raw-source ingestion stays separate from climatology generation. The main climatology asset builder consumes a normalized staged raster format rather than raw ERA5 delivery formats directly.
9. Heavy climatology acquisition and prep happen off-prod. Production receives only staged normalized rasters or final climatology baseline assets.

## Non-Goals For V1

These are explicitly out of scope to keep the first rollout focused.

- No precipitation anomalies in v1.
- No percent-of-normal anomaly fields in v1.
- No standardized anomaly or sigma departure fields in v1.
- No deterministic-model anomaly rollout in v1.
- No anomaly comparison mode or special split-screen UX.
- No user-selectable anomaly baseline in the viewer.
- No climatology generation pipeline for every possible variable before the first anomaly products ship.
- No browser-side anomaly math.

## Product Scope

### V1 Variables

The v1 rollout order is fixed and should be reflected consistently in implementation sequencing, testing, and any follow-up specs.

First slice:

- `tmp2m_anom`

Second slice:

- `hgt500_anom`

Third slice:

- `tmp850_anom`

Notes:

1. `tmp2m_anom` is the safest first proving ground for the shared anomaly pipeline and the clearest initial anomaly product for users.
2. `hgt500_anom` comes next because it validates upper-air anomaly support and the contour-overlay behavior needed for synoptic workflows.
3. `tmp850_anom` is third because it reuses the temperature-anomaly machinery after both the surface and upper-air anomaly slices are already proven.

### Model Scope

V1 model order:

1. GEFS
2. EPS

Deferred after v1 validation:

1. Deterministic GFS
2. Deterministic ECMWF
3. Precipitation anomalies

## Variable Contract

Anomaly variables need a stable public contract before code is written.

### Canonical Public Shape

Each anomaly variable should define:

1. Canonical public key.
2. Display name.
3. Units.
4. Anomaly type.
5. Climatology version.
6. Baseline resolution metadata.
7. Optional contour overlay metadata when relevant.

Recommended v1 public display names:

- `tmp2m_anom`: `Surface Temperature Anomaly`
- `tmp850_anom`: `850mb Temperature Anomaly`
- `hgt500_anom`: `500mb Height Anomaly`

Recommended units:

- Temperature anomalies: `F` for user-facing display consistency with the current viewer.
- 500 mb height anomalies: `dam`

Recommended internal anomaly metadata:

- `anomaly_kind: departure`
- `baseline_kind: climatology`
- `climatology_version: <version>`

### Baseline Source Archive

The default v1 baseline source archive is ERA5.

Resolved source choices:

1. Use ERA5 hourly single-level data for `tmp2m`.
2. Use ERA5 hourly pressure-level data for `tmp850` and `hgt500` inputs.
3. Treat ERA5 as the scientific source of truth for climatology generation unless a later document explicitly changes that contract.

V1 ingestion architecture:

1. Raw ERA5 archive.
2. Separate prep or staging step.
3. Normalized internal staged rasters.
4. Climatology asset builder.
5. Repo-owned climatology baseline assets under the CartoSky data root.

The main climatology asset builder should not be taught to directly ingest arbitrary raw-source formats such as GRIB, NetCDF, or Zarr. Those archive-specific details belong in the prep step.

The public API does not need to expose all of that in v1, but the backend should retain it in sidecars or internal metadata so future anomaly types do not require redesign.

## Naming And Runtime Artifact Strategy

The current codebase already supports public canonical variables mapping to hidden runtime artifacts for ensemble products. Anomalies should use the same pattern.

### Public Canonical Variables

Examples:

- `tmp2m_anom`
- `tmp850_anom`
- `hgt500_anom`

### Runtime Ensemble Artifacts

Examples:

- `tmp2m_anom__mean`
- `tmp850_anom__mean`
- `hgt500_anom__mean`

### Why This Shape

This matches the existing GEFS and EPS ensemble model pattern:

1. Public variable remains stable across the viewer and manifest contract.
2. Runtime build artifact can remain ensemble-specific.
3. The frontend does not need a separate anomaly or ensemble artifact awareness layer.
4. Runtime-only artifacts can continue to be hidden through `frontend.internal_only` metadata.

## Architecture Overview

The anomaly pipeline should be built as a standard derive path:

```text
model forecast field
  + matching climatology field
  -> anomaly derive strategy
  -> grid packing / sidecar / legend metadata
  -> normal viewer variable selection
```

### Core Principle

Climatology is not a selector hint against upstream forecast inventory. It is a separate repo-owned data source resolved by backend logic.

That means:

1. Existing `VarSelectors` remain focused on upstream forecast inputs.
2. Climatology lookup logic lives in a dedicated anomaly resolver service or inside the derive layer behind a clean helper.
3. The derive strategy owns unit alignment and anomaly subtraction.

## Climatology Asset Strategy

V1 should assume repo-owned climatology assets under the main data root.

### Requirements

Climatology assets must be:

1. Versioned.
2. Immutable once published.
3. Independently replaceable without changing public variable keys.
4. Usable offline during normal build execution.
5. Mappable by valid time and field.

### Source And Staging Contract

The staging boundary is fixed for v1.

Rules:

1. ERA5 acquisition and decode work happen before climatology generation, not inside the main builder.
2. The prep step converts raw ERA5 inputs into a normalized internal raster format suitable for repeatable downstream aggregation.
3. The climatology asset builder consumes only the staged normalized rasters.
4. Production does not need the full raw ERA5 archive for normal anomaly operations.
5. Production should receive only staged normalized rasters when a prod-side build is required, or preferably only the final climatology baseline assets.

### ERA5 Staged Raster Contract

The normalized staged raster format for the v1 ERA5 path is fixed.

Layout:

```text
<stage_root>/
  era5/
    single-levels/
      tmp2m/
        1991/
          1991010100_tmp2m.tif
          1991010106_tmp2m.tif
          ...
    pressure-levels/
      tmp850/
        1991/
          1991010100_tmp850.tif
          ...
      hgt500/
        1991/
          1991010100_hgt500.tif
          ...
```

Rules:

1. The staging root is off-prod by default.
2. Filenames must encode valid time as `YYYYMMDDHH` so the climatology asset builder can resolve day-of-year and synoptic hour without archive-specific parsing.
3. Staged rasters are single-band GeoTIFFs in `EPSG:4326` on the normalized ERA5 lat-lon grid.
4. `tmp2m` and `tmp850` staged rasters should be stored in Kelvin.
5. `hgt500` staged rasters should be stored in geopotential meters.
6. Stage only `00/06/12/18Z` valid times for the first pilot.
7. The climatology asset builder remains responsible for warping staged rasters to the consuming model target grid.

This contract gives the project a stable intermediate representation without coupling the main builder to ERA5 raw delivery formats.

### Recommended Data-Root Layout

```text
data/
  climatology/
    v1/
      gefs/
        baseline/
          tmp2m/
            doy_001_h00.tif
            doy_001_h06.tif
            ...
          hgt500/
            doy_001_h00.tif
            ...
          tmp850/
            doy_001_h00.tif
            ...
      eps/
        baseline/
          tmp2m/
            doy_001_h00.tif
            ...
          hgt500/
            doy_001_h00.tif
            ...
          tmp850/
            doy_001_h00.tif
            ...
```

Notes:

1. The model-family folder must be explicit so GEFS climatology and EPS climatology are never conflated operationally.
2. The `baseline/` tree is the only climatology source of truth used during anomaly subtraction.
3. Published anomaly outputs do not live under `data/climatology/`.
4. Published anomaly outputs should continue to use the normal published and staging artifact trees keyed by model, run, and variable.
5. The climatology storage contract and the published artifact contract must remain separate so baseline assets cannot be confused with viewer-facing anomaly outputs.

### Fixed Directory Contract

The directory contract for v1 is:

1. Climatology baselines live only under `data/climatology/<version>/<model_family>/baseline/<field>/...`.
2. Published anomaly artifacts live only under the existing normal artifact trees such as `published/<model>/<run>/<variable>` and `staging/<model>/<run>/<variable>` under the configured data root.
3. There is no `anomaly/` subtree under `data/climatology/` in v1.
4. Model-family separation is mandatory in both places. GEFS anomalies consume GEFS climatology assets, and EPS anomalies consume EPS climatology assets.

Illustrative published-artifact examples:

```text
data/
  published/
    gefs/
      2026042100/
        tmp2m_anom/
        hgt500_anom/
        tmp850_anom/
    eps/
      2026042100/
        tmp2m_anom/
        hgt500_anom/
        tmp850_anom/
```

This separation is intentional:

1. `data/climatology/` is long-lived versioned baseline input.
2. `data/published/` and `data/staging/` are run-scoped operational outputs.
3. The derive step reads from the first and writes to the second.

The exact extension can change, but the path scheme should encode:

1. Climatology version.
2. Model family.
3. Base field.
4. Day-of-year.
5. Synoptic hour bucket.

### Baseline Time Alignment

V1 should align climatology by valid time, not by forecast lead.

Recommended key:

1. Day of year.
2. UTC synoptic hour bucket.

The v1 contract is leap-day-aware.

Rules:

1. The climatology key space includes `366` day-of-year buckets, not a normalized `365`-day climatology.
2. February 29 remains an explicit baseline bucket in leap years.
3. For `00/06/12/18Z` products, a complete field produces `366 x 4 = 1464` baseline assets.
4. Any future move to a `365`-day normalized climatology would require an explicit contract change and a new climatology asset version.

This is the least surprising for temperature and height anomalies because it ties the anomaly to the atmosphere expected at the valid time, not just to model lead structure.

### Seasonal Smoothing

V1 should use daily climatology with seasonal smoothing baked into the generated baseline assets.

Rules:

1. Seasonal smoothing is an offline climatology-generation concern.
2. The anomaly derive step should consume already-smoothed baseline assets.
3. Runtime anomaly subtraction should not expose smoothing controls, smoothing windows, or alternate smoothing modes.
4. Any future smoothing changes should be handled through a new climatology asset version, not runtime branching.

This keeps the runtime pipeline deterministic and avoids turning baseline interpretation into a viewer concern.

### Open Design Choice Already Resolved For V1

The plan should not attempt to support both lead-based and valid-time-based climatology in v1. Valid-time alignment is the primary contract.

The plan also should not fold raw-source decoding into the main climatology builder. A separate ERA5 prep step is the v1 contract.

## Backend Design

### 1. Model Registry Additions

Add anomaly variables to the model registries and capability catalogs for GEFS and EPS.

Likely files:

- `backend/app/models/gefs.py`
- `backend/app/models/eps.py`

For each supported anomaly variable:

1. Add public canonical variable capability.
2. Add hidden runtime artifact capability when needed.
3. Add color map ID.
4. Add units.
5. Add group and order.
6. Add ensemble artifact mapping for public canonical keys.

Recommended v1 grouping:

- `tmp2m_anom`: `Temperature`
- `tmp850_anom`: `Temperature`
- `hgt500_anom`: `Dynamics`

### 2. Base Components

Each anomaly product depends on a raw forecast base field.

Recommended mappings:

- `tmp2m_anom` -> base field `tmp2m`
- `tmp850_anom` -> base field `tmp850`
- `hgt500_anom` -> base field `hgt500`

For GEFS and EPS runtime artifacts, the derive layer should resolve the correct runtime base field through the existing ensemble artifact mapping behavior.

Examples:

- `tmp2m_anom` with `ensemble_view=mean` -> runtime forecast input `tmp2m__mean`
- `tmp850_anom` with `ensemble_view=mean` -> runtime forecast input `tmp850__mean`
- `hgt500_anom` with `ensemble_view=mean` -> runtime forecast input `hgt500__mean`

### 3. New Derive Strategy Family

Add a generic anomaly derive strategy in `backend/app/services/builder/derive.py`.

Recommended direction:

1. One reusable resolver for climatology-backed departures.
2. Separate strategy IDs per output variable only if the input requirements differ materially.

Two acceptable implementation shapes:

#### Option A: One generic strategy per anomaly family

Examples:

- `anomaly_departure`

This strategy reads metadata from selectors or capability constraints such as:

- `base_component`
- `climatology_field`
- `anomaly_units`

#### Option B: One strategy per output variable

Examples:

- `tmp2m_anomaly_departure`
- `tmp850_anomaly_departure`
- `hgt500_anomaly_departure`

Recommended choice: Option A, with variable metadata carrying the field-specific resolution details. It is less repetitive and better aligned with the request to architect for future anomaly types from day one.

### 4. New Climatology Resolver

Add a dedicated service or helper for loading climatology assets.

Recommended file:

- `backend/app/services/climatology.py`

Responsibilities:

1. Resolve climatology asset path from model, field, valid time, and version.
2. Load climatology raster and metadata.
3. Validate CRS, transform, shape, and units.
4. Cache climatology reads when safe.
5. Emit explicit errors for missing baseline assets.

Do not bury climatology file-path logic inside the derive strategy directly. Keep it factored so future anomaly types and future asset-generation workflows can reuse it.

### 5. Grid Alignment

Grid alignment is fixed for v1.

Rules:

1. Climatology assets must be generated pre-aligned to the exact backend output grid the anomaly derive step will subtract against.
2. Runtime reprojection should be avoided in the anomaly derive step.
3. Forecast data may still flow through the normal backend grid-prep path, but climatology should already be on the target subtraction grid.
4. If a climatology asset does not match the expected backend output grid, the build should fail explicitly rather than silently reprojecting.

This reduces runtime complexity, removes a major source of spatial drift, and makes frozen anomaly validation more meaningful.

### 6. Unit Handling

Anomaly subtraction must happen in a consistent unit space.

Rules:

1. Convert forecast field and climatology field into the same native subtraction units before differencing.
2. Only convert to final display units after the anomaly is computed, if needed.
3. Sidecar metadata must reflect final user-facing anomaly units, not internal subtraction units.

Recommended v1 handling:

1. Temperature anomalies can be computed in C or K internally, then exposed in F if desired.
2. Height anomalies should be computed and exposed in decameters for user-facing consistency.

### 7. Grid Packing

Add anomaly variables to `backend/app/services/grid.py`.

This is mandatory for any buildable anomaly field.

Recommended packing direction:

1. Use signed-friendly ranges encoded through scale and offset.
2. Bias around a symmetric zero-centered display range.
3. Keep enough precision for subtle anomalies without over-inflating file size.

Illustrative packing targets:

- `tmp2m_anom`: scale `0.1`, offset `-80.0`, units `F`
- `tmp850_anom`: scale `0.1`, offset `-50.0`, units `F` or `C` depending on final choice
- `hgt500_anom`: scale `0.1` or `1.0` depending on precision validation, offset centered around the expected decameter anomaly range, units `dam`

Exact numeric ranges should be validated against climatology samples before finalizing.

### 8. Sidecar Metadata

Anomaly sidecars should include at minimum:

1. Final units.
2. Min and max.
3. Climatology version.
4. Anomaly kind.
5. Baseline metadata sufficient for debugging.
6. Reference period.
7. Baseline model family.

Recommended additional sidecar keys:

- `anomaly_kind: departure`
- `baseline_kind: climatology`
- `baseline_version: v1`
- `baseline_alignment: valid_time`
- `reference_period: 1991-2020`
- `baseline_model_family: gefs` or `eps`

## Frontend Design

### 1. Variable Selection

Anomaly products should appear as normal variables in the dropdown.

Do not add:

1. A top-level anomaly mode toggle.
2. A separate anomaly page flow.
3. Special variable-selection branching for anomaly products.

This fits the current frontend flow in `frontend/src/App.tsx` and `frontend/src/lib/app-utils.ts` where variables are selected from capability-driven lists.

### 2. Labels And Grouping

Add anomaly labels and grouping overrides in `frontend/src/lib/app-utils.ts`.

Recommended labels:

- `tmp2m_anom`: `Surface Temp Anomaly`
- `tmp850_anom`: `850mb Temp Anomaly`
- `hgt500_anom`: `500mb Height Anomaly`

Recommended groups:

- Temperature anomalies -> `SURFACE` or `UPPER AIR` depending on field.
- `hgt500_anom` -> `UPPER AIR`

Recommended UX rule:

1. Keep anomaly fields near their raw analogs in the picker ordering.
2. Do not split them into a separate giant `ANOMALIES` group in v1.

### 3. Legends

Anomaly legends must be diverging, centered on zero, and visually distinct from raw-value palettes.

Required behavior:

1. Neutral center near zero.
2. Warm colors for positive temperature and height departures only if that reads clearly against the existing style.
3. Cool colors for negative departures.
4. Symmetric legend stops unless real-world validation shows a clear need for asymmetric clamping.

Temperature anomaly example direction:

- Negative: cool blues
- Near zero: neutral light gray or near-white
- Positive: warm oranges and reds

500 mb height anomaly example direction:

- Negative: cool purples or blues
- Near zero: neutral
- Positive: oranges or reds

The key rule is that anomaly palettes should not be confused with raw temperature palettes already used by `tmp2m`.

### 4. 500 mb Contour Behavior

`hgt500_anom` should include raw 500 mb height contours by default over the anomaly shading.

Rules:

1. Contour the raw `hgt500` field.
2. Do not contour the anomaly field itself in v1.
3. Keep the anomaly shading and raw-height contour pairing explicit in variable metadata and variable-guide copy.
4. Reuse the current contour-overlay pattern already used by upper-air fields where practical.

### 5. Variable Guide Page

Add anomaly product definitions to `frontend/src/pages/variables.tsx` after the backend capability contract is stable.

V1 copy should explain:

1. That the field is a departure from climatology.
2. That positive and negative values indicate above- or below-normal conditions.
3. That anomaly products are best used for pattern recognition, not as a replacement for raw-value fields.

## API And Capability Contract

V1 should keep the public contract simple.

### Capabilities

Anomaly variables appear in:

- `/api/v4/capabilities`
- `/api/v4/bootstrap`
- `/api/v4/models/{model}/capabilities`

They should look like standard variables with:

1. `var_key`
2. `display_name`
3. `units`
4. `order`
5. `group`
6. `color_map_id`
7. `ensemble.supported_views` and `ensemble.default_view` where applicable

Public capabilities do not need to expose internal runtime artifact IDs.

### Manifest Behavior

Published run manifests should keep the canonical public variable key.

Example:

1. Manifest variable key is `tmp2m_anom`.
2. Runtime build artifact may still resolve to `tmp2m_anom__mean` for GEFS and EPS.

This mirrors the current ensemble pattern and avoids frontend contract drift.

## Data Flow

### Build-Time Flow

```text
1. Resolve requested public anomaly variable.
2. Resolve runtime forecast input variable for the model and ensemble view.
3. Resolve climatology asset by model, field, valid time, and climatology version.
4. Load forecast field and climatology field.
5. Align units and grids.
6. Compute anomaly departure.
7. Write normal output artifacts and sidecars.
8. Publish through the existing manifest and grid pipeline.
```

### Runtime Viewer Flow

```text
1. Bootstrap/capabilities expose anomaly products as standard variables.
2. User selects an anomaly variable from the existing picker.
3. Viewer loads the anomaly field through the same frame and grid paths as any other variable.
4. Legend and labels reflect anomaly semantics.
```

## Execution Phases

### Phase 0 - Contract And Asset Decisions

Done criteria:

1. Variable keys are fixed.
2. Units are fixed.
3. Valid-time climatology alignment is fixed.
4. Climatology versioning scheme is fixed.
5. GEFS and EPS v1 scope is confirmed.

Deliverables:

1. This plan document.
2. A short follow-up asset-generation note if needed.

### Phase 1 - Shared Backend Infrastructure

Work:

1. Add climatology resolver service.
2. Add anomaly derive strategy infrastructure.
3. Add shared helpers for baseline path resolution and metadata validation.
4. Decide and implement sidecar metadata keys for anomaly provenance.

Done criteria:

1. A forecast field and matching climatology field can be differenced reliably in isolation.
2. Missing-climatology failures are explicit and actionable.
3. Unit alignment is deterministic.

### Phase 2 - GEFS V1 Rollout

Work:

1. Add GEFS `tmp2m_anom` first.
2. Add runtime artifact mappings.
3. Add color maps and legends.
4. Add grid packing.
5. Add tests for capabilities, manifests, and derive logic.
6. Add `hgt500_anom` second with raw height contours by default.
7. Add `tmp850_anom` third.

Done criteria:

1. GEFS anomaly products appear in capabilities.
2. GEFS anomaly artifacts publish successfully.
3. The viewer can display them without special-case UI code.

### Phase 3 - EPS Parity

Work:

1. Add EPS anomaly registry entries.
2. Add EPS runtime artifact mappings.
3. Validate climatology alignment against EPS valid times.
4. Reuse the same palettes and UI semantics where possible.

Done criteria:

1. EPS anomaly products use the same public contract as GEFS.
2. EPS builds and publishes with the same anomaly pipeline.

### Phase 4 - UX Hardening

Work:

1. Refine picker ordering and labels.
2. Add variable guide copy.
3. Validate mobile legend readability.
4. Review whether anchor labels or contour defaults should differ on anomaly fields.

Done criteria:

1. Anomaly products feel like native viewer products, not experimental add-ons.
2. Legends are legible and not confused with raw-value products.

### Phase 5 - Post-V1 Expansion

Candidate next steps:

1. Deterministic GFS and ECMWF anomaly support.
2. Precipitation anomaly design.
3. Additional anomaly types such as percent-of-normal or sigma departure.

## Testing Plan

### Unit And Service Tests

Add tests for:

1. Climatology asset resolution by valid time.
2. Missing asset failure behavior.
3. Unit alignment and anomaly subtraction.
4. Ensemble runtime artifact mapping for anomaly products.
5. Sidecar anomaly metadata.
6. Pre-aligned climatology asset grid validation.
7. Baked-in seasonal smoothing assumptions at the asset level, not runtime.

Recommended new test areas:

- `backend/tests/test_gefs_anomaly_contract.py`
- `backend/tests/test_eps_anomaly_contract.py`
- `backend/tests/test_anomaly_climatology_resolver.py`
- `backend/tests/test_anomaly_derive.py`

### Invariant Tests

Extend model invariant tests so they catch:

1. Expected anomaly variable presence.
2. Correct buildable versus internal-only runtime variable split.
3. Ensemble artifact mapping drift.
4. Color map and unit regressions.
5. `hgt500_anom` contour metadata continues to reference raw height contours, not anomaly contours.

### API Contract Tests

Add coverage for:

1. `/api/v4/capabilities`
2. `/api/v4/bootstrap`
3. Manifest variable key stability

Key invariant:

The public capability and manifest key should remain canonical even when the runtime artifact key is suffixed with `__mean`.

### Frontend Verification

Validate:

1. Variable appears in the picker.
2. Legend renders with a zero-centered diverging scale.
3. Sharing and permalinks preserve anomaly variable selection.
4. Variable guide page definitions render correctly.
5. `hgt500_anom` shows raw 500 mb contours over anomaly shading by default.

### Frozen Sample Cases

Add one or more frozen anomaly sample cases for known cycles and forecast hours.

Each frozen case should assert:

1. Anomaly min and max stay within an expected range.
2. A spatial pattern checksum or similarly stable summary stays within an agreed tolerance.
3. Zero-line or sign-structure behavior looks as expected for the known cycle.

Recommended direction:

1. Use at least one GEFS `tmp2m_anom` case.
2. Use at least one GEFS `hgt500_anom` case.
3. Add EPS frozen cases after GEFS behavior is stable.

The purpose of these frozen cases is not perfect bit-for-bit permanence across all future climatology versions. The purpose is to catch unintentional drift in sign structure, magnitude range, and broad spatial pattern when the code changes.

## Risks

### 1. Climatology Asset Mismatch

If forecast and climatology assets do not share sufficiently aligned grid or reprojection assumptions, anomaly fields may be spatially misleading.

Mitigation:

1. Validate CRS, transform, and shape explicitly.
2. Keep v1 on a narrow set of well-understood ensemble products.

### 2. Unit Confusion

Temperature anomaly units can become confusing if subtraction is done in one unit space and displayed in another without explicit metadata.

Mitigation:

1. Define internal subtraction units clearly.
2. Emit final units clearly in sidecars and legends.

### 3. Weak First-Use UX

If anomaly fields are added with raw-sounding names or poor legends, users may not understand what they are looking at.

Mitigation:

1. Use explicit `Anomaly` naming.
2. Add variable-guide copy in the same rollout.
3. Use clearly diverging palettes.

### 4. Precipitation Anomaly Scope Creep

Once anomaly infrastructure exists, there will be pressure to add precipitation anomalies immediately even though the baseline semantics are materially harder.

Mitigation:

1. Keep precip anomalies out of v1.
2. Require a separate contract document for precip anomalies.

### 5. EPS Field Availability Drift

EPS support may expose field-availability or aggregation nuances not present in GEFS.

Mitigation:

1. Land the shared pipeline first.
2. Treat EPS as a second rollout phase, not as the first proving ground.

## Open Questions

These do not block writing the initial code structure, but they should be resolved before broad expansion.

1. Should `tmp850_anom` ship in the same user-facing batch as `tmp2m_anom`, or should v1 public launch start with only one temperature anomaly field?
2. `hgt500_anom` should include raw 500 mb height contours by default over anomaly shading. The anomaly field itself is not contoured in v1.
3. Should anomaly sidecars expose climatology provenance publicly, or should some of that remain internal until the contract stabilizes?
4. Should deterministic GFS be the first post-v1 expansion, or should precipitation anomalies take priority after GEFS and EPS are validated?

## Recommended First Build Slice

If the work is implemented incrementally, the safest first slice is:

1. Add shared climatology resolver infrastructure.
2. Add generic departure derive support.
3. Lock ERA5 as the v1 climatology source archive and keep raw-source prep separate from the main builder.
4. Build an off-prod pilot for staged ERA5 `tmp2m` rasters at `00/06/12/18Z` only.
5. Roll out `gefs/tmp2m_anom` first.
6. Add `gefs/hgt500_anom` second, with raw 500 mb contours enabled by default.
7. Add `gefs/tmp850_anom` third.
8. Mirror the same order on EPS.

This gives the project a real anomaly pipeline without overcommitting to harder fields too early.