# MRMS Radar Implementation Plan

## Summary
This plan adds MRMS observed radar to CartoSky by making it a first-class source inside the existing capability, manifest, frames, and loop-manifest architecture.

The repo-specific decision is:
1. MRMS will not get a separate viewer or rendering path.
2. MRMS will publish immutable rolling bundles that conform to the current `model/run/variable/frame` contract.
3. The frontend will become source-aware about time semantics so observed sources stop leaking forecast-hour language.
4. V1 will be tightly scoped to one MRMS product: `MRMS Merged Base Reflectivity QC`, ingested as raw scientific values, not pre-rendered imagery.

This is the cleanest fit for the current codebase because:
1. the backend already serializes model and variable capabilities through `backend/app/main.py`
2. the published artifact flow is already manifest-backed
3. the frontend already plays ordered frame sequences from `frames` and `loop-manifest`
4. sidecars already support per-frame `valid_time`

## Execution Checklist
This is the short path to execution for V1.

### Before Coding Phase 2
1. Confirm the exact NOAA/NCEP MRMS GRIB2 URL pattern and file naming for `MRMS Merged Base Reflectivity QC`.
2. Confirm deployment support for `wgrib2` in all target environments.
3. Decide the initial rolling-window target if different from the default 120 minutes.
4. Lock MRMS freshness and availability states:
   - `Live`: newest scan within normal freshness window
   - `Delayed`: newest scan older than expected but under stale threshold
   - `Stale`: newest scan older than 15 minutes
   - `Unavailable`: no publishable MRMS bundle

### Required Prototype Gate
1. Fetch one representative rolling MRMS input set.
2. Decode it with `wgrib2`.
3. Validate `pygrib` fallback on the same input set.
4. Build one complete bundle:
   - `val.cog`
   - `rgba.cog`
   - sidecars
   - loop assets
5. Record:
   - total build time
   - decode time
   - loop generation time
   - CPU and memory peaks
6. Decide whether full immutable rebuilds fit within cadence.

### V1 Implementation Order
1. Add observed-source capability metadata.
2. Register `mrms` and `reflectivity`.
3. Build MRMS fetch and publish flow.
4. Validate frames, loop-manifest, and sampling against MRMS.
5. Add observed-time UI behavior.
6. Add stale and unavailable MRMS viewer states.
7. Add telemetry and admin freshness visibility.

### V1 Done Checklist
1. `mrms` is selectable in the viewer.
2. MRMS uses `MRMS Merged Base Reflectivity QC` raw values.
3. `wgrib2` is documented and working in deployment.
4. The latest-only rolling bundle publishes immutably.
5. Sampling works for MRMS.
6. MRMS opens on the newest frame.
7. MRMS uses observed timestamps instead of `FH`.
8. Stale and unavailable MRMS states are visible to users.
9. Forecast products still behave exactly as before.

## V1 Decisions Locked
These are hard decisions for the first MRMS release.

1. Product:
   - `MRMS Merged Base Reflectivity QC`
2. Data form:
   - raw scientific reflectivity values upstream
   - CartoSky performs its own colorization
3. Variable scope:
   - V1 ships `reflectivity` only
   - the pipeline must remain compatible with future MRMS precipitation-type products
4. Sampling:
   - `yes`
   - V1 must publish value rasters suitable for hover and point sampling
5. Timeline:
   - latest-only rolling window
   - no run-history UI work in v1
6. Frame identity:
   - internal `fh` remains the ordered frame index for compatibility
7. UX target:
   - smooth recent reflectivity loop in the existing viewer
   - no radar-specific viewer branch

## Assumptions Used In This Plan
These are reasonable defaults to keep the plan executable. They can be made configurable during implementation.

1. Rolling window target:
   - 120 minutes by default
2. Default frame on open:
   - newest available frame
3. Frame order:
   - oldest to newest
4. Region target:
   - `conus` first
5. Source transport:
   - NOAA/NCEP HTTP MRMS GRIB2
6. Poll cadence:
   - MRMS publish orchestration will run on a much tighter cadence than forecast schedulers
   - the exact cadence should be based on observed upstream availability and measured build cost

## Non-Negotiable Invariants
These are implementation invariants, not suggestions.

1. MRMS bundles are immutable after publish.
2. `latest` is a pointer to a bundle, not a mutable artifact tree.
3. Frame order is always oldest to newest within a bundle.
4. Observed sources must never derive display time from `run + fh`.
5. The frontend playback engine remains generic; there is no radar-specific rendering path.
6. MRMS ingest and publish logic must not be forced into forecast-cycle abstractions.
7. V1 includes sampling support because raw values are being ingested.

## Why This Fits The Repo
The current architecture already has the right seams.

1. Capability metadata:
   - `backend/app/models/base.py`
   - `backend/app/models/registry.py`
   - `backend/app/main.py`
2. Manifest-backed runtime APIs:
   - `backend/app/main.py`
3. Existing published artifact conventions:
   - sidecars with `valid_time`
   - loop URL resolution
   - value-raster sampling
4. Frontend frame playback:
   - `frontend/src/App.tsx`
   - `frontend/src/components/map-canvas.tsx`
   - `frontend/src/components/bottom-forecast-controls.tsx`
5. Existing legend and palette flow:
   - `backend/app/services/colormaps.py`
   - `backend/app/services/builder/colorize.py`
   - `frontend/src/components/map-legend.tsx`

The real mismatch is time semantics, not rendering:
1. the viewer still treats the scrubber as a forecast-hour control
2. screenshot labeling still stamps `FH`
3. sampling and telemetry names still use forecast-hour language even when the UI would be showing observed data

That means the system should now be thought of as:
1. a time-sequenced raster viewer
2. that supports forecast sources and observed sources
3. with different time semantics but the same rendering contract

## Target V1 Product Shape

### Model and Variable
1. `model_id = "mrms"`
2. `var_key = "reflectivity"`
3. display name:
   - `Merged Base Reflectivity QC`
4. units:
   - `dBZ`
5. kind:
   - `discrete`

### Run Semantics
1. Each published MRMS bundle gets a run-like ID based on publish time.
2. Example:
   - `20260327_1730z`
3. That run ID represents the published rolling bundle, not a forecast cycle.

### Frame Semantics
1. `fh` is retained as the ordered frame index.
2. `fh = 0` is the oldest frame in the bundle.
3. `fh = N` is the newest frame in the bundle.
4. Every frame must include `valid_time`.

### User-Facing Timeline Semantics
1. The viewer defaults to the newest frame.
2. The timeline is labeled by observed timestamp, not `FH`.
3. Forecast sources keep current `run + FH` behavior.
4. MRMS does not need run-history browsing in v1.

## Explicit Scope

### In Scope For V1
1. `mrms` capability registration
2. reflectivity-only MRMS source
3. raw-value ingest
4. colorization in CartoSky
5. RGBA COG generation
6. value COG generation for sampling
7. sidecars with `valid_time`
8. frames API support
9. loop-manifest support
10. latest-only rolling window
11. source-aware observed-time UI
12. screenshot labeling for observed sources
13. freshness and completeness metadata

### Out Of Scope For V1
1. MRMS precipitation type UI
2. MRMS accumulated precipitation products
3. MRMS run history UI
4. forecast vs observed comparison mode
5. public API renaming away from `forecast_hour`
6. radar-specific chrome or special viewer mode

## Future Compatibility Requirement
V1 is reflectivity-only, but the pipeline must not block future MRMS precipitation-type products.

That means:
1. `mrms.py` should be structured as a true variable catalog, not a one-off reflectivity hack.
2. ingest and publish code should allow multiple MRMS variables later.
3. palette and legend logic should remain model-plus-variable driven.
4. frontend time semantics should be model or source driven, not variable specific.

## Upstream Product Lock
The V1 upstream acquisition path is now locked.

Locked V1 choices:
1. upstream product:
   - `MRMS Merged Base Reflectivity QC`
2. transport:
   - NOAA/NCEP HTTP MRMS GRIB2
3. preferred decoder:
   - `wgrib2`
4. fallback decoder:
   - `pygrib`
5. downstream processing:
   - reuse CartoSky's existing raster colorization, `val.cog`, and loop pipeline
6. publish model:
   - immutable rolling bundles
   - latest-only for v1

Still-required implementation details to pin down:
1. exact upstream URL pattern and file naming convention
2. native cadence
3. native spatial resolution
4. expected latency from observation time to availability
5. retention expectation upstream
6. nodata and missing-value handling rules
7. deployment and packaging expectations for `wgrib2`

## Target Backend Architecture

### 1) Add MRMS As A Model Capability
Create a model plugin in the same style as existing models.

Primary files:
1. `backend/app/models/base.py`
2. `backend/app/models/registry.py`
3. new file: `backend/app/models/mrms.py`

Recommended capability shape for v1:
1. `model_id = "mrms"`
2. `name = "MRMS Radar"`
3. `product = "obs"`
4. `canonical_region = "conus"`
5. `ui_defaults`:
   - `default_var_key = "reflectivity"`
   - `default_run = "latest"`
   - `default_frame_selection = "latest"`
6. `ui_constraints`:
   - `canonical_region = "conus"`
   - `time_axis_mode = "observed"`
   - `latest_only = true`
   - `supports_sampling = true`
7. `variable_catalog.reflectivity`:
   - name, units, kind, color map ID, display order

Repo-specific note:
1. `VariableCapability.frontend` exists in `backend/app/models/base.py`, but `backend/app/main.py` does not currently serialize it.
2. For V1, use model-level defaults and constraints because they already flow through capability serialization.

### 2) Add A Dedicated MRMS Ingest And Publish Flow
Do not force MRMS into the forecast scheduler model.

Recommended new files:
1. `backend/app/services/mrms_fetch.py`
2. `backend/app/services/mrms_publish.py`
3. optional entrypoint or scheduler integration in `backend/app/services/scheduler.py`
4. optionally a dedicated runner such as `backend/app/services/mrms_poller.py`

Responsibility split:
1. `mrms_fetch.py`
   - discover latest upstream scans
   - fetch NOAA/NCEP MRMS GRIB2 files over HTTP
   - normalize timestamps and grid metadata
2. `mrms_publish.py`
   - assemble a rolling window
   - build artifacts
   - write manifest
   - publish atomically
3. `mrms_poller.py` or equivalent dedicated loop
   - poll or trigger on MRMS cadence
   - maintain a separate control loop from forecast model scheduling
   - enqueue or invoke MRMS publish attempts without coupling to forecast run discovery

Poller rule:
1. MRMS should use a separate orchestration loop from forecast-cycle scheduling.
2. Reusing `scheduler.py` is acceptable only if MRMS cadence and locking are clearly isolated from forecast model logic.
3. If isolation becomes awkward, prefer a dedicated MRMS runner instead of forcing reuse.

### 3) Keep The Existing Artifact Family
MRMS should publish artifacts that the current backend and frontend already understand.

Per frame, V1 should publish:
1. `fhNNN.rgba.cog.tif`
2. `fhNNN.val.cog.tif`
3. `fhNNN.json`

Why:
1. `rgba.cog` gives the current tile pipeline what it expects
2. `val.cog` enables sampling for v1
3. sidecars carry `valid_time`, legend metadata, and freshness metadata

Decoder rule:
1. `wgrib2` is the primary supported production decoder for MRMS GRIB2.
2. `pygrib` is the fallback path for environments where `wgrib2` is unavailable or for local experimentation.
3. The ingest layer should normalize decoder output into one internal frame payload shape before the rest of the pipeline sees it.

### 4) Build Loop Assets The Same Way As Other Sources
The frontend already supports loop manifests and loop WebP tiers.

MRMS should therefore:
1. pre-generate loop assets for the bundle
2. publish a complete `loop-manifest`
3. allow the frontend to reuse the existing decode, preload, and playback logic

## Concrete V1 Publish Pipeline
This is the backend sequence that needs to be implemented.

1. Discover upstream scans for the target rolling window.
2. Filter out scans that are too stale, unreadable, or outside the target window.
3. Normalize valid times and sort oldest to newest.
4. Assign stable frame indices for the bundle:
   - oldest scan -> `fh000`
   - newest scan -> highest `fh`
5. For each frame:
   - decode raw reflectivity values from MRMS GRIB2 using `wgrib2` as the preferred path or `pygrib` as fallback
   - normalize nodata handling
   - write `val.cog`
   - colorize values using a CartoSky reflectivity palette
   - write `rgba.cog`
   - write sidecar JSON with `valid_time` and source metadata
6. After all required frame artifacts exist:
   - generate loop assets
   - write manifest JSON
   - stage the bundle in a temporary publish directory
   - atomically move the bundle into place
   - atomically update the `latest` pointer

## Early Validation Gate
Before full Phase 2 implementation is committed, run a focused prototype for one complete MRMS bundle build.

The prototype must answer:
1. how long it takes to fetch and normalize a full rolling input set
2. how long `wgrib2` decode takes on a representative bundle
3. whether `pygrib` fallback is operationally acceptable if used
4. how long it takes to build all `val.cog` and `rgba.cog` artifacts
5. how long loop asset generation takes
6. peak CPU and memory use during the build
7. whether the full build comfortably fits inside the intended MRMS update cadence

Decision rule:
1. if a full immutable rebuild fits comfortably within cadence, keep the simpler full-bundle publish model
2. if it does not, then evaluate artifact reuse or incremental build strategies after measurement, not before
3. if `pygrib` is materially slower or less reliable, treat it as non-production fallback only

## Explicit Artifact Questions Answered

### Are Tiles Generated From A Single Raster Per Frame?
Yes.

For each frame, V1 should derive runtime tiles from:
1. one RGBA COG used for display
2. one value COG used for sampling

### Are Loop WebPs Generated From Full-Frame Composites Or Tiles?
Full-frame composites.

Reason:
1. this matches the current loop-manifest and loop-prewarm model better
2. it avoids inventing a tile-based animation path for MRMS

### Are We Storing Scientific Rasters, Display Rasters, Or Both?
Both.

V1 requires:
1. scientific values for sampling
2. display rasters for fast display and color consistency

### What Happens If A New Upstream Scan Arrives Mid-Build?
It is ignored for the current bundle.

Rule:
1. each bundle is built from a frozen input scan list
2. newer scans discovered mid-build are candidates for the next bundle only

This preserves immutability and prevents "latest" from changing shape during publish.

## Failure Modes And Bundle Rules
These states must be handled in the backend, not discovered first in the frontend.

### Required Failure Handling
1. Missing expected upstream scans
2. Corrupted upstream scan
3. Bundle older than freshness threshold
4. Fewer frames than target rolling window
5. Publish failure after staging starts
6. Latest publish fails while an older published bundle still exists

### Required Publish Rules
1. A publish operates on a frozen scan list.
2. A bundle may be published incomplete if it satisfies minimum viability rules.
3. Minimum viability rules must be explicit, for example:
   - at least one valid frame exists
   - newest frame is within the freshness threshold
   - sidecars and loop assets were generated successfully
4. If a new bundle fails validation, the previous published `latest` bundle remains active.
5. Partial staging output must never be exposed as the live bundle.

### Frontend State Rules For MRMS
The frontend must have explicit user-facing behavior for stale or unavailable MRMS data.

Required V1 behavior:
1. If MRMS has a publishable latest bundle and the newest scan is within the normal freshness window, show `Live`.
2. If the newest scan is older than expected but younger than the stale threshold, show `Delayed`.
3. If the newest scan is older than 15 minutes but the bundle is still usable, show `Stale`.
4. If MRMS has no publishable bundle, show `Unavailable`.
5. If MRMS has a valid latest bundle, the source remains selectable.
6. If MRMS has no usable latest bundle, the source should not fail mysteriously after selection.

Minimum UX requirement:
1. do not hide MRMS problems behind generic loading behavior
2. show freshness or degraded-state messaging in the viewer for MRMS when appropriate
3. keep the exact chrome lightweight for v1; the important part is explicit state, not custom styling

### Freshness And Completeness Metadata
Each MRMS bundle should expose enough metadata to support debugging, admin views, and future UI improvements.

Recommended bundle metadata:
1. `latest_scan_valid_time`
2. `bundle_published_at`
3. `bundle_age_seconds`
4. `stale`
5. `target_frame_count`
6. `available_frame_count`
7. `source = "mrms"`
8. `time_axis_mode = "observed"`
9. `usable`
10. `degraded_reason` when applicable
11. `freshness_state` with one of:
   - `live`
   - `delayed`
   - `stale`
   - `unavailable`

Recommended per-frame sidecar metadata:
1. `valid_time`
2. `source`
3. `product`
4. `observed = true`
5. `frame_interval_minutes` when known

## Frontend Changes

### 1) Add Source-Aware Timeline Semantics
This is mandatory for MRMS.

Current issue:
1. `frontend/src/components/bottom-forecast-controls.tsx` derives display time from `runDate + forecastHour`
2. desktop copy says `Forecast Hour`
3. screenshot text uses `FH`

Required V1 behavior:
1. forecast sources keep the current behavior
2. observed sources derive display labels from `frame.valid_time`
3. observed sources must never show `Forecast Hour` or `FH`

Primary files:
1. `frontend/src/lib/api.ts`
2. `frontend/src/App.tsx`
3. `frontend/src/components/bottom-forecast-controls.tsx`
4. `frontend/src/lib/screenshot_export.ts`

Recommended first-pass implementation:
1. keep internal state names like `forecastHour` to reduce churn
2. introduce derived timeline semantics in the UI layer
3. use capability metadata to switch between `forecast` and `observed`

### 2) Default Observed Sources To The Newest Frame
Required behavior for MRMS:
1. initialize to the newest frame in the bundle
2. preserve current initial-frame behavior for forecast models

This should be controlled by capability metadata such as:
1. `defaults.default_frame_selection = "latest"`

### 3) Keep Playback Generic
Do not rewrite:
1. tile readiness logic
2. loop decode cache
3. buffer snapshot logic
4. WebP fallback path

Only adapt:
1. time labels
2. newest-frame default
3. screenshot and share copy
4. any other hardcoded `FH` assumptions that reach the user

### 4) Sampling Is Included In V1
Because raw scientific values are being ingested, sampling is in scope for the first release.

Implications:
1. MRMS must publish `val.cog`
2. current point-sample and batch-sample APIs must work with MRMS frames
3. hover and anchor sampling should be verified against observed-time semantics

Repo-specific note:
1. request payloads can continue using `forecast_hour` in v1 even though they represent ordered MRMS frame indices internally
2. that naming issue is a later cleanup, not a blocker for shipping MRMS

### 5) User-Facing Freshness And Availability UX
MRMS is a near-real-time source, so freshness must be visible to users.

Required V1 behavior:
1. if MRMS is live, indicate normal operation
2. if MRMS is delayed, indicate that the newest scan is older than expected
3. if MRMS is stale, indicate that clearly in the viewer
4. if MRMS is unavailable, do not leave the user in an ambiguous loading state

Suggested repo touchpoints:
1. `frontend/src/App.tsx`
2. `frontend/src/components/weather-toolbar.tsx`
3. `frontend/src/components/bottom-forecast-controls.tsx`

### 6) Screenshot And Share Labels Must Be Source-Aware
Current issue:
1. screenshot text stamps `FH ${state.fh}`

Required V1 behavior:
1. forecast sources keep current labeling
2. MRMS shows observed timestamp labeling

Example:
1. `MRMS Radar • 20260327_1730z • Observed 5:25 PM CDT`

## Repo Touchpoints

### Backend
1. `backend/app/models/base.py`
2. `backend/app/models/registry.py`
3. new file: `backend/app/models/mrms.py`
4. new file: `backend/app/services/mrms_fetch.py`
5. new file: `backend/app/services/mrms_publish.py`
6. `backend/app/services/colormaps.py`
7. `backend/app/services/builder/colorize.py`
8. `backend/app/main.py`
9. optionally `backend/app/services/scheduler.py`
10. `backend/app/services/admin_telemetry.py`

### Frontend
1. `frontend/src/lib/api.ts`
2. `frontend/src/App.tsx`
3. `frontend/src/components/bottom-forecast-controls.tsx`
4. `frontend/src/lib/screenshot_export.ts`
5. optionally `frontend/src/components/weather-toolbar.tsx`

## Phase Plan

### Phase 0: Contract Prep
Files:
1. `backend/app/models/base.py`
2. `backend/app/main.py`
3. `frontend/src/lib/api.ts`

Work:
1. extend capability metadata for observed timeline support
2. serialize model-level observed-source constraints
3. keep route shapes stable

Done criteria:
1. frontend can identify `time_axis_mode = "observed"`
2. frontend can identify `default_frame_selection = "latest"`
3. existing forecast sources remain unchanged

### Phase 1: MRMS Capability And Palette
Files:
1. new file: `backend/app/models/mrms.py`
2. `backend/app/models/registry.py`
3. `backend/app/services/colormaps.py`

Work:
1. register `mrms`
2. add `reflectivity` capability metadata
3. add a reflectivity palette for CartoSky colorization
4. ensure legend metadata is emitted for the variable

Done criteria:
1. `GET /api/v4/capabilities` includes `mrms`
2. the frontend can list `mrms`
3. capability metadata is sufficient to drive observed-time behavior later

### Phase 2: MRMS Fetch, Build, And Publish
Files:
1. new file: `backend/app/services/mrms_fetch.py`
2. new file: `backend/app/services/mrms_publish.py`
3. `backend/app/services/builder/colorize.py`
4. `backend/app/main.py`
5. optionally `backend/app/services/scheduler.py`
6. optionally new file: `backend/app/services/mrms_poller.py`

Work:
1. discover upstream scans
2. freeze the input scan list for a bundle
3. fetch NOAA/NCEP MRMS GRIB2 inputs
4. decode raw reflectivity values with `wgrib2` as preferred or `pygrib` as fallback
5. build `val.cog`, `rgba.cog`, and sidecars
6. generate loop assets
7. write manifest JSON
8. publish atomically
9. preserve prior `latest` if new publish fails validation
10. validate full-bundle build cost against intended cadence before optimizing for incremental reuse
11. validate `wgrib2` as the production decoder path

Done criteria:
1. `/api/v4/mrms/latest/manifest` resolves
2. `/api/v4/mrms/latest/reflectivity/frames` returns ordered frames with `valid_time`
3. `/api/v4/mrms/latest/reflectivity/loop-manifest` is playable by the existing frontend
4. sampling endpoints work against MRMS frame indices
5. measured full-bundle build time is documented against MRMS cadence expectations
6. deployment assumptions for `wgrib2` are documented

### Phase 3: Observed Timeline UX
Files:
1. `frontend/src/App.tsx`
2. `frontend/src/components/bottom-forecast-controls.tsx`
3. `frontend/src/lib/screenshot_export.ts`
4. optionally `frontend/src/components/weather-toolbar.tsx`

Work:
1. remove forecast-hour language from observed sources
2. open MRMS on the newest frame
3. show observed timestamps in the timeline UI
4. update screenshot and share labeling
5. surface `Live`, `Delayed`, `Stale`, and `Unavailable` MRMS state explicitly in the viewer

Done criteria:
1. MRMS opens on the newest frame
2. no user-facing `FH` text appears for MRMS
3. forecast models still behave the same as before
4. `Live`, `Delayed`, `Stale`, and `Unavailable` MRMS states are visible and understandable to users

### Phase 4: Freshness, Telemetry, And Hardening
Files:
1. `backend/app/services/admin_telemetry.py`
2. `frontend/src/lib/telemetry.ts`
3. `frontend/src/pages/status.tsx`
4. any admin usage and performance views that need MRMS visibility

Work:
1. expose freshness and completeness metadata
2. add MRMS-specific usage and performance telemetry
3. surface stale or incomplete MRMS bundle state in admin views
4. capture end-to-end observation-to-display latency where possible
5. extract shared publish helpers from `scheduler.py` into a neutral module such as `publish_utils.py` so MRMS publish code no longer depends on scheduler-private helpers

Done criteria:
1. stale MRMS bundles are visible operationally
2. latest scan age is measurable
3. incomplete bundle states are diagnosable without filesystem inspection
4. observation-to-display latency is measurable or explicitly documented as unavailable
5. MRMS publish orchestration no longer relies on private scheduler helper reuse

## Testing Plan

### Backend Tests
1. MRMS capability serialization test
2. MRMS manifest contract test
3. frames endpoint test with `valid_time`
4. loop-manifest test
5. sampling test against MRMS `val.cog`
6. publish fallback test proving previous `latest` remains active when new publish fails

Likely test files:
1. `backend/tests/test_mrms_invariants.py`
2. `backend/tests/test_mrms_manifest_contract.py`
3. `backend/tests/test_api_frames_mrms.py`
4. `backend/tests/test_mrms_sampling.py`

### Frontend Tests
1. observed timeline labeling coverage
2. newest-frame default coverage
3. screenshot subtitle formatting coverage

Likely touchpoints:
1. `frontend/src/components/bottom-forecast-controls.tsx`
2. `frontend/src/lib/screenshot_export.ts`

### Manual QA
1. Open MRMS and confirm newest frame is selected.
2. Scrub through MRMS and confirm labels use observed timestamps.
3. Start autoplay and confirm there is no flash or basemap blink.
4. Hover or sample MRMS and confirm scientific values are returned.
5. Switch between MRMS and HRRR and confirm timeline semantics swap correctly.
6. Export a screenshot and confirm it uses observed-time labeling.
7. Force a stale or incomplete MRMS bundle and confirm admin/status surfaces it clearly.

## Biggest Risk
The biggest risk is semantic leakage.

If `fh` remains only an internal frame index but:
1. the scrubber still says `Forecast Hour`
2. screenshots still stamp `FH`
3. telemetry and sampling assumptions quietly become user-visible

then MRMS will work technically while still feeling wrong throughout the app.

The central architectural rule is:
1. CartoSky is no longer only a forecast viewer
2. it is a time-sequenced raster viewer supporting forecast and observed sources

The MRMS work should be implemented with that rule in mind.

## First Release Definition
V1 is done only when all of the following are true:

1. `mrms` appears in capabilities and can be selected in the viewer.
2. The source uses `MRMS Merged Base Reflectivity QC` scientific values, not pre-rendered imagery.
3. The latest-only rolling bundle publishes successfully and immutably.
4. The frames API and loop-manifest API work with the existing viewer.
5. MRMS opens on the newest frame.
6. MRMS uses observed timestamp labeling, not `FH`.
7. Sampling works for MRMS in the same viewer.
8. Forecast products still behave exactly as they do today.

## Suggested Follow-Up After V1
1. Add MRMS precipitation-type products using the same source plugin and publish model.
2. Add optional run-history or bundle-history browsing if it proves useful operationally.
3. Add comparison features such as MRMS versus forecast reflectivity or precipitation type.
4. Revisit broader naming cleanup from `forecast_hour` to `frame_index` after observed-source support is stable.
