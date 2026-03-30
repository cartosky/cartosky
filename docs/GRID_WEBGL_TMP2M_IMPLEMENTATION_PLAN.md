# HRRR `tmp2m` Grid WebGL Shadow Pipeline Implementation Plan

## Purpose

Define a production-deployable shadow pipeline for `hrrr/tmp2m` that:

- runs alongside the current CartoSky WebP loop + tile pipeline
- uses a packed-grid substrate plus client-side `MapLibre + WebGL/custom layer` rendering
- is realistic enough to predict full-migration performance and quality
- is simple to expand to additional variables and models if the experiment succeeds
- is simple to disable or roll back if it does not

This document is intended to be implementation-ready. It includes:

- target architecture
- artifact contract
- API contract
- feature flags
- frontend/backend integration points
- rollout phases
- acceptance criteria
- cutover and rollback rules

## Product Goals

The prototype should answer the following questions with production-like fidelity:

1. Does packed-grid + WebGL rendering produce visibly better low/mid-zoom `tmp2m` maps than WebP loops?
2. Does it animate more smoothly than the current loop path?
3. Does it reduce weather-layer decode and asset-swap cost on the client?
4. Is the architecture clean enough to generalize across more variables and models?

## Constraints

- `MapLibre + WebGL/custom layer` is the primary target.
- The new path must be prod-deployable in shadow mode.
- The current pipeline must remain fully usable during the test.
- The prototype must lay reusable groundwork for broader migration.
- Rollback must be fast and low-risk.

## Non-Goals

- Replacing the current tile server or WebP system during the initial prototype
- Migrating all variables or models in the first implementation
- Replacing the authoritative sampling path
- Reprojecting arbitrary scientific grids in-browser for phase 1
- Supporting every product type in the first iteration

## Current State

CartoSky currently publishes per-frame:

- RGBA COGs for weather imagery
- value COGs for numeric sampling
- JSON sidecars for frame metadata

Relevant code and contracts:

- [README.md](../README.md)
- [docs/ARTIFACT_CONTRACT.md](./ARTIFACT_CONTRACT.md)
- [backend/app/main.py](../backend/app/main.py)
- [backend/app/services/tile_server.py](../backend/app/services/tile_server.py)
- [frontend/src/App.tsx](../frontend/src/App.tsx)
- [frontend/src/components/map-canvas.tsx](../frontend/src/components/map-canvas.tsx)

Today, the frontend effectively chooses between:

- `webp_tier0`
- `tiles`

The new design introduces a higher-level routing layer:

- `legacy`
- `grid_webgl_v1`

The legacy route keeps the current `webp_tier0 | tiles` logic intact.

## Target Architecture

### High-Level Design

The new substrate for `hrrr/tmp2m` is:

`offline packed display grid -> static manifest + binary frame delivery -> MapLibre custom WebGL renderer`

The existing substrate remains:

`published RGBA/value COGs -> loop WebP + tile rendering`

Both substrates must coexist for the same run/variable.

### Why This Design

This design isolates the experiment to the weather-layer render substrate without disturbing:

- existing data ingest
- current viewer behavior
- existing tile fallback
- current sampling path
- current publish integrity rules

It also keeps the expansion path clean by introducing a generic substrate contract rather than a `tmp2m`-only hack.

## Phase 1 Scope

Initial production shadow scope:

- model: `hrrr`
- variable: `tmp2m`
- region: canonical region only (`conus`)
- frontend render domain: low and mid zoom
- high zoom behavior: retain current tile fallback

This scope is intentionally narrow enough to ship safely and broad enough to measure the architecture honestly.

## Core Principles

### Principle 1: Shadow, Not Replace

The prototype must build and serve new artifacts without interfering with:

- current publish success
- current WebP loop generation
- current tile availability
- current client behavior for users not opted into the experiment

### Principle 2: No Viewer-Path Generation

The experimental path must not generate artifacts on demand.

If `grid_webgl_v1` artifacts are missing or invalid:

- the client falls back to legacy immediately
- the server reports the issue clearly
- the current user session still works

### Principle 3: One Generic Substrate Contract

Even though phase 1 only supports `hrrr/tmp2m`, the contract must be reusable for:

- additional continuous scalar variables
- additional models
- later multi-resolution variants

### Principle 4: Authoritative Sampling Stays Separate

Numeric tooltips and readouts must continue to use `val.cog.tif` or the future authoritative numerical source, not WebGL pixels.

This preserves correctness during the experiment and reduces migration risk.

## Artifact Contract

### Directory Layout

New artifacts should live beside the existing published frame set, not replace it.

Recommended path:

```text
$CARTOSKY_DATA_ROOT/published/hrrr/conus/{run_id}/tmp2m/grid_v1/
  manifest.json
  fh000.l0.u16.bin
  fh001.l0.u16.bin
  ...
```

Future-compatible layout:

```text
$CARTOSKY_DATA_ROOT/published/{model}/{region}/{run_id}/{var}/grid_v1/
  manifest.json
  fh{NNN}.l0.u16.bin
  fh{NNN}.l1.u16.bin
```

Where:

- `l0` = base level of detail used in phase 1
- `l1+` = optional future higher-detail grids or multiresolution levels

### Canonical Backing Source

For phase 1, `grid_v1` should be derived from the same transformed `tmp2m` field used to generate:

- `fhNNN.rgba.cog.tif`
- `fhNNN.val.cog.tif`

This guarantees:

- same geographic extent
- same projection assumptions
- same frame count
- same run/version alignment

This is a hard requirement, not a preference.

The prototype must not introduce a second independent reprojection path for `tmp2m`.

Instead, `grid_v1` packing must reuse the same post-transform, projection-aligned field or the same exact reprojection rules already used by the current published weather artifacts. The goal is to evaluate the new runtime substrate, not to accidentally compare two different spatial transforms.

### Reprojection And Spatial Fidelity Rules

Although arbitrary in-browser reprojection is a non-goal for phase 1, spatial fidelity still needs to be specified explicitly because the experimental grid artifacts must line up with the current map.

Phase 1 rules:

1. The packed grid must represent the same projected map space as the existing published `tmp2m` outputs.
2. The projection for the packed grid remains `EPSG:3857`.
3. The packed grid extent and alignment must match the current published tmp2m extent for the same run/region.
4. The interpolation/resampling policy used when deriving the packed grid must match the current visual intent for `tmp2m` rather than inventing a new variable-specific path.
5. Edge handling and nodata behavior must be parity-tested against the current tmp2m outputs.

Required validation:

- parity test against current bbox alignment
- parity test against current projected grid dimensions
- visual alignment comparison against current tmp2m imagery at representative zooms
- no upside-down, transposed, shifted, or edge-fringing regressions

### Data Type Choice

For `tmp2m`, use `uint16`.

Rationale:

- better fidelity than `uint8`
- still compact enough for browser delivery
- generalizes better to other scalar fields

Recommended packing:

- display units: Fahrenheit
- stored value: `encoded = round((value_f - offset) / scale)`
- initial `scale`: `0.1`
- initial `offset`: `-100.0`
- `nodata`: `65535`

This yields:

- precision of 0.1 F
- wide safe range for future fields
- trivial shader decode

### Compression

Phase 1 should prefer simplicity and reliability over aggressive codec complexity.

Recommended approach:

- binary payload served as raw packed bytes
- HTTP compression at the CDN/origin layer
- revisit custom compression only after telemetry justifies it

Do not introduce a novel binary compression scheme in phase 1.

### Frame Size And Delivery Budget

Per-frame packed grids for `tmp2m` will be materially larger than tiny loop thumbnails, so the prototype must be evaluated using realistic playback-window economics rather than total-run size in the abstract.

The important question is not "can we fetch the entire run at once?".

The important questions are:

- how many frames can be kept warm within the defined memory budget
- how quickly the next playable window can be fetched and uploaded
- whether scrub and autoplay remain stable under bounded prefetch

Phase 1 policy:

- do not fetch all frames eagerly
- preload only a bounded short-ahead window
- rely on LRU eviction for older inactive frames
- treat autoplay stability and scrub latency as the primary delivery health measures

Success should be judged on sustained interactive behavior, not on whole-run eager transfer.

### Binary Frame File Layout

Each `fhNNN.l0.u16.bin` file contains only packed grid values in row-major order.

No per-file header is required for phase 1 if the manifest fully specifies:

- width
- height
- dtype
- endianness
- scale
- offset
- nodata

Recommended binary rules:

- little-endian `uint16`
- row-major
- no header
- exact length must equal `width * height * 2`

### Manifest Schema

`manifest.json` should be the single source of truth for the grid substrate.

Recommended response shape:

```json
{
  "manifest_version": 1,
  "subtype": "grid_webgl_v1",
  "model": "hrrr",
  "run": "20260329_18z",
  "var": "tmp2m",
  "region": "conus",
  "projection": "EPSG:3857",
  "bbox": [-14916811.77, 2753408.11, -6679169.45, 7361866.11],
  "grid": {
    "width": 2745,
    "height": 1537,
    "dtype": "uint16",
    "endianness": "little",
    "scale": 0.1,
    "offset": -100.0,
    "nodata": 65535,
    "units": "F"
  },
  "palette": {
    "color_map_id": "tmp2m",
    "legend_url": "/api/v4/hrrr/20260329_18z/tmp2m/frames"
  },
  "lods": [
    {
      "level": 0,
      "label": "base",
      "width": 2745,
      "height": 1537,
      "frames": [
        { "fh": 0, "url": "/grid/v1/hrrr/20260329_18z/tmp2m/fh000.l0.u16.bin?v=..." },
        { "fh": 1, "url": "/grid/v1/hrrr/20260329_18z/tmp2m/fh001.l0.u16.bin?v=..." }
      ]
    }
  ],
  "source": {
    "authoritative_sampling": "val_cog",
    "run_version_token": "..."
  }
}
```

### Manifest Rules

Rules for `grid_v1` manifests:

1. `manifest_version` must be explicit and required.
2. `subtype` must be explicit so the frontend can branch cleanly.
3. `projection` and `bbox` must align with published tmp2m outputs.
4. URLs must be versioned using the run version token.
5. Missing or partial frame lists are allowed only if explicitly reflected in the manifest.
6. The manifest must be immutable for a given run version.

## API Contract

### New Endpoint

Add a new endpoint:

```text
GET /api/v4/{model}/{run}/{var}/grid-manifest
```

This is the grid-substrate analog to the current `loop-manifest` endpoint.

### Endpoint Behavior

If the run exists and `grid_v1` is available for the requested var:

- return `200` with the manifest

If the run exists but the var is unsupported for `grid_v1`:

- return `404` with structured JSON error

If the run exists, var is supported, but artifacts are missing or invalid:

- return `503` with structured JSON error for operational visibility

Recommended error shape:

```json
{
  "error": "grid_manifest_unavailable",
  "model": "hrrr",
  "run": "20260329_18z",
  "var": "tmp2m",
  "detail": "grid_v1 artifacts missing or incomplete"
}
```

### Frames Endpoint Compatibility

Do not replace the existing `/frames` contract in phase 1.

Instead, optionally extend rows in `/frames` for supported vars with additive metadata:

```json
{
  "fh": 0,
  "has_cog": true,
  "loop_webp_url": "...",
  "loop_webp_tier0_url": "...",
  "grid_webgl_v1_available": true,
  "meta": { "meta": { "...": "..." } }
}
```

This is optional in phase 1. The experimental frontend can also rely exclusively on `grid-manifest`.

### Capability Metadata

Add substrate metadata to model capability responses so the frontend knows which vars are eligible.

Recommended variable capability addition:

```json
{
  "var_key": "tmp2m",
  "render_substrates": ["legacy", "grid_webgl_v1"]
}
```

Recommended defaults addition:

```json
{
  "defaults": {
    "default_render_substrate": "legacy"
  }
}
```

This keeps the new path opt-in while laying the groundwork for future defaults.

## Feature Flags And Overrides

### Backend Flags

Add environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOSKY_GRID_V1_ENABLED` | `0` | Master backend enable for grid manifest + artifact serving |
| `CARTOSKY_GRID_V1_ALLOWLIST` | `hrrr:tmp2m` | Allowed model/var pairs |
| `CARTOSKY_GRID_V1_REGIONS` | `conus` | Allowed regions |
| `CARTOSKY_GRID_V1_BUILD_ENABLED` | `0` | Enable post-publish grid artifact generation |
| `CARTOSKY_GRID_V1_WORKERS` | `1` | Parallel grid artifact generation workers |
| `CARTOSKY_GRID_V1_KEEP_RUNS` | unset | Optional independent retention for grid artifacts |
| `CARTOSKY_GRID_V1_MAX_PENDING_RUNS` | `2` | Backpressure protection for shadow backlog |

### Frontend Flags

Add environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_CARTOSKY_GRID_V1_ENABLED` | `false` | Master frontend support flag |
| `VITE_CARTOSKY_GRID_V1_DEFAULT_ENABLED` | `false` | Optional future default |
| `VITE_CARTOSKY_GRID_V1_HIGH_ZOOM_FALLBACK` | `true` | Keep current tiles at high zoom |
| `VITE_CARTOSKY_GRID_V1_DEBUG_OVERLAY` | `false` | Debug HUD for substrate diagnostics |

### Runtime User Overrides

Add three runtime controls:

1. Query param:

```text
?weather_substrate=grid
?weather_substrate=legacy
```

2. User-visible override in the display/settings UI:

- `Automatic`
- `Classic`
- `Experimental Grid`

3. Persisted permalink state so test sessions can be shared and repeated.

The query param should override UI preference for the current session.

## Frontend Selection Rules

### Substrate Selection Layer

Add a new substrate state above the existing render mode logic.

Recommended substrate enum:

```ts
type WeatherSubstrate = "legacy" | "grid_webgl_v1";
```

Phase 1 selection behavior:

Use `grid_webgl_v1` only if all are true:

- frontend flag enabled
- backend capability or manifest support present
- model is `hrrr`
- variable is `tmp2m`
- region is `conus`
- query param or user override requests grid, or future default is enabled

Else use `legacy`.

### Interaction With Existing Render Modes

For `legacy`:

- retain current `webp_tier0 | tiles` logic unchanged

For `grid_webgl_v1`:

- low/mid zoom: custom WebGL layer
- high zoom: current tile path

Recommended phase 1 thresholds:

- use existing zoom threshold concepts to decide when the weather layer should fall back to tiles
- do not attempt full all-zoom replacement initially

### Zoom Handoff Validation

The low/mid-zoom WebGL to high-zoom tile handoff is a critical perception point and must be treated as an explicit prototype workstream, not a later polish item.

Risks at the handoff:

- visible sharpness discontinuity
- color drift between substrates
- positional jump
- distracting mode pop during zoom gestures

Phase 1 requirements:

1. Prototype the handoff early, before broad frontend rollout.
2. Use stable thresholds with hysteresis, not jittery zoom-boundary switching.
3. Validate the handoff visually at representative zooms before treating the substrate as production-testable.
4. If the handoff is visibly poor, the phase is not complete even if the renderer works technically.

Acceptance should include:

- no positional jump
- no obvious palette change
- acceptable sharpness transition
- no zoom-boundary oscillation during interaction

## Backend Build Plan

### New Builder Module

Add a new service module for grid artifact generation, for example:

- `backend/app/services/builder/grid_manifest.py`
- `backend/app/services/builder/grid_packer.py`

Responsibilities:

- resolve source field for supported var
- pack scalar values to `uint16`
- write per-frame binaries
- write `manifest.json`
- validate output lengths and metadata

### Publish Pipeline Integration

Integrate grid artifact generation as a post-publish shadow stage.

Recommended order for `hrrr/tmp2m`:

1. existing authoritative build completes
2. existing publish writes current artifacts
3. existing loop pre-generation proceeds as configured
4. new `grid_v1` stage runs afterward

This preserves current publish integrity and keeps the new work clearly non-blocking.

### Shadow Build Resource Policy

The prototype must be realistic enough to measure but not so aggressive that it destabilizes current publishing.

Recommended policy:

- dedicated worker count for grid shadow builds
- no contention with the main build worker pool where avoidable
- bounded backlog
- metrics for build lag and build failure rate

Recommended initial policy:

- `CARTOSKY_GRID_V1_WORKERS=1`
- max pending runs `2`
- skip oldest backlog if a larger queue forms

This is enough to make the test viable without distorting normal operations.

### Shadow Throughput As A Phase Gate

Because HRRR updates hourly, shadow build throughput must be treated as a rollout gate, not merely something to observe after the fact.

Questions the shadow pipeline must answer:

- can it stay caught up across consecutive hourly cycles
- can it produce artifacts quickly enough that testers usually encounter ready runs
- can it do so without materially harming the legacy publish path

Phase 1 throughput rule:

- start with conservative worker settings
- measure real build lag over multiple hourly cycles
- if lag consistently accumulates, tune worker count or queue policy before wider validation

The prototype should not be considered representative if shadow builds are routinely late enough that testers are mostly exercising fallback behavior instead of the intended substrate.

### Retention

Keep grid artifacts for the same runs retained by the current publish pipeline by default.

If independent retention is needed later:

- add a grid-specific retention setting
- never delete legacy artifacts as part of grid cleanup

## WebGL Renderer Plan

### Rendering Strategy

Use a `MapLibre` custom layer for the weather raster.

Phase 1 renderer responsibilities:

- fetch frame binaries using the `grid-manifest`
- parse `ArrayBuffer` to `Uint16Array`
- upload the active frame as a texture
- decode `uint16 -> physical value` in shader
- apply color ramp in shader
- draw aligned to the weather layer bbox

### Shader Design

Recommended shader responsibilities:

- texture sample from the packed scalar grid
- detect `nodata`
- apply `decoded = encoded * scale + offset`
- map decoded value through a LUT texture derived from the existing tmp2m colormap
- output RGBA

Phase 1 should not attempt:

- dynamic contour generation
- arbitrary projection correction in shader
- multi-layer weather compositing

### Color Ramp Reuse

Do not invent a parallel color system.

Instead:

- derive the LUT from the existing `tmp2m` colormap definition
- keep legend display sourced from the existing metadata and sidecar logic where possible

This keeps the visual comparison honest.

### Worker Use

Binary fetch and parse should use a worker where practical.

Recommended division:

- worker: fetch, parse, basic validation
- main thread: GL texture upload and draw

This improves the realism of the prototype on real devices.

## Cache And Memory Policy

### Why This Matters

A prototype that uses unrealistic memory budgets will not predict a full migration honestly.

### Initial Client Cache Rules

Use bounded caches for:

- manifest
- fetched frame buffers
- parsed typed arrays
- GL textures

Recommended phase 1 policy:

- keep a short-ahead playable window
- LRU-evict inactive frame assets
- separate desktop and mobile budgets

Suggested initial budgets:

- desktop: target `128-192 MiB` total substrate budget for frame buffers + textures
- mobile: target `64-96 MiB`

Exact values should be tuned after telemetry, but phase 1 should start conservative.

### Playback Policy

Use policies similar in spirit to the current loop buffering logic:

- minimum start buffer
- minimum ahead while playing
- bounded critical concurrency
- bounded idle warming

But for the grid substrate, readiness is:

- fetched
- parsed
- texture-upload-ready

## Observability And Telemetry

### Backend Metrics

Add metrics for:

- `grid_manifest_resolve`
- `grid_manifest_build`
- `grid_shadow_build_success`
- `grid_shadow_build_failure`
- `grid_shadow_build_lag_seconds`
- `grid_shadow_artifact_bytes`
- `grid_shadow_backlog_depth`

### Frontend Metrics

Add substrate-aware metrics for:

- `grid_manifest_resolve`
- `grid_frame_fetch`
- `grid_frame_parse`
- `grid_texture_upload`
- `grid_first_visible`
- `grid_frame_change`
- `grid_scrub_latency`
- `grid_loop_start`
- `grid_animation_stall`
- `grid_frame_drop_gap`
- `weather_substrate_switch`

Each metric should include:

- model
- variable
- run
- region
- substrate
- device class

### Debug HUD

Add an optional debug overlay behind a frontend flag showing:

- substrate
- manifest version
- active frame
- cached frame count
- bytes used
- texture upload timing
- fallback reason when legacy is selected

This will reduce QA friction substantially.

## Testing Plan

### Correctness Tests

Add backend tests for:

- manifest generation for `hrrr/tmp2m`
- correct frame count and ordering
- correct file size for each binary
- correct packing and unpacking parity
- versioned URL generation
- graceful errors for missing artifacts

Add frontend tests for:

- manifest selection and fallback routing
- query param override behavior
- user override persistence
- graceful fallback on failed manifest/frame fetch

### Visual Parity Tests

For `tmp2m`, compare:

- legacy low-zoom output
- grid WebGL low-zoom output

Validate:

- same spatial alignment
- same bbox coverage
- no obvious palette drift
- no upside-down or transposed frames
- no nodata leakage
- acceptable zoom handoff appearance between WebGL and tile fallback

### Sampling Parity Tests

Validate that:

- tooltip values still come from the authoritative sample path
- grid WebGL rendering does not change numeric readout semantics

### Performance QA

Reuse and extend:

- [docs/RENDERING_PERFORMANCE_QA_RUNBOOK.md](./RENDERING_PERFORMANCE_QA_RUNBOOK.md)

Add a dedicated grid test block for:

- cold manifest load
- first visible frame
- frame advance
- scrub
- autoplay stability
- substrate fallback under error
- zoom handoff quality and stability at the WebGL/tile boundary

### Shadow Throughput QA

Add a dedicated operational validation block for:

- multiple consecutive hourly HRRR cycles
- grid shadow build lag per run
- artifact readiness rate before tester interaction
- whether fallback usage is caused by policy choice or by missing experimental artifacts

The experiment is not representative if the substrate is technically sound but usually unavailable when new hourly runs appear.

## Rollout Plan

### Phase 0: Contract And Plumbing

Changes:

- add flags
- add capability metadata
- add substrate selection plumbing in frontend
- add no-op backend route placeholders if desired

Acceptance criteria:

- no behavior change for existing users
- flags off means zero user-visible impact
- build and tests remain green

### Phase 1: Shadow Artifact Generation

Changes:

- generate `grid_v1` binaries and manifest for `hrrr/tmp2m`
- serve versioned static URLs
- expose `grid-manifest`

Acceptance criteria:

- no publish regression for current pipeline
- grid artifacts appear for supported runs
- grid artifact failures do not break legacy publish
- metrics show shadow build health and lag
- multiple consecutive hourly HRRR cycles can be serviced without sustained shadow backlog growth

### Phase 2: Hidden Frontend Integration

Changes:

- implement substrate selection
- add query param and UI override
- add `grid-manifest` fetch
- implement WebGL custom layer

Acceptance criteria:

- default users remain on legacy
- `?weather_substrate=grid` works for supported selections
- unsupported selections fall back cleanly
- no app-breaking errors when artifacts are absent
- zoom-boundary handoff is visually acceptable in controlled QA

### Phase 3: Prod Shadow Validation

Changes:

- run with prod-deployable backend flags enabled
- use internal testers and stable query param/UI override
- collect telemetry over multiple HRRR cycles

Acceptance criteria:

- artifact readiness is high enough to support meaningful testing
- low/mid-zoom tmp2m grid path is stable
- visual quality is at least on par with legacy
- performance metrics are useful and non-zero

### Phase 4: Optional Default For `tmp2m`

Changes:

- allow `grid_webgl_v1` to become default substrate for `hrrr/tmp2m` under config
- keep legacy available as override

Acceptance criteria:

- substrate default can be changed with config only
- rollback to legacy default requires no code changes
- high zoom continues to fall back to tiles safely

## Cutover Plan

If the experiment is successful, the easiest cutover path is:

1. keep building both substrates for a transition period
2. flip frontend default substrate for `hrrr/tmp2m` to `grid_webgl_v1`
3. retain legacy override for support/debugging
4. expand the same substrate contract to the next variable

Do not remove legacy artifacts immediately after cutover.

Recommended cutover sequence:

- `tmp2m` default changes first
- `dp2m` and `tmp850` next
- precipitation and wind products later
- categorical products last

## Rollback Plan

Rollback must be possible at three levels.

### Level 1: Frontend Immediate Rollback

Action:

- disable `VITE_CARTOSKY_GRID_V1_DEFAULT_ENABLED`
- or force substrate default to `legacy`

Effect:

- users stop using the new path immediately
- backend shadow artifacts can remain in place

### Level 2: Backend Exposure Rollback

Action:

- disable `CARTOSKY_GRID_V1_ENABLED`

Effect:

- `grid-manifest` becomes unavailable
- opted-in clients fall back to legacy

### Level 3: Shadow Build Rollback

Action:

- disable `CARTOSKY_GRID_V1_BUILD_ENABLED`

Effect:

- no new grid artifacts are built
- existing ones may remain until retention clears them

At no point should rollback require:

- data migration
- destructive cleanup
- changes to current publish artifacts

## Generalization Path

This prototype should lay the groundwork for broader migration by making these abstractions generic now:

1. substrate capability metadata
2. generic grid manifest schema
3. generic grid frame fetch/cache interface
4. generic WebGL scalar renderer
5. generic color ramp LUT pipeline
6. generic fallback rules

If these are done correctly for `tmp2m`, then adding additional continuous variables should mostly require:

- packer config
- color map binding
- variable allowlist updates

## Variable Expansion Order

Recommended order after `tmp2m`:

1. `dp2m`
2. `tmp850`
3. `precip_total`
4. `snowfall_total`
5. `wspd10m`
6. `wgst10m`
7. `radar_ptype` only after categorical rendering rules are separately designed

## Open Implementation Decisions

These should be resolved during implementation, but they do not block phase 0 planning:

- whether phase 1 binary files should include a lightweight header or remain headerless
- whether the shader should use a 1D LUT texture or a small uniform-driven ramp for tmp2m
- whether the grid path should eventually consume the full-resolution or a specifically tuned display grid for all variables
- exact low/mid/high zoom handoff thresholds for the grid substrate

These decisions should be settled before broad phase 2 frontend work begins to avoid unnecessary renderer rework.

## Recommended File Touchpoints

Backend:

- [backend/app/main.py](../backend/app/main.py)
- [backend/app/services/builder/pipeline.py](../backend/app/services/builder/pipeline.py)
- [backend/app/services/publish_utils.py](../backend/app/services/publish_utils.py)
- [backend/app/models/hrrr.py](../backend/app/models/hrrr.py)

Likely new backend files:

- `backend/app/services/builder/grid_packer.py`
- `backend/app/services/builder/grid_manifest.py`

Frontend:

- [frontend/src/App.tsx](../frontend/src/App.tsx)
- [frontend/src/components/map-canvas.tsx](../frontend/src/components/map-canvas.tsx)
- [frontend/src/lib/api.ts](../frontend/src/lib/api.ts)
- [frontend/src/lib/config.ts](../frontend/src/lib/config.ts)
- [frontend/src/lib/permalink.ts](../frontend/src/lib/permalink.ts)

## Final Recommendation

Implement `hrrr/tmp2m` as a `grid_webgl_v1` shadow substrate now, not as a one-off experiment and not as a full migration.

The right shape is:

- additive artifact family
- additive manifest endpoint
- additive frontend substrate router
- additive WebGL renderer
- existing sampling preserved
- existing legacy path preserved

If the prototype succeeds, switching over should be mostly:

- a default substrate change
- additional variable allowlists
- incremental renderer adoption

If it fails, rollback should be mostly:

- flip off the frontend default
- disable backend manifest exposure if needed
- stop shadow builds later

That is the lowest-risk way to get a realistic answer about whether the new pipeline should become CartoSky's long-term weather rendering substrate.
