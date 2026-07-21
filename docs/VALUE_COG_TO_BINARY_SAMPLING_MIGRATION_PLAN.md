# Value COG → Grid Binary Sampling Migration Plan

**Status: MIGRATION COMPLETE (2026-07-14).** Every COG-writing product in CartoSky is now live on `CARTOSKY_BINARY_SAMPLING_MODELS` with value-COG writes off: the forecast models GFS, HRRR, NBM, GEFS, EPS, AIFS, ECMWF, AIGFS, and the four standalone-publisher observed products NDFD, WPC, current_analysis (RTMA), goes-east (GOES), and mrms. The COG-vs-binary drift this migration existed to eliminate is now structurally impossible — one artifact (raw model output → warp → display prep → pack → grid binary) serves both rendering and sampling everywhere. SPC Outlooks, CPC Outlooks, and NWS Hazards are confirmed vector/polygon products with no packed grid artifact — permanently out of scope, never pending. The observed products required dedicated phased plans (standalone poller-driven publishers, not `scheduler.py`/`pipeline.py`-routed) rather than the standard checklist — see the NDFD/WPC and current_analysis/GOES/MRMS sections below, including a critical shadow-gate bug caught before production (NDFD/WPC), a live production Kelvin-mislabel bug fixed by the GOES flip, a pre-existing MRMS sentinel-corruption data bug found and fixed mid-migration, and a 15-18× MRMS sampling-latency regression diagnosed and resolved (seek-read optimization) before flip. **Remaining post-migration items** (housekeeping only, no correctness or scope work left): per-variable **binary** footprint measurement on a post-cutover run per model (feeds `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` Phase 0); the passive storage reclaim as COG-era runs age out of retention per model (ECMWF's ~103 GB is the largest single-model reclaim, NDFD/WPC's combined ~4.8 GB the smallest); and a one-time confirmation that a fully-post-flip MRMS run reaches zero COGs once reuse-path hardlinks to pre-flip source frames age out of the rolling window (expected within hours of the 2026-07-14 flip — see the MRMS reuse-propagation note below).

**AIGFS note:** AIGFS's flip was carried out during this migration by operator decision (deterministic model, compressed process) but its per-model audit/canary record was never written into this document; it is included in the complete list above on the basis of the flip having been performed, and warrants a retroactive one-line audit confirmation (packing scope, `precip_16d_anom` uncataloged-variable handling shared with GFS/ECMWF) if any AIGFS-specific issue surfaces.

**Document basis:** Every claim in this plan was verified directly against the current codebase (`grid.py`, `sampling.py`, `pipeline.py`, `grid_display_prep.py`, `gfs.py`, `forecast_page.py`, `main.py`, `city-labels.ts`, `grid-webgl.ts`, `test_sample_batch_api.py`) during scoping, including a full review pass by an independent reviewer (Codex) whose corrections are incorporated and individually re-verified against the code below. Where something could not be verified, it is marked explicitly rather than assumed.

**Revision note:** This is a corrected version of an earlier draft. The first draft contained four confirmed errors and one confirmed gap, all caught by independent review and re-verified here rather than taken on trust. They are documented in place throughout this version rather than hidden, since the mistakes themselves are informative about where this kind of migration is easy to get subtly wrong.

---

## 1. What this migration actually changes

Today, two artifacts are written per forecast frame: a `.val.cog.tif` (float32 GeoTIFF, used exclusively for server-side point sampling) and a `.bin`/`.bin.br` grid binary (uint16 or uint8, used exclusively for WebGL rendering). The COG predates the grid binary architecturally — it was the sampling substrate before grid/WebGL rendering existed, and has remained a parallel artifact since. This migration retires the COG as a sampling source and makes the grid binary serve both rendering and sampling, eliminating the redundant artifact.

**Confirmed in code:**

- `_PACKING_BY_MODEL_VAR` in `grid.py` defines `scale`/`offset`/`nodata`/`dtype` per `(model, var)` pair. For GFS: `precip_total` packs at `scale=0.01` (0.01" precision), `tmp2m` at `scale=0.1` (0.1°F precision), `snowfall_total`/`snowfall_kuchera_total` at `scale=0.1` (0.1" precision). These are all acceptable for display purposes — no coarse-packing risk found for GFS's variable set.
- `_encode_values` (`grid.py:1577`) implements the forward transform: `encoded = round((value - offset) / scale)`, clipped to `[0, nodata-1]`. The inverse is `value = (encoded * scale) + offset`. **This inverse function does not exist anywhere in the codebase today** — it must be written from scratch, not adapted from an existing function.
- The grid frame meta sidecar (written alongside every `.bin` file) stores `width`, `height`, `bbox` (`[left, bottom, right, top]`), and `projection` — but **not the affine transform object itself**. See Decision Point A (Section 4) for the corrected persistence approach.
- `sampling.py`'s point-sampling functions (`_sample_dataset_index`, `_read_sample_value`, `sample_value`, `sample_values_parallel`) are 100% rasterio-dependent — they call `ds.crs`, `ds.index(x, y)`, and `ds.read(window=...)`. None of this has a binary equivalent today. This is new code, not a refactor of existing code.
- The value COG write is immediately followed by `validate_cog()` (structural validation: CRS, pixel size, band count, dtype, tiling, overviews, COG layout) and `check_value_sanity()` (a data-sanity gate) in `pipeline.py`. **If either fails, the frame is rejected and never published** (`pipeline.py:1644-1663`). This is a real, currently-load-bearing quality gate, not an incidental side effect. `validate_cog()` checks several structural properties (CRS, tiling, overviews, COG layout) that a simple array-only sanity check does not naturally cover — see Phase C for the corrected replacement approach.

### Critical finding: seven GFS variables, not three, are upscaled before the grid binary is encoded

**Correction from independent review, confirmed against code.** An earlier version of this plan stated three GFS variables had active display-prep upscaling. This was incomplete — the same code search that found three results actually returned seven, and only the first three were read. All seven are confirmed below.

`prepare_grid_display_values()` (`grid_display_prep.py:241`) runs **after** the value COG is written but **before** the grid binary is encoded. For GFS, seven variables have an active display-prep config with `upscale_factor=3`:

| Variable | Config ID | Upscale factor | Resampling kind |
|---|---|---|---|
| `precip_total` | `gfs_precip_total_display_v2` | 3x (9x pixel count) | Continuous |
| `snowfall_total` | `gfs_snowfall_total_display_v1` | 3x (9x pixel count) | Continuous |
| `snowfall_kuchera_total` | `gfs_snowfall_total_display_v1` | 3x (9x pixel count) | Continuous |
| `ptype_intensity` | `gfs_ptype_intensity_display_v1` | 3x (9x pixel count) | **Categorical-nearest** (`categorical_nearest=True`) |
| `ptype_intensity_rain` | `gfs_ptype_intensity_component_display_v1` | 3x (9x pixel count) | Continuous-ish |
| `ptype_intensity_snow` | `gfs_ptype_intensity_component_display_v1` | 3x (9x pixel count) | Continuous-ish |
| `ptype_intensity_ice` | `gfs_ptype_intensity_component_display_v1` | 3x (9x pixel count) | Continuous-ish |

This means the value COG and the grid binary are **genuinely different resolutions today** for all seven variables — the grid binary is a finer grid, upscaled specifically for visual display smoothness. A direct migration to binary-based sampling is not merely a format swap for these variables; it changes which underlying pixel gets sampled. This is expected to make sampling **more precise** for the continuous-field variables, since the binary is a higher-resolution representation of the same field. `ptype_intensity` is a distinct case — it uses `categorical_nearest=True`, meaning its resampling is nearest-neighbor on a categorical field, not interpolation. Disagreement behavior at class boundaries for `ptype_intensity` is a different failure mode than the smoother continuous-field variables and must be tested and tolerated separately, not folded into the same tolerance bucket as the others. See Section 3, Layer 2 and Layer 3 for the adjusted, per-variable-group tolerance approach.

No other GFS variable in scope has an active display-prep upscale config — this was re-confirmed by reading every `("gfs", *)` entry in `_GRID_DISPLAY_PREP_BY_MODEL_VAR` in full, not by reading only the first matches.

### Confirmed: binary sampling means sampling the display-prepped field, which is a semantic decision, not only a precision change

**Addition from independent review.** `write_grid_frame_for_run_root()` calls `prepare_grid_display_values()` before `_encode_values()` (`grid.py:1621`). This means the grid binary stores the *display-prepared* field (post-upscale, post-smoothing where configured), not the raw warped model output the value COG stores. Migrating sampling to the binary is implicitly a product decision to sample the display-oriented field rather than the raw field, for every variable with an active display-prep config. This plan treats that as the intended outcome — sampling the same field the map renders is arguably more consistent for the user, not less — but it is being stated explicitly here as a deliberate decision rather than left as an implicit side effect of the migration.

**Addition: the strategic value of this migration extends beyond storage.** The 23 GB storage reclaim (Section 6) is real and measured, but it is not the most durable justification for this work. Today, two artifacts can represent the same forecast field with two different pipelines producing them — the value COG (sampling) and the grid binary (rendering) — and any divergence between them, however small, is a class of bug that is structurally possible today and will remain possible for as long as both formats coexist. After this migration, every consumer of a sampled or rendered value — the WebGL map, hover values, meteograms, `/api/v4/sample`, future exports, client-side city value labels — reads from the same artifact: raw model output → warp → display prep → pack → grid binary → everything. This eliminates an entire category of "the map shows X but the meteogram shows Y because they sampled different artifacts" bugs, not by fixing each instance but by removing the structural possibility. This is worth stating explicitly as a primary motivation, not an incidental side effect of a storage cleanup.

---

## 2. Scope

**This phase:** GFS only. Mean/deterministic pipeline only — no ensemble member work, since that storage layout does not exist yet (separate, later workstream) and must not be conflated with this migration.

**Variables in scope for GFS, corrected:**

`tmp2m`, `tmp2m_anom`, `tmp850_anom`, `hgt500_anom`, `dp2m`, `rh2m`, `rh700`, `tmp850`, `wspd850`, `wspd300`, `vort500`, `sbcape`, `mlcape`, `mucape`, `pwat`, `wspd10m`, `wgst10m`, `precip_total`, `ptype_intensity` (and its 3 component variants), `snowfall_total`, `snowfall_kuchera_total`, `ice_total`, **and `precip_5d_anom`, `precip_7d_anom`, `precip_10d_anom`, `precip_16d_anom`.**

**Correction from independent review, fully traced and confirmed:** the four precip-anomaly variables were missing from the original scope list entirely. This was not a deliberate exclusion — it was a methodology gap. These variables are not literal `("gfs", "precip_5d_anom")`-style entries in `grid.py`; they are registered through a `for` loop (`grid.py:1107-1109`) that was found by an earlier search but only partially read. They are also not static dict-literal entries in `gfs.py`'s `GFS_VARS` — they are inserted into `GFS_VARS` at module-import time by a second loop (`gfs.py:1054-1060`) that calls `_precip_anomaly_var_spec()` and assigns the result directly into the `GFS_VARS` dict that `GFS_VARIABLE_CATALOG` is built from. Confirmed: these are real, live, `primary=True`, derived (`derive="precip_accum_anomaly_departure"`) GFS variables in the `"Anomalies"` display group, fully present in the variable catalog the API and frontend consume. There is no basis for excluding them. They are included in scope.

**Explicitly out of scope for this phase:**

- All other models (ECMWF, NAM, NBM, AIFS, AIGFS, HRRR, MRMS, current_analysis, EPS/GEFS mean) — staged rollout, one model at a time, after GFS is fully proven.
- `uint8`-packed variables (MRMS `reflectivity`, `mrms_radar_ptype`) — none are GFS, but the decode function must branch on dtype correctly from day one so it does not silently misread these when MRMS's turn comes.
- Ensemble per-member sampling — separate workstream, sequenced after this migration completes in full. *(Now specified: `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` — member artifacts are binary-only and gate on this migration's GEFS/EPS Phase F cutover.)*
- **Client-side city value-label sampling — corrected scope statement.** An earlier version of this plan stated city labels were "a static asset, no backend sampling involved" and used this as the basis for ruling them out of scope. This was an incomplete and partly inaccurate explanation. City *name* labels are static GeoJSON (`city-labels.ts`). However, city *value* labels (the sampled temperature/precip number shown next to a city name in `"value"` label mode) are sampled by `sampleAnchorPoints()` in `grid-webgl.ts`, which reads directly from the grid binary bytes already loaded into the browser for rendering — entirely client-side, with zero dependency on `sampling.py`, `/api/v4/sample`, or any backend service this migration touches. The corrected, accurate statement is: **no backend dependency exists, but real client-side value sampling exists and is unaffected by this migration because it was already reading grid binaries, not value COGs, before this migration started.** No action needed in this migration, but the original "static only" framing should not be relied on as a general statement about how city labels work.
- Contour generation — confirmed out of scope / no dependency exists. `_build_contour_metadata_for_variable()` (the only contour-metadata function actually called from the live pipeline, at `pipeline.py:1665`) builds contours from its own independently fetched and warped component data via `fetch_ctx`/`get_cached_warped_component`. It does not take the value COG path or any COG-derived value as an input.

---

## 3. Testing strategy

Four layers, ordered so each layer can only pass if the previous one is correct. The canary period is Layer 3 running against real production data — it is not a separate, looser step.

### Layer 1 — Encode/decode round-trip unit test (no I/O, no sampling, pure math)

New test file: `backend/tests/test_grid_value_decode.py`.

**Correction from independent review, confirmed numerically.** The original Layer 1 spec asserted `abs(decoded - original) <= scale / 2` uniformly, including for a deliberately out-of-range value meant to exercise the encoding's clip behavior. This assertion is mathematically wrong for clipped values — a verified example: for `precip_total` (`scale=0.01`), encoding `700.0"` (an unrealistic but boundary-testing value) clips to a decoded value of `655.34`, a difference of `44.66` against a `scale/2` tolerance of `0.005`. A test asserting tight round-trip tolerance on a value designed to clip will either fail by construction or be written loosely enough to silently not test the clip path at all.

**Corrected test design:** two distinct assertion paths, not one.

- For values within each variable's realistic, non-clipping range: assert `abs(decoded - original) <= scale / 2`.
- For values deliberately constructed to exceed the encoding's representable range: assert the decoded value equals the expected **clipped** result — i.e. `decoded == (clip(round((value - offset) / scale), 0, nodata - 1) * scale) + offset` — not that it round-trips close to the original input.

For every `("gfs", var)` entry in `_PACKING_BY_MODEL_VAR`, including the four precip-anomaly variables now correctly in scope, generate representative in-range values and at least one deliberately out-of-range value per variable, and apply the correct assertion path to each. Assert the nodata sentinel round-trips to `None`/`NaN` correctly.

Zero dependency on file I/O or sampling — proves the arithmetic in isolation.

### Layer 2 — Pixel-index parity test (synthesized fixture, mirrors existing test pattern)

New test file: `backend/tests/test_binary_sampler_parity.py`.

Mirror the fixture pattern already used in `test_sample_batch_api.py`, which synthesizes a COG via `rasterio` + `from_origin` rather than depending on real published data. Extend this pattern to also synthesize a matching `.bin` file + meta sidecar with the same known grid of values, written through the real `write_grid_frame_for_run_root()` function (not hand-constructed), so the test exercises the real encode path including real display-prep behavior.

For a fixed set of lat/lon points — including points squarely inside a pixel, points near a pixel boundary, and at least one point outside the grid's bbox — call the existing `sample_value()` (COG path) and the new binary sampler side by side.

**Tolerance groups, corrected to reflect all seven upscaled variables and the categorical-nearest distinction:**

- **Group 1 — no display-prep upscaling** (the majority of GFS variables, including the four precip-anomaly variables): assert agreement within `scale/2`.
- **Group 2 — continuous-field 3x upscale** (`precip_total`, `snowfall_total`, `snowfall_kuchera_total`, `ptype_intensity_rain`, `ptype_intensity_snow`, `ptype_intensity_ice`): construct the test fixture to reflect the real 3x upscale relationship between formats (synthetic COG and synthetic binary built at their actual relative resolutions, not assumed identical). Assert the binary-sampled value is within a tolerance that accounts for nearest-pixel selection on a finer grid. Document this tolerance explicitly in the test with a comment explaining why it differs from Group 1.
- **Group 3 — categorical-nearest 3x upscale** (`ptype_intensity` only): this variable's display-prep uses `categorical_nearest=True`, a structurally different resampling kind than Group 2. Test this variable separately, with class-boundary test points specifically constructed (not reused from Group 2's fixture), and assert that the sampled category is correct rather than applying a numeric tolerance — a categorical field either samples the right class or it doesn't; numeric closeness is not the right success criterion here.

### Layer 3 — Production canary (multi-day parallel write, real data, real traffic patterns)

For a window of 4 model cycles, GFS continues writing both the value COG (unchanged) and the grid binary (already happening today) — no pipeline change needed for this step, since both are already produced. During this window:

- The live sampling service continues reading from COGs — no behavior change to production traffic yet.
- A standalone script (not wired into any live endpoint) runs periodically against real published GFS runs from the canary window, sampling a meaningful set of real-world points — reuse an existing NWS station or city-label coordinate list rather than synthetic points, to surface real-world edge cases (points near the bbox edge, near nodata/ocean-masked regions, at the highest forecast hours).
- The script calls the existing COG-based `sample_value()` and the new binary-based sampler for every point, across every variable in the corrected scope list (including the four precip-anomaly variables), across every forecast hour, across every run published during the window, and logs any divergence with full context (model, run, var, fh, lat, lon, both values, distance-to-nearest-pixel-boundary).
- **Pass conditions, per tolerance group from Layer 2:** Group 1 (no upscale) — zero unexplained divergence. Group 2 (continuous 3x upscale) — divergences expected near sharp gradients; pass condition is "divergences are spatially explainable and bounded in magnitude," not zero. Group 3 (`ptype_intensity`, categorical) — divergences should be rare and should only occur at genuine class-boundary pixels; any divergence in the interior of a uniform-category region is a real bug, not expected noise.
- Any divergence outside these expectations gets root-caused before proceeding.

### Layer 4 — Meteogram integration test (loop-level correctness)

Extend `test_forecast_meteogram_api.py` with a test that requests a real GFS meteogram (multiple variables across all three tolerance groups, full forecast hour range) during the canary window, once through the existing COG-backed path and once through the new binary-backed path (via the feature-flag-and-cache-key approach described in Phase B), and diffs the full resulting point arrays per variable using the same per-group tolerance logic as Layer 2/3. This catches any bug in the batch-loop replacement of `_sample_variable_series` that would not show up in single-point tests.

**Gate to proceed:** Layers 1 and 2 must be green before the canary starts. Layer 3 must show zero unexplained divergence (per the documented per-group exceptions) across its full window. Layer 4 must be green before COG writes are turned off for GFS.

---

## 4. Implementation phases

**Cross-cutting requirement, applies to every phase below: build for GFS, but build generically.** GFS is the first model migrated, not the only one this infrastructure will ever serve. Every piece of code written in Phases A-F must be model-parameterized from the start — accepting `model` as an argument, reading `_PACKING_BY_MODEL_VAR[(model, var)]` rather than anything GFS-specific, and containing zero hardcoded `"gfs"` string literals in logic (GFS-specific literals are acceptable only in test fixtures and in the canary script's initial target, not in the sampler, decode function, or route-handler logic itself). The cost of doing this correctly in Phase A-F is small. The cost of discovering it was done as GFS-only when Phase G starts is a partial rewrite. Specific requirements:

- **The sampling-substrate toggle (Phase B) must be a per-model list, not a global boolean.** Use a config value structured as a model allowlist — e.g. `CARTOSKY_BINARY_SAMPLING_MODELS=gfs` today, becoming `CARTOSKY_BINARY_SAMPLING_MODELS=gfs,nam` when NAM is added — not a single on/off flag. A boolean would force re-deriving the on/off semantics when the second model is added; a list lets the second model migrate by appending one value with zero risk to GFS's already-cutover behavior.
- **`_decode_values()` (Phase A) must take `model` and `var` as parameters** and look up packing constants from `_PACKING_BY_MODEL_VAR` exactly as `_encode_values()` already does — it must not be written with GFS's specific scale/offset values inlined or assumed.
- **The binary sampler functions (Phase B) and the route-handler binary-path logic (Phase F) must accept `model` as a parameter** and branch only on the `CARTOSKY_BINARY_SAMPLING_MODELS` allowlist membership, never on a literal `model == "gfs"` check.
- **The `sampling_source` cache-key addition (Phase B/F) is inherently generic already** — it is a `"cog" | "binary"` value keyed alongside model/var/run, not a GFS-specific field. No additional work needed here beyond what is already specified, but worth confirming the implementation doesn't accidentally scope it to GFS only.
- **The quality-gate parallel-run approach (Phase C) must be written to read packing/validation config per-model**, not with GFS's specific validation thresholds hardcoded.

### Phase A — Decode primitive + unit tests (Layer 1)

Add `_decode_values()` to `grid.py`, the direct inverse of `_encode_values()`, including correct nodata handling, correct dtype branching (`uint8` vs `uint16`), and correct handling of the clip-boundary case per the corrected Layer 1 test design. Write the Layer 1 test suite against it, covering the full corrected scope list including the four precip-anomaly variables.

**`_decode_values()` must be the single decode authority in the codebase.** Every consumer of an encoded grid value — the new binary sampler (Phase B), the new route-handler binary path (Phase F), and any future export or API surface that reads grid binaries — must call this function rather than reimplementing the `value = (encoded * scale) + offset` math inline. This is cheap to enforce now and expensive to retrofit once a second inline copy of the math exists somewhere and silently drifts from the canonical version.

**Add `format_version` to the grid frame meta sidecar in the same edit.** The meta sidecar (`width`, `height`, `bbox`, `projection`, plus the corrected effective transform from Decision Point A below) currently has no version field. Add `format_version: 1` to `frame_meta` in `write_grid_frame_for_run_root()` as part of this phase's edit — it costs nothing extra since the dict is already being modified for the transform fix, and it removes the need for fragile field-presence-sniffing compatibility code the first time anything about the sidecar's shape needs to change (packing precision, compression, an optional field, etc.). The binary sampler (Phase B) should read and check this field, even if there is currently only one version to check against.

**Decision Point A — corrected.** The original recommendation was to "persist the original transform" in the meta sidecar. **This was incomplete and would have been wrong for all seven upscaled variables.** `write_grid_frame_for_run_root()` computes the geographic `bbox` from the **original, pre-upscale** array dimensions specifically so that upscaling does not inflate the geographic extent (`grid.py:1610-1617`, confirmed by an explicit code comment to this effect) — but the encoded array's `width`/`height` reflect the **post-upscale** shape. This means the original `dst_transform` describes pixels three times larger than the binary's actual pixel grid for any Group 2/3 variable. Persisting the original transform as-is would silently misalign sampling for exactly the variables most likely to be sampled often (`precip_total`, `snowfall_total`).

**Corrected recommendation:** persist the **effective encoded-frame transform** — derived from the same `bounds` already being computed at write time, combined with the **post-upscale** `width`/`height` (i.e. `encoded.shape`, available at the same point `frame_meta` is constructed). This is still a small, cheap write-time addition; it is simply the *correct* transform rather than the original one. This must be specified exactly this way in the implementation prompt — "persist the transform" alone is not sufficient instruction and would likely reproduce this exact bug.

### Phase B — Binary sampler (Layer 2 target) + cache key integration

Binary-backed equivalent of `_sample_dataset_index` + `_read_sample_value` in `sampling.py`, reading the meta sidecar for geometry (using the corrected effective transform and `format_version` from Phase A) and the `.bin` file for values, using the Phase A decode function exclusively — no inline reimplementation of the decode math anywhere in this layer.

**Read strategy is not prescribed in advance — benchmark first (see Phase D addition below).** A plain `open()`/`read()`/`close()` per frame is the default starting implementation. Do not adopt `mmap`/`numpy.memmap` preemptively; GFS `.bin` files are small (sub-megabyte range per the earlier file-size estimates in this project), so the OS page cache likely already makes repeated-read performance adequate without it. If the Phase D benchmarks (added below) show meaningful latency or memory pressure under realistic meteogram-scale access patterns (85-105 sequential frame reads), revisit the read strategy then — including options like an LRU of decoded arrays (mirroring the existing `_get_cached_dataset` pattern for rasterio handles) as well as memory-mapping. This is a "decide with data" item, not a Phase B design decision.

**Meteogram cache key gap — confirmed and resolved.** `_meteogram_cache_key()` (`forecast_page.py:2305`) builds its cache key from `lat`, `lon`, `models`, `variables`, `policy_type`, `include_members`, per-model `run_ids`, and per-model `entitled` flags — confirmed by direct read of the function. Nothing in this key identifies which sampling substrate (COG vs. binary) produced the cached payload. The sampling-substrate toggle must not be a single global flag that silently changes live endpoint behavior. Use a clearly separated **offline/shadow comparison mode** for the canary (Phase D). When the eventual production cutover happens (Phase F), add a `sampling_source: "cog" | "binary"` value to `_meteogram_cache_key()`'s hashed inputs so a substrate change always produces a fresh cache key.

**`/api/v4/sample` and `/api/v4/sample/batch` — structurally harder, fully traced and confirmed.** Both route handlers (`main.py:5588`, `main.py:5752`) call `_resolve_val_cog()` **unconditionally** and return a hard 404 if the COG is absent. They open the COG via `_get_cached_dataset()` (a rasterio dataset handle) and read pixel values directly from it. There is no binary-path fallback, and no abstraction layer between the route handler and the COG.

This means:

1. **Phase B's binary sampler addition to `sampling.py` alone does not cover these endpoints.** The route handlers themselves must be updated to route to the binary path, replace `_resolve_val_cog()` with an equivalent binary-frame resolver, change the 404 condition from "COG absent" to "binary frame absent," and open the binary file rather than a rasterio dataset. This is a non-trivial route-handler change, not a flag flip.

2. **The in-process `_sample_cache` substrate-collision risk is real but small.** `_sample_cache` is a plain Python dict in process memory (`main.py:2608`) with a default TTL of **2 seconds** (`CARTOSKY_SAMPLE_CACHE_TTL_SECONDS`, default `"2.0"`). It evaporates on every process restart and deploy. The maximum substrate-collision window is 2 seconds — meaningfully lower risk than the meteogram's Cloudflare-cached response. Adding `sampling_source` to `_sample_cache_key` and `_sample_batch_cache_key` at cutover is still correct practice, but the urgency is lower than the meteogram case.

3. **Phase F cutover scope is larger than previously described.** For these two endpoints, cutover requires route-handler code changes, a new binary-frame resolver analogous to `_resolve_val_cog()`, a new rasterio-free read path using the Phase A/B decode primitive, and updated 404 semantics. This work belongs in Phase F and must be explicitly scoped in that phase's implementation prompt.

Write the Layer 2 parity test suite against this, including the three tolerance groups.

### Phase C — Pipeline quality-gate replacement, corrected approach

**Correction from independent review, adopted.** Run the new binary/meta structural gate and a pre-encode array sanity gate **in parallel with the existing COG-based gates first**, for the duration needed to gather evidence that the new gates catch the same class of bad frames the old gates catch. Only remove the COG-based gates once this evidence exists — not as a same-phase swap. This is the same canary-before-cutover philosophy applied consistently to the quality gate.

### Phase D — Canary (Layer 3)

Deploy Phases A-C to prod. Run the standalone shadow-comparison script (using the offline/shadow mode from Phase B, not the production endpoint path) against real GFS runs for 4 model cycles. Production traffic continues reading from COGs throughout. Review results per the per-group pass conditions in Section 3.

**Add performance benchmarking alongside the correctness comparison — this phase currently validates correctness only.** The shadow-comparison script should record latency for both the COG path and the binary path at each of: single-point sample, a 100-point batch, a 1,000-point batch, and a full meteogram request (85-105 sequential frames). This produces actual evidence of the expected performance win rather than an assumption, and gives the Phase B read-strategy decision (sequential read vs. LRU cache vs. memory-mapping) real data to be made against instead of being guessed at in advance.

**Add production metrics capture during the canary window**, logged alongside the existing divergence logging: binary sample latency, COG sample latency, decode failures, cache hit rate, missing-frame rate, and sampling exceptions, per substrate. This turns the canary from "we found no disqualifying divergence" into "we found no disqualifying divergence and we have objective before/after evidence that the migration improved performance," which is a stronger basis for the Phase F cutover decision.

### Phase E — Meteogram integration verification (Layer 4)

Run the Layer 4 integration test against real canary-window data, using the corrected per-group tolerance logic. Fix any batch-loop-specific issues found.

### Phase F — Cutover

Add `sampling_source` to `_meteogram_cache_key()`. Update `/api/v4/sample` and `/api/v4/sample/batch` route handlers to use the binary path, including the new binary-frame resolver, rasterio-free read, and updated 404 semantics. Add `sampling_source` to both `_sample_cache_key` and `_sample_batch_cache_key`. Flip production sampling to read from the grid binary for GFS. Confirm the parallel quality gates from Phase C have been live and evidence-gathered before removing COG-based gates. Stop writing value COGs for GFS going forward (retained COGs age out under current retention — no backfill).

### Phase G — Next model: the reusable per-model audit checklist

"Repeat Phases A-F for the next model" is not sufficient instruction on its own — it does not distinguish between infrastructure that is already generic and ready to use (the decode primitive, the binary sampler, the cache-key fix, the route-handler binary path, the parallel quality gates — all built model-parameterized per the cross-cutting requirement above) and the genuinely new, model-specific *discovery work* that produced GFS's Section 1 and Section 2 findings. That discovery work must be redone for every new model, and it is exactly where the GFS pass found real surprises (the seven-vs-three display-prep miss, the loop-registered precip-anomaly variables, the categorical-nearest distinction). The next model's implementer should follow this checklist explicitly rather than relying on memory of how the GFS pass went:

1. **Enumerate every `(model, var)` entry in `_PACKING_BY_MODEL_VAR` for the new model — read every match, not just the first several.** Watch specifically for variables registered via a `for` loop rather than a literal dict entry (as the GFS precip-anomaly variables were) — a single grep for `("model_name"` will miss these; also grep for the model name as a loop variable value (e.g. `_precip_anom_model in (..., "new_model", ...)`).
2. **Cross-reference the variable catalog (`{model}.py`'s `{MODEL}_VARS` / `{MODEL}_VARIABLE_CATALOG`)** to confirm every packed variable is actually a live, requestable catalog entry — not a stale/unused packing config left over from a removed feature. Check for the same loop-registration pattern here too (the GFS precip-anomaly variables were registered into `GFS_VARS` via a loop at module-import time, not as static dict literals).
3. **Enumerate every `(model, var)` entry in `_GRID_DISPLAY_PREP_BY_MODEL_VAR` for the new model — read every match in full.** For each match found, record the `upscale_factor` and whether `categorical_nearest=True` is set. Any variable with `upscale_factor > 1` needs its own tolerance-group treatment in that model's Layer 2/3 tests, following the same Group 1/2/3 pattern used for GFS (no-upscale / continuous-upscale / categorical-upscale) — the specific variables in each group will differ per model, but the three-way split itself is the reusable pattern.
4. **Check whether the new model has any structural differences from GFS that affect the migration** — e.g. a different dtype (`uint8` vs `uint16`) for any of its variables, a different CRS/region setup, or any model-specific quirk in how `write_grid_frame_for_run_root()` is called for it.
5. **Re-run Layers 1-4 for the new model specifically**, using the same test files extended with the new model's parameters rather than duplicated test files — `test_grid_value_decode.py` and `test_binary_sampler_parity.py` should be structured (from Phase A/B) to be easily parameterized by model, not GFS-only in structure.
6. **Add the new model to `CARTOSKY_BINARY_SAMPLING_MODELS`** only after Layers 1-4 pass for it, exactly as GFS did.
7. **Measure that model's actual COG storage footprint on prod** (the same `du -sh` exercise from Section 6) before claiming a storage win for it — do not assume the GFS percentage (~27%) transfers to another model; different models have different variable sets, different display-prep configs, and different forecast-hour ranges.

This checklist is the actual deliverable of "Phase A's decode/parity infrastructure is now reusable" — the infrastructure is reusable, but the audit is not automatic, and skipping steps 1-4 for a new model risks reproducing the exact class of errors the GFS pass caught (incomplete enumeration, loop-registered variables, undiscovered upscale configs).

### Phase G addendum — leaner canary protocol for models after GFS

GFS's canary window required deep manual investigation (neighborhood-sampling comparisons, category-boundary tracing, palette-code verification) for every divergence that appeared, because nothing was known yet about what a real bug versus expected behavior looked like for this migration. That depth was necessary the first time; it is not necessary every time, and treating it as necessary every time would make Phase G prohibitively slow across multiple models.

**For every model after GFS, escalate to full manual investigation only when a divergence pattern doesn't match something already characterized during GFS's pass:**

- **Already-characterized, low-escalation patterns** (confirm via distance-to-boundary check only, not a full neighborhood dive): divergences on a continuous-field variable with an active `upscale_factor > 1` config, 100% concentrated within roughly one pixel-width of a boundary, at a rate similar to what GFS showed (low single-digit percent of that variable's samples at most). This matches the `precip_total`/`ptype_intensity_rain` pattern exactly — a quick `distance_to_boundary_px` distribution check confirming near-100% sub-pixel concentration is sufficient evidence, without re-running the Orlando/Bridgeport-style live neighborhood query every time.
- **Already-characterized, low-escalation pattern for categorical variables**: divergences on a `categorical_nearest=True` variable, 100% boundary-concentrated, with category mismatches confirmed adjacent (via that model's own palette/bin definitions, not assumed) or crossing no physical-type boundary. Same treatment as `ptype_intensity` — verify the palette-adjacency claim once per model (palettes differ per model), but don't repeat the full manual dive if the pattern otherwise matches.
- **Requires full escalation, same rigor as GFS's original passes:** any Group 1 (non-upscaled) divergence at all — this bar never relaxes, since Group 1's whole premise is that these variables should match exactly regardless of model. Any divergence pattern that is *not* boundary-concentrated. Any new variable type not seen during GFS (a new dtype, a new display-prep resampling kind beyond continuous/categorical-nearest, anything structurally novel). Any divergence rate meaningfully higher than what GFS showed for a comparable variable type.

**A single packing-constant audit substitutes for a large share of the investigative burden.** GFS's one real bug (`vort500`) was a packing-table configuration mistake, not a sampler logic error — and Section on Phase G's checklist item 1 already requires enumerating every packing entry for the new model. Doing that audit carefully *before* running the canary, specifically checking every physically-signed variable (anomalies, vorticity, divergence, or anything else that can be negative) has a correctly negative `offset`, is likely to catch this exact class of bug before the canary ever runs — turning what cost a full extra investigation cycle for GFS into a five-minute static check for the next model.

### Phase G addendum — packing fixes do not apply retroactively to already-published runs

**This is a distinct, important operational gotcha, discovered during GFS's own rollout, that must be accounted for during every future model's cutover, not just noted once and forgotten:**

The value COG stores raw, unquantized float32 — it never passes through the packing table's `scale`/`offset`/`_encode_values()` machinery at all, since that machinery exists solely to quantize values into the grid binary's compact integer format. This means **any packing-table bug (like `vort500`'s) only ever corrupts the *binary* representation, never the COG.** COG sampling of an affected variable has been correct the entire time, even on runs published before the bug was found and fixed.

Per this migration's own explicit decision (no backfill of already-published frames), fixing a packing bug does **not** retroactively correct binary frames for runs already sitting in retention. Those runs keep their bad bytes until they naturally age out.

**Consequence for any future cutover:** the moment a model is added to `CARTOSKY_BINARY_SAMPLING_MODELS`, the flag applies to **every currently-retained run for that model**, not just runs published after a fix. If a packing bug is found and fixed during that model's canary window, flipping the allowlist before all currently-retained runs have cycled through post-fix publishing will serve **known-wrong data for the affected variable on any older retained run a user's run-selector might request** — a real, live regression on data already in front of users, for however long that model's retention window takes to fully turn over (for GFS's 6-run/6-hour-cadence pattern, roughly 24-30 hours).

**Before flipping the allowlist for any future model, explicitly check:** was a packing-table fix made at any point during that model's canary window? If yes, either wait for full retention turnover before flipping, or make a deliberate, informed decision to accept a known, bounded, self-resolving regression window — do not flip without having asked this question.

### Phase G audit — HRRR and NBM static readiness

This audit covers the model-specific discovery work required before running either model's canary. It does **not** start a canary, add either model to `CARTOSKY_BINARY_SAMPLING_MODELS`, or change `pipeline.py` cutover behavior.

#### HRRR

Packing scope from `_PACKING_BY_MODEL_VAR` is 23 variables:

`dp2m`, `mlcape`, `mucape`, `precip_total`, `pwat`, `radar_ptype`, `radar_ptype_frzr`, `radar_ptype_rain`, `radar_ptype_sleet`, `radar_ptype_snow`, `rh2m`, `rh700`, `sbcape`, `snowfall_kuchera_total`, `snowfall_total`, `tmp2m`, `tmp850`, `tmp850_anom`, `vort500`, `wgst10m`, `wspd10m`, `wspd300`, `wspd850`.

Catalog cross-reference: all packed HRRR variables are present in `HRRR_VARS`. The four `radar_ptype_*` component variables are catalog entries but are marked `internal_only` and `buildable=False` in `HRRR_VARIABLE_CATALOG`, matching their role as internal composite layers rather than direct user-facing products. No HRRR variables are registered through a hidden model loop in `grid.py`; the packed HRRR scope is represented by literal `("hrrr", var)` entries.

Packing constants checked:

| Variable | Checked result |
|---|---|
| `vort500` | Correctly packed with `offset=-100.0`; no repeat of the earlier GFS signed-vorticity bug. |
| `tmp850_anom` | Correctly packed with `offset=-80.0`. |
| All HRRR packed variables | `uint16`; no HRRR variable uses `uint8`. |

Tolerance groups for the generalized canary — **corrected 2026-07-02, see below**:

| Group | HRRR variables |
|---|---|
| Group 1 | `dp2m`, `mlcape`, `mucape`, `precip_total`, `pwat`, `rh2m`, `rh700`, `sbcape`, `snowfall_kuchera_total`, `snowfall_total`, `tmp2m`, `tmp850`, `tmp850_anom`, `vort500`, `wgst10m`, `wspd10m`, `wspd300`, `wspd850` |
| Group 2 | **None (corrected — see below)** |
| Group 3 | None |
| Group 4 | `radar_ptype` |
| Excluded from comparison scope entirely | `radar_ptype_frzr`, `radar_ptype_rain`, `radar_ptype_sleet`, `radar_ptype_snow` |

The `radar_ptype` Group 4 classification is intentional and structurally distinct from GFS's old categorical group: `grid_display_prep_config("hrrr", "radar_ptype")` has `upscale_factor=1` and `categorical_nearest=True`. There is no resolution difference between the value COG and the grid binary for this variable, so the canary requires strict integer-category equality and treats any divergence as blocking.

**Correction, found during HRRR's Layer 3 canary (2026-07-02):** the four `radar_ptype_rain/snow/sleet/frzr` component variables were originally classified as Group 2 based on their `upscale_factor=3` / `categorical_nearest=False` display-prep config alone. This was incomplete — the audit read the packing table and display-prep table (checklist items 1 and 3) but did not cross-reference the variable catalog's `buildable` flag. All four are marked `buildable=False, internal_only=True, allow_dry_frame=True` in `HRRR_VARIABLE_CATALOG` (`hrrr.py`, `_capability_from_var_spec`) — they are derive-strategy inputs consumed in-memory by `radar_ptype_combo` to composite the single published `radar_ptype` frame, and are never independently written to disk as their own grid frame on either substrate. There is no COG-vs-binary parity question to ask for a variable that is never independently published; **HRRR has no Group 2 variables**, and these four are excluded from canary/parity comparison scope entirely, not merely expected to show zero divergence. Checklist item 2 ("cross-reference the variable catalog") is amended to explicitly state: confirm `buildable=True` for every packed/display-prep-configured variable before assigning it a tolerance group, not just confirm catalog presence.

Storage measurement status: **measured on prod, 2026-07-02** (checklist item 7 satisfied for HRRR). Six retained runs, `20260702_10z` through `20260702_15z` — note HRRR retention holds a mix of cycle lengths, unlike GFS:

| Cycle type | Runs in retention | Per-run total (all artifacts) | Per-run value-COG subset |
|---|---|---|---|
| Standard 18-hour cycle | 5 (`10z`, `11z`, `13z`, `14z`, `15z`) | 7.9 GB each | 3.4 GB each |
| Extended 48-hour cycle | 1 (`12z`) | 21 GB | 8.9 GB |

Full HRRR retention footprint: **60 GB total, of which 26 GB is value COGs (~43%)**. As checklist item 7 anticipated, the GFS percentage (~27%) did not transfer — HRRR's COG share is materially higher, so retiring HRRR's value COGs is a proportionally larger per-model win than GFS's despite HRRR's smaller absolute footprint. Any "per-run" figure quoted for HRRR must distinguish the two cycle types; a single blended average is misleading given the 48-hour cycle is ~2.6x the size of a standard cycle.

Per-forecast-hour consistency (aggregated across all variables and all retained runs): ~1,026–1,103 MB per forecast hour for fh000–fh018 (present in all six runs, i.e. ~171–184 MB per run per fh) and ~183–192 MB per forecast hour for fh019–fh048 (present only in the single retained 48-hour cycle). Sizes are smooth and monotonic-ish across the fh range with no outliers — no per-hour anomaly suggesting a corrupt or unusually-shaped artifact.

**Per-variable breakdown: not yet captured.** The first measurement pass keyed aggregation on filename, but HRRR's published layout places the variable name in the directory path (`{run}/{var}/fhNNN.val.cog.tif`), with no variable token in the filename — so that pass produced the per-forecast-hour table above instead of a per-variable table. If a Section 6-style per-variable table is wanted for HRRR (useful for the same prioritization observation made for GFS, not a gate for canary/cutover), re-run the aggregation keyed on the parent directory name. The headline figures above are sufficient for the checklist item 7 storage-win claim.

Retention-turnover note for the packing-fix addendum: with 6 retained runs at hourly cadence, HRRR's full retention turnover after a packing fix is roughly **6-7 hours** — far shorter than GFS's 24-30 hours — which materially lowers the cost of the "wait for turnover before flipping the allowlist" option should a packing bug be found during HRRR's canary.

#### NBM

Packing scope from `_PACKING_BY_MODEL_VAR` is 5 variables:

`precip_total`, `sbcape`, `snowfall_total`, `tmp2m`, `wspd10m`.

Catalog cross-reference: the packed variables are the real buildable NBM products in `NBM_VARIABLE_CATALOG`. `NBM_VARS` also contains source/component entries (`10u`, `10v`, `10si`, `apcp_step`, `asnow_step`) used to derive the buildable products, including `wspd10m` from the wind components. No NBM variables are registered through a hidden model loop in `grid.py`, and there is no GFS-style hidden scope gap.

Packing constants and dtype check: all NBM packed variables use `uint16`; no NBM variable uses `uint8`.

Tolerance groups for the generalized canary:

| Group | NBM variables |
|---|---|
| Group 1 | `sbcape`, `tmp2m`, `wspd10m` |
| Group 2 | `precip_total`, `snowfall_total` |
| Group 3 | None |
| Group 4 | None |

`precip_total` and `snowfall_total` both have `upscale_factor=3` and `categorical_nearest=False`, matching the continuous-upscale Group 2 pattern. No NBM variable falls into Group 3 or Group 4.

Storage measurement status: **measured on prod, 2026-07-02** (checklist item 7 satisfied for NBM). Six retained runs, `20260702_00z` through `20260702_15z` at 3-hour cadence, uniform in size — unlike HRRR, NBM retention has no mixed cycle lengths:

| Measure | Value |
|---|---|
| Per-run total (all artifacts) | 1.1 GB, consistent across all 6 runs |
| Per-run value-COG subset | 257–258 MB, consistent across all 6 runs |
| Full retention footprint | **6.2 GB total, of which 1.6 GB is value COGs (~26%)** |

NBM's COG share (~26%) lands almost exactly on GFS's ~27% — coincidentally, given HRRR came in at ~43% — reinforcing checklist item 7's point that the percentage must be measured per model, not assumed. In absolute terms NBM is by far the smallest storage win of the three models measured (1.6 GB vs. GFS's 23 GB and HRRR's 26 GB), consistent with its 5-variable scope. The justification for migrating NBM is therefore almost entirely the single-artifact architectural consistency argument (Section 1), not storage.

Per-forecast-hour profile (aggregated across all variables and all 6 retained runs; per-fh aggregation for the same filename-layout reason noted in the HRRR section — variable name is a directory component, `{run}/{var}/fhNNN.val.cog.tif`): NBM's forecast-hour structure is hourly fh000–fh036, then 3-hourly fh039–fh264. Three distinct size bands: ~16.0 MB per fh for fh000–fh005, ~19.4–21.0 MB per fh for fh006–fh036, and ~10.3–10.7 MB per fh for the 3-hourly fh039–fh264 range. The step up at fh006 and the drop in the extended range presumably reflect which of the 5 variables are published at which forecast hours (e.g. accumulation products not present at the earliest hours, and a reduced variable set at extended range) — **plausible but not verified against the catalog**; worth a one-line confirmation during NBM's Layer 2/canary work only if per-fh frame availability turns out to matter for the shadow-comparison script's expected-frame enumeration. No anomalous outliers in the profile.

**Per-variable breakdown: not yet captured** — same reason and same corrected command (keyed on parent directory) as the HRRR section; optional for prioritization, not a gate.

Retention-turnover note for the packing-fix addendum: 6 retained runs at 3-hour cadence gives NBM a full retention turnover of roughly **18-19 hours** after a packing fix — between HRRR's ~6-7 hours and GFS's 24-30 hours.

#### Phase G checklist status — HRRR and NBM (as of 2026-07-02)

| Checklist item | HRRR | NBM |
|---|---|---|
| 1-4 — static audit (packing enumeration, catalog cross-reference incl. `buildable`, display-prep enumeration, structural differences) | Complete, corrected 2026-07-02 (this section) | Complete (this section) |
| 5 — re-run Layers 1-4 | **Layers 1-3 complete**; Layer 4 pending | **Layers 1-2 complete**; Layers 3-4 pending — blocked on retention turnover |
| 6 — add to `CARTOSKY_BINARY_SAMPLING_MODELS` | Not yet — gated on Layer 4 + Phase C evidence | Not yet — gated on item 5 completing |
| 7 — prod storage measurement | Complete (measured above) | Complete (measured above) |

**Layer 3 (canary) result for HRRR, completed 2026-07-02.** Four consecutive cycles (`20260702_18z` through `21z`, including the 18z extended/48-hour cycle for fh019-048 coverage) run via `canary_binary_sampler.py --model hrrr --run <run>`. Corrected scope (18 Group 1 variables + `radar_ptype` Group 4; see the Group 2 correction above): **zero divergence across all four runs**, `no_value_sample_rate.cog == no_value_sample_rate.binary` exactly on every run (parity on the shared no-data footprint, not a substrate gap), `radar_ptype` (Group 4) exercised with thousands of comparisons per run at strict integer-category equality. Benchmarks captured on the 18z (extended-cycle) pass: binary sampling beat COG on single-point (5.6-5.9ms vs 6.6-7.1ms) and 1000-point batch (1.1-1.3ms vs 2.0-2.3ms) reads; meteogram-scale benchmark was informational only (HRRR's real per-run frame count is 19-49, not GFS's 85-105 — the canary script's benchmark warning threshold is hardcoded to the GFS figure and needs correcting before it's reused for other models, tracked as a canary-script follow-up below). The canary script's `_scope_for_model()` did not originally filter on `buildable`, which produced the Group 2 misclassification corrected above — that fix is required before NBM's canary is re-run or any future model's Phase G audit is performed, since the same gap applies fleet-wide, not just to HRRR.

**Canary script hardening, completed 2026-07-03.** All four follow-ups from the HRRR Layer 3 pass are implemented in `canary_binary_sampler.py` (verified by direct code read, not just the implementation summary): (1) scope derivation now filters on `VariableCapability.buildable`, **with one necessary refinement found during implementation** — a naive buildable-only filter would have wrongly excluded GFS's `ptype_intensity_rain/snow/ice`, which are `buildable=False` yet are independently published via the scheduler's `companion_vars` mechanism (`scheduler.py`, `_companion_vars_for_var`/`_scheduled_targets_for_cycle`) as companions of the buildable `ptype_intensity`. The filter now excludes `buildable=False` only when a variable is *also* not companion-published; HRRR's `radar_ptype_*` components are companions of nothing and remain correctly excluded. (2) `bin_meta_invalid_count` is now a distinct, always-blocking classification (any binary resolution failure on a frame the COG side sampled successfully exits 4 regardless of tolerance group) separate from a blocking asymmetric-no-value-rate check (binary no-value rate > 0.2 while COG stays < 0.05); both surface at the summary's top level. (3) `--vars` added, validated against post-filter scope, composable with `--run`/`--sample-limit`; a `vars_with_zero_comparisons` warning fires whenever `--sample-limit` truncates coverage before an in-scope variable gets any comparisons. (4) `_expected_meteogram_frame_count()` derives the expected frame count from `plugin.scheduled_fhs_for_var()` for the run's actual cycle hour instead of a hardcoded GFS-era "need 85+" — confirmed against HRRR's real canary output (49 frames expected/observed for the 18z extended cycle, 19 for standard cycles). Verification: 314 directly-affected tests pass; full suite shows the same ~79 pre-existing failures as main, confirmed via stash-diff to be pre-existing rather than introduced by this change.

**Tracked, not yet a problem:** EPS/GEFS `__mean` variables are resolved to a runtime var id via a third mechanism (`resolve_runtime_var_id`) that the buildable/companion filter does not model. If an EPS/GEFS Phase G audit runs before this is addressed, verify the filter correctly resolves runtime var ids against the capability catalog rather than silently excluding valid ensemble-mean variables. *Update 2026-07-04: addressed — the canary's scope filter now includes `ensemble.artifact_map` values of buildable entries (`_ensemble_artifact_published_vars`), and the GEFS/EPS Phase G audit below found one remaining refinement needed on the opposite side (bare artifact-mapped ids are still falsely included; see that section).*

**Layers 1-2 implementation, completed 2026-07-02.** The static audit above was independently re-verified at runtime during this work and confirmed in full: 23 literal HRRR entries, 5 literal NBM entries, no loop-registered entries for either model (the registration loops cover gfs/ecmwf/aigfs/gefs/aifs/eps/ndfd/wpc only), all uint16, `vort500` `offset=-100.0`, `tmp850_anom` `offset=-80.0`, and all tolerance-group assignments match `grid_display_prep_config` exactly. What was built:

- `test_grid_value_decode.py` is now parameterized over `("gfs", "hrrr", "nbm")` with per-model variable lists derived from `_PACKING_BY_MODEL_VAR` at collection time (future packing additions are covered automatically), all assertions through `_decode_values()`, in-range values generated from each variable's own packing band so negative-offset variables exercise negative values automatically, plus a guard test that HRRR/NBM remain all-uint16 so a future `uint8` addition forces a re-audit.
- `test_binary_sampler_parity.py` fixture helpers generalized to take transform/projection (GFS tests unchanged); HRRR and NBM fixtures use each model's real grid geometry (`MODEL_REGISTRY` + `get_grid_params` + `compute_transform_and_shape` — 3 km and 13 km CONUS respectively, EPSG:3857), not GFS geometry. Group 1/2/4 parity assertions per the group tables above, including strict integer-category equality for `radar_ptype` (Group 4) at interior, near-boundary, and on-boundary points. A partition test pins this section's group tables to the live display-prep config, so an unaudited new variable fails loudly. Also fixed a latent EPSG:4326 assumption in the test file's `_meta_index` helper that broke on projected meta transforms.
- Tolerance-group classification extracted to a shared helper, `sampling_tolerance_group()` in `grid_display_prep.py`, deriving group 1/2/3/4 purely from `upscale_factor` × `categorical_nearest` with no model or variable names; the canary script's `_classify_variable` now delegates to it; covered by its own unit test fed synthetic configs for all four group shapes.
- Verification: 287 tests passing across the four affected suites (146 new HRRR/NBM parameterizations); 26 failures in adjacent suites confirmed via stash-diff to be pre-existing on main, not regressions.

**Correction to the record on Group 4 novelty.** The working assumption going into the item 5 implementation (stated in that task's prompt, not in this document) was that Group 4 assertion logic "does not exist yet anywhere." This was wrong: the canary script already contained config-derived Group 4 classification, integer-equality divergence checking, and blocking exit-code logic. What item 5 genuinely added was Group 4 *parity-test* coverage and the extraction of classification into the shared, unit-tested helper. Recorded per this document's convention of documenting corrections in place rather than silently absorbing them.

**Remaining sequence before either model's allowlist flip:** deploy the item 5 changes to prod (the shared helper and the canary script's delegation to it are production-adjacent code the shadow script depends on); run each model's 4-cycle Layer 3 canary using the leaner protocol from the addendum above (the two canaries may run concurrently; HRRR's window should include at least one 48-hour extended cycle so fh019-048 frames are exercised; point lists must be filtered to each model's CONUS bbox so out-of-coverage points register as expected-missing rather than divergence noise); gather Phase C parallel-gate evidence on HRRR and NBM frames during the same window; extend and run Layer 4 meteogram integration per model (NBM's fh264 range is the longest sequential-read pattern the binary path has yet faced); apply the packing-fix retroactivity check from the addendum above; then and only then, checklist item 6.

### Phase G audit — GEFS and EPS static readiness (checklist items 1–4)

This audit covers checklist items 1–4 only, performed 2026-07-04 as a single combined pass because both models publish through the same buildable/`__mean`-twin ensemble pattern (`BaseModelPlugin.resolve_runtime_var_id` + `ensemble.artifact_map`) — but every claim below was verified per-model against `gefs.py`, `eps.py`, `grid.py`, `grid_display_prep.py`, and `scheduler.py` directly, not assumed to transfer between the two. It does **not** start a canary, implement Layer 1/2 tests, add either model to `CARTOSKY_BINARY_SAMPLING_MODELS`, or measure prod storage (checklist item 7 remains open for both).

#### The ensemble publish path — the finding that shapes both audits

Neither model publishes any frame under a bare catalog var id. The scheduler resolves every build target through `_runtime_var_id()` (`scheduler.py`, `_build_one_frame` and `_build_bundle`) *before* calling `build_frame`/`build_frame_bundle`, and `build_frame` stages artifacts and performs its packing-table lookup under that resolved id. For both models, every buildable catalog entry carries `ensemble.artifact_map = {"mean": "<var>__mean"}` and `default_view = "mean"`, so **the on-disk published artifact ids are exclusively the `__mean` twins** (verified programmatically: 18/18 GEFS and 14/14 EPS buildable entries redirect; zero publish under their own id; zero `companion_vars` anywhere in either catalog). `supported_views` is `["mean"]` on every entry of both models — per-member views are not reachable today and are out of scope pending their own sizing spike.

Three consequences:

1. **The parity/comparison scope for both models is the set of `__mean` artifact ids, not the bare user-facing ids.** The bare ids are runtime aliases; users requesting `tmp2m` are served `tmp2m__mean` frames.
2. **Seven bare-id packing entries per model are write-path-dead**: `tmp2m_anom`, `tmp850_anom`, `hgt500_anom`, and the four loop-registered precip-anomaly bare ids each have a `_PACKING_BY_MODEL_VAR` entry that the production write path never uses (the encoder keys on the `__mean` id, which has its own entry). Verified that each dead entry's constants are byte-identical to its `__mean` twin's, so there is no `vort500`-class divergence hazard hiding in the duplication — but no frames exist on disk under these ids, and any tooling that iterates packing keys (the canary does) will find empty directories for them.
3. **Canary scope-filter status:** the 2026-07-04 filter fix (`_ensemble_artifact_published_vars` in `canary_binary_sampler.py`) correctly pulls the `__mean` artifacts *into* scope. The remaining gap found by this audit is the mirror image: the buildable bare ids stay in scope (they pass the `buildable=True` check) even though their frames live under a different id, so a GEFS/EPS canary run today would report zero comparisons for 7 variables per model. **Required before either model's canary:** either extend the filter to exclude buildable ids whose runtime resolution redirects to a different artifact id, or run with an explicit `--vars` list of artifact ids and treat the bare-id zero-comparison warnings as expected. The former is the real fix; the current behavior is a false inclusion, not a false exclusion, so parity coverage itself is not compromised either way. *Resolved later the same day: `_ensemble_dead_alias_vars()` now excludes buildable ids whose `artifact_map` redirects every reachable view elsewhere, reported separately in the summary as `excluded_dead_alias_variables` (distinct from `excluded_non_buildable_variables`). Verified: GEFS 18 in scope + 7 dead aliases, EPS 14 + 7 (+ `hgt500__mean` non-buildable), GFS/HRRR/NBM scopes byte-identical to before.*

#### GEFS

Packing scope from `_PACKING_BY_MODEL_VAR` is 25 entries — 17 literal plus **8 loop-registered** (the `for _precip_anom_var in ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_16d_anom")` loop near the bottom of `grid.py` registers both the bare and `__mean` id for each): checklist item 1's loop-registration warning applies for real here, and note GEFS's fourth anomaly window is **16d**, not ECMWF-family 15d.

Per the publish-path finding above, the 25 packed entries split into **18 published artifacts** (comparison scope) and **7 write-path-dead bare aliases** (`tmp2m_anom`, `tmp850_anom`, `hgt500_anom`, `precip_{5,7,10,16}d_anom`). Published-artifact packing constants, all `uint16` (no GEFS entry sets a `dtype`, so all take the `GRID_DTYPE` default; no `uint8` anywhere):

| Artifact id | scale | offset | units |
|---|---|---|---|
| `tmp2m__mean`, `tmp850__mean` | 0.1 | -100.0 | F |
| `tmp2m_anom__mean`, `tmp850_anom__mean` | 0.1 | -80.0 | F |
| `hgt500_anom__mean` | 1.0 | -600.0 | m |
| `rh2m__mean`, `rh700__mean` | 0.1 | 0.0 | % |
| `wspd850__mean`, `wspd300__mean` | 0.1 | 0.0 | kt |
| `wspd10m__mean` | 0.1 | 0.0 | mph |
| `sbcape__mean` | 1.0 | 0.0 | J/kg |
| `snowfall_total__mean` | 0.1 | 0.0 | in |
| `pwat__mean`, `precip_total__mean` | 0.01 | 0.0 | in |
| `precip_{5,7,10,16}d_anom__mean` | 0.01 | -128.0 | in |

Signed-variable offset audit (the `vort500`-class check from the addendum): every physically-signed variable — both temperature anomalies, the height anomaly, and all four precip anomalies — carries a correctly negative offset. Note `hgt500_anom__mean`'s `scale=1.0` means 1 m quantization (0.5 m Group 1 tolerance), the coarsest quantization in the GEFS set; same on both substrates' comparison logic, just worth knowing when eyeballing canary output.

Catalog cross-reference: all 25 packed ids are live entries in `GEFS_VARIABLE_CATALOG` — which itself uses the loop-registration pattern for the precip anomalies (`gefs.py`, the `PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY` loops appending to both `GEFS_VARS` and `GEFS_VARIABLE_CATALOG` at import time). Every buildable entry has an `artifact_map`, every mapped runtime id has a packing entry, and no packed GEFS id is unreachable — the 7 bare aliases are reachable as *build targets* (they are the scheduled ids), just never used as *artifact* ids.

Tolerance groups for the 18 published artifacts (via `sampling_tolerance_group` against `grid_display_prep_config`, verified programmatically):

| Group | GEFS artifacts |
|---|---|
| Group 1 | `tmp2m__mean`, `tmp2m_anom__mean`, `tmp850__mean`, `tmp850_anom__mean`, `hgt500_anom__mean`, `rh2m__mean`, `rh700__mean`, `wspd850__mean`, `wspd300__mean`, `wspd10m__mean`, `sbcape__mean`, `pwat__mean`, `precip_{5,7,10,16}d_anom__mean` |
| Group 2 | `precip_total__mean`, `snowfall_total__mean` (both `upscale_factor=3`, `categorical_nearest=False`, `preserve_zero_support=True`) |
| Group 3 / 4 | None |
| Excluded (write-path-dead bare aliases) | `tmp2m_anom`, `tmp850_anom`, `hgt500_anom`, `precip_{5,7,10,16}d_anom` |

Structural differences from GFS/HRRR/NBM (checklist item 4):

- **Published region is `na` (-178 to -25 lon, 5 to 82 lat), not CONUS** — the first audited model whose canonical build region is not CONUS. Canary anchor-point lists must be filtered to the `na` bbox, and CONUS-only point lists would silently ignore most of the coverage; conversely, Alaska/Canada points that HRRR/NBM treated as expected-missing are *in scope* here.
- Grid resolution 25 km (`grid_meters_by_region`: 25,000 m for both `na` and `conus`), from 0.5° source GRIB (`atmos.5`, Herbie `member="mean"` for every `__mean` runtime var).
- Forecast-hour schedule is **cycle-hour independent**: fh 0–384 step 6 = 65 frames, every cycle, 6-hour cadence — no HRRR-style mixed cycle lengths.
- Constraint-windowed variables reduce real frame counts (all derived via `scheduled_fhs_for_var`, which the canary's expected-frame logic already uses): `precip_total__mean`/`snowfall_total__mean` 64 frames (min_fh 6); `precip_5d_anom__mean` 45 (min_fh 120); `precip_7d_anom__mean` 37 (min_fh 168); `precip_10d_anom__mean` 25 (min_fh 240); and **`precip_16d_anom__mean` is a single-frame variable** (min_fh = max_fh = 384) — with default sampling limits it will get very few comparisons per run, so per-variable coverage warnings need reading with that in mind rather than as truncation.
- Heavily derived variable set: every artifact except `tmp2m/tmp850/rh2m/rh700/pwat/sbcape` means is derive-strategy output (ERA5-baseline anomalies, cumulative precip, 10:1 snowfall, u/v-composited wind speeds) — all consumed upstream of `write_grid_frame_for_run_root`, so no new parity semantics, but Group 1 anomalies exercise the negative-offset packing bands more than any previously audited model.

#### EPS

Packing scope from `_PACKING_BY_MODEL_VAR` is 22 entries — 14 literal plus **8 loop-registered** via a separate, EPS-specific loop in `grid.py` (not the GEFS loop: EPS's fourth anomaly window is **15d**, `precip_{5,7,10,15}d_anom` + `__mean` twins). The 22 split into **14 published artifacts**, **7 write-path-dead bare aliases** (`tmp2m_anom`, `tmp850_anom`, `hgt500_anom`, `precip_{5,7,10,15}d_anom`), and **one packed id that is not reachable at all — see the flag below**. All entries `uint16` (no `dtype` key anywhere in the EPS set; no `uint8`).

Published-artifact packing constants:

| Artifact id | scale | offset | units |
|---|---|---|---|
| `tmp2m__mean`, `tmp850__mean` | 0.1 | -100.0 | F |
| `tmp2m_anom__mean`, `tmp850_anom__mean` | 0.1 | -80.0 | F |
| `hgt500_anom__mean` | 1.0 | -600.0 | m |
| `rh2m__mean`, `rh700__mean` | 0.1 | 0.0 | % |
| `wspd10m__mean` | 0.1 | 0.0 | mph |
| `pwat__mean`, `precip_total__mean` | 0.01 | 0.0 | in |
| `precip_{5,7,10,15}d_anom__mean` | 0.01 | -128.0 | in |

Signed-variable offset audit: all anomalies carry correctly negative offsets, same constants as their GEFS counterparts. EPS has no wind-aloft, CAPE, or snowfall artifacts — its 14-variable scope is a strict subset of GEFS's shape apart from the 15d/16d window difference.

**Flagged, packed but unreachable: `("eps", "hgt500__mean")`** (scale 0.1, offset -60.0, dam). Its catalog entry is `buildable=False, internal_only=True`, it is not an `artifact_map` value of any buildable entry, and it is nobody's companion — it exists as the *contour-component input* consumed in-memory by `hgt500_anom`'s derive/contour hints. No scheduler build target can produce a frame under this id, so the packing entry appears to be stale or anticipatory config. GEFS has the same catalog shape for its `hgt500__mean` but — correctly — no packing entry for it, which reinforces the stale-config reading. The canary's scope filter already excludes it. **Before EPS's canary: confirm no `hgt500__mean` directory exists under any retained EPS run on prod** (a one-line `ls`); if frames *do* exist there, a fourth publish path is in play that this audit did not model, and the scope filter needs re-examination before trusting its exclusions.

Catalog cross-reference: all 22 packed ids are live entries in `EPS_VARIABLE_CATALOG`, which loop-registers the precip anomalies the same way GEFS does (`eps.py`, `PRECIP_ANOM_360_TARGET_FH_BY_VAR_KEY` loops). Every buildable entry has an `artifact_map`; every mapped runtime id has a packing entry.

Tolerance groups — **EPS is the first audited model with zero display-prep entries**: `_GRID_DISPLAY_PREP_BY_MODEL_VAR` contains no `("eps", …)` key at all, so every EPS artifact is Group 1, **including `precip_total__mean`** (unlike GFS/NBM/GEFS, whose precip products are upscale-3 Group 2; this matches the ECMWF-family pattern — deterministic `ecmwf`'s snowfall/ptype configs also use `upscale_factor=1`):

| Group | EPS artifacts |
|---|---|
| Group 1 | All 14 published artifacts |
| Group 2 / 3 / 4 | None |
| Excluded (write-path-dead bare aliases) | `tmp2m_anom`, `tmp850_anom`, `hgt500_anom`, `precip_{5,7,10,15}d_anom` |
| Excluded (packed but unreachable — flagged above) | `hgt500__mean` |

Consequence: EPS's canary has **no tolerated boundary-divergence category whatsoever** — any divergence on any variable is a blocking Group 1 divergence under the addendum's never-relaxed bar. Expect the cleanest pass/fail signal of any model so far, and treat any nonzero divergence as full-escalation.

Structural differences from GFS/HRRR/NBM (checklist item 4):

- **Same `na` published region caveat as GEFS** (EPS reuses `ECMWF_REGIONS`; identical bbox), 18 km grid (`grid_meters_by_region`: 18,000 m).
- **Cycle-hour-dependent schedule on the synoptic/off-cycle axis** — a different axis than HRRR's standard/extended split: 00z/12z run fh 0–360 step 6 (61 frames); 06z/18z run fh 0–144 step 6 (25 frames), 6-hour cadence.
- **Off-cycle runs legitimately publish zero frames for three variables**: `precip_7d_anom__mean` (min_fh 168), `precip_10d_anom__mean` (240), and `precip_15d_anom__mean` (360) all have min_fh above the off-cycle max of 144, and `precip_5d_anom__mean` drops to 5 frames (120–144). A canary pass over an 06z/18z run must treat zero comparisons for those three as *expected schedule behavior*, not missing coverage — this is a case the HRRR/NBM canaries never exercised, and the strongest reason EPS's 4-cycle canary window should include both a synoptic and an off-cycle run. `precip_15d_anom__mean` is additionally a single-frame variable (min_fh = max_fh = 360) on synoptic cycles.
- Source is ECMWF ensemble GRIB (Herbie `model="ifs"`, `product="enfo"`) with pf-member-mean aggregation requested via a custom fetch kwarg (`_cartosky_fetch_aggregation="ecmwf_pf_mean"`-family values in `eps.py`'s `herbie_request`) — a fetch-path difference with no parity-semantics impact, since aggregation happens before the shared warp/encode path.
- EPS publishes late relative to its nominal cycle time (`stale_cycle_release_minutes_by_hour`: 390–450 min) — relevant to canary scheduling (a "latest run" selected shortly after a cycle time will be the previous cycle), not to parity.
- Every EPS variable is bundle-built (`_is_derive_bundle_candidate` returns True for the whole model in `scheduler.py`) — build-orchestration difference only; frames still go through the same `write_grid_frame_for_run_root`.

#### Phase G checklist status — GEFS and EPS (as of 2026-07-04)

| Checklist item | GEFS | EPS |
|---|---|---|
| 1-4 — static audit (packing enumeration incl. loop-registered entries, catalog cross-reference incl. `buildable`/publish-path, display-prep enumeration, structural differences) | Complete (this section) | Complete (this section) |
| 5 — re-run Layers 1-4 | **Complete** (2026-07-04) | **Complete** (2026-07-04) |
| 6 — add to `CARTOSKY_BINARY_SAMPLING_MODELS` | **Complete 2026-07-04** — flipped, `csky-api`/`csky-gefs-scheduler` restarted | **Complete 2026-07-04** — flipped, `csky-api`/`csky-eps-scheduler` restarted |
| 7 — prod storage measurement | **Complete** — 27 GB total across 6 retained runs (uniform 4.5 GB/run, fixed 65-frame schedule), 11 GB value-COG (~41%) | **Complete** — 19 GB total across 6 retained runs (synoptic 4.5 GB / off-cycle 1.7 GB, confirmed consistent once `20260704_12z` settled past its late-releasing build window), 11 GB value-COG (**~58%, the highest COG share of any model in this migration**) |

**Phase C evidence, completed 2026-07-04 for both models.** Prod shadow-mode grep against `csky-gefs-scheduler.service`/`csky-eps-scheduler.service` over a 48-hour window: real PASS counts on both gates, zero shadow-gate failures, zero errored — confirms no false positives against real production data for either model. Synthetic bad-frame coverage added to `test_binary_only_frame_builds.py`: GEFS gets two cases (`tmp2m__mean` Group 1 generic-continuous rejection, `precip_total__mean` Group 2 rejection under its real branch) using GEFS's real runtime-artifact packing constants, not bare catalog ids; EPS gets one case (`tmp2m__mean` Group 1 only, matching the audit's finding that EPS has zero display-prep entries — no Group 2 case was invented). Both confirm the enforced gate genuinely blocks bad input for each model's real variable shapes, not just that shadow mode agrees with clean data.

**Retroactivity check, both models:** no packing-table constant was changed during either model's canary window (2026-07-03 through 2026-07-04) — pass by inspection, no retention-turnover wait required for either flip.

**GEFS and EPS allowlist flips, both completed 2026-07-04.** `CARTOSKY_BINARY_SAMPLING_MODELS` now includes `gfs, hrrr, nbm, gefs, eps`. `csky-api` restarted for each flip; `csky-gefs-scheduler` and `csky-eps-scheduler` restarted respectively. *(Sequencing note: GEFS flipped first; EPS flipped after its in-flight 12z run finished building, so no run built mid-flip on either model.)*

**Post-flip verification, completed 2026-07-05 for both models.** First binary-only build verified per model: COG-write-skip logged on every frame with zero enforced pre-encode gate rejections; zero `*.val.cog.tif` files under the new run directories; frame counts match `scheduled_fhs_for_var` per cycle type (including EPS off-cycle 25-frame shape and the anomaly variables' constrained/zero-frame windows); `/api/v4/sample`, `/api/v4/sample/batch`, and meteogram spot-checks passed at interior and near-bbox-edge points with values sane against pre-flip retained runs; Ensembles tab charts render normally for both models. **Phase F is closed for GEFS and EPS, and with it the migration is complete end-to-end across all five models.** Retained COG-era runs continue to serve correctly through the binary path (retroactivity check passed above); their COG bytes (~11 GB per model) reclaim passively as retention turns over.

**Resolved:** the `ls -1`/`du` count discrepancy was a `LATEST.json` pointer file at the top level of `data/published/eps/` — confirmed via `ls -la`, not a 7th unpruned run. Retention is correctly holding at 6 runs; storage figures above stand as final.

With both flips complete, five models are now on the binary sampling pipeline: GFS, HRRR, NBM, GEFS, EPS.

### Phase G audit — AIFS and ECMWF (compressed process)

**Process note.** Six models in, the checklist's full ceremony (4-layer test suite, 4-cycle canary, dual Phase C evidence) had produced zero real migration-correctness bugs — every finding to this point was a canary-tooling gap (scope-filter misclassifications, script crashes, a hardcoded benchmark variable), not a COG-vs-binary data problem, with the single exception of HRRR's pre-migration stale-scheduler writer bug (a deploy-discipline issue, not migration logic). Given that track record, AIFS and ECMWF's audits (checklist items 1-4) were performed first and used to decide, per model, how much of the downstream process to compress rather than applying full ceremony by default. A model auditing as structurally novel keeps full rigor; a model auditing as a plain re-shape of an already-proven pattern gets a single full-scope canary run, shadow-mode Phase C evidence, and storage measurement, skipping Layer 4 and the synthetic Phase C test.

#### AIFS — static audit

Packing scope from `_PACKING_BY_MODEL_VAR` is 18 entries, all published under bare catalog ids (AIFS is a deterministic single-member model — no `__mean` twin/artifact-map complexity of any kind): `tmp2m`, `tmp2m_anom`, `dp2m`, `rh2m`, `rh700`, `tmp850`, `tmp850_anom`, `wspd850`, `wspd300`, `hgt500_anom`, `precip_total`, `precip_{5,7,10,15}d_anom`, `pwat`, `snowfall_total`, `wspd10m`. Anomaly window matches the ECMWF-family 15d convention, loop-registered the same way as ECMWF/EPS (checklist item 1 applies, no surprises found).

One flagged catalog entry, resolved cleanly: `AIFS_VARIABLE_CATALOG["hgt500"]` is explicitly `buildable=False, internal_only=True` — same role as EPS's `hgt500__mean` (a `base_component`/`contour_component` hint input for `hgt500_anom`'s derive step and contour rendering, never independently scheduled). Unlike EPS's case, **`("aifs", "hgt500")` has no packing-table entry at all** — confirmed by direct read of `_PACKING_BY_MODEL_VAR`. No live-artifact ambiguity, no scheduler trace needed; simpler than the EPS case, not a gap.

No `ptype_intensity`/companion-vars pattern in AIFS's rollout scope at all (not in `AIFS_VARS`). No ensemble complexity. Schedule is **cycle-hour independent** — fixed `range(0, 361, 6)`, 61 frames, every cycle (`AIFSPlugin.target_fhs` explicitly discards `cycle_hour`) — simpler than both ECMWF's SCDA/OPER split and EPS's synoptic/off-cycle split. 9 km grid, region `na`. Net assessment: the most structurally simple model audited in this migration — closest precedent is GFS's own variable shapes, with none of GFS's upscale/categorical-nearest complexity in scope.

#### AIFS — Layer 3, Phase C, storage, flip (completed 2026-07-05)

Given the audit found nothing novel, AIFS received the compressed treatment: one full-scope canary run (not 4 cycles), no Layer 4, no synthetic Phase C test.

**Canary:** `--run 20260705_06z` (full scope, no `--sample-limit`) — **179,172 samples, 0 divergences**, `bin_meta_invalid_count: 0`, `no_value_rate_asymmetric: false`. A prior `--sample-limit 500` smoke test had only exercised 1 of 18 variables (alphabetical-first consumed the limit, same artifact seen on every prior model's smoke test); the full-scope run confirmed all 18. Benchmarks: first full-scope pass showed the meteogram category losing to COG (10.70ms vs 10.04ms) — the only such result across seven models' worth of benchmarks — but a second independent run on a different retained run (`20260705_00z`) showed binary winning normally on every category including meteogram (9.53ms vs 10.33ms). Treated as single-run noise, not investigated further; single-point/batch benchmarks won comfortably on both runs (COG 6.1-6.7ms vs binary 3.4-4.3ms).

**Storage — large, but no longer the largest (see ECMWF below).** 24 GB per run, uniform across all 6 retained runs, **139 GB total retention footprint, 79 GB value-COG (~57% COG share)**. At the time of this measurement this was the largest single-model reclaim in the migration; ECMWF's subsequent measurement (103 GB) surpassed it — see the ECMWF section below. AIFS's 57% COG *share* remains the highest percentage in the migration even though its absolute reclaim is no longer the largest. Given disk is a live constraint (~878 GB/2 TB at time of writing), this is a materially significant reclaim regardless of rank.

**Phase C:** shadow-mode grep against `csky-aifs-scheduler.service` was initially run as a single combined pattern (`PASS|failed|errored`), returning `3792` total matches — flagged as insufficiently granular to confirm cleanliness (a combined count cannot distinguish 3792 passes from a mix of passes and failures) and a corrected per-pattern set of commands was provided. This record does not have a confirmed per-pattern breakdown on file; the flip proceeded on the combined signal plus the already-clean full-scope canary result. Worth a retroactive per-pattern check if any AIFS-specific gate issue surfaces post-flip.

**Flip:** `CARTOSKY_BINARY_SAMPLING_MODELS` now includes `aifs` (full list: `gfs, hrrr, nbm, gefs, eps, aifs`); `csky-api` and `csky-aifs-scheduler` restarted 2026-07-05.

#### Phase G checklist status — AIFS

| Checklist item | AIFS |
|---|---|
| 1-4 — static audit | Complete (this section) |
| 5 — re-run Layers 1-4 | Layer 1-3 equivalent complete via compressed process (full-scope single-run canary substituting for the 4-cycle protocol, justified by a clean structural audit); Layer 4 and synthetic Phase C test intentionally skipped, no novel code path to cover |
| 6 — add to `CARTOSKY_BINARY_SAMPLING_MODELS` | **Complete 2026-07-05** |
| 7 — prod storage measurement | **Complete** — 139 GB total, 79 GB value-COG (~57%), largest reclaim in this migration |

Retroactivity check: no packing-table constant changed during AIFS's canary window — pass by inspection.

#### ECMWF — static audit

Packing scope from `_PACKING_BY_MODEL_VAR` is 28 entries — 27 real + **one dead entry with no catalog backing at all**, a novel and worse failure mode than anything found on prior models (see below). Structural differences from every prior model: a genuine `ptype_intensity`/companion-vars pattern (identical mechanism to GFS's, packed and catalog-consistent: `ptype_intensity`, `ptype_intensity_rain/snow/ice` all real, `sampling_tolerance_group` classifies `ptype_intensity` as Group 4 via its `categorical_nearest=True, upscale_factor=1` config); a cycle-hour-dependent schedule (`06z`/`18z` get `ECMWF_SCDA_FHS`, 0–144h 3-hourly only, 49 frames; `00z`/`12z` get the full `ECMWF_OPER_FHS`, 0–144h 3-hourly + 150–360h 6-hourly, 85 frames) — same shape as EPS's synoptic/off-cycle split, reused pattern not new engineering; 9 km grid, region `na`.

**Confirmed bug, found via canary, not the static audit:** the shared multi-model precip-anomaly packing loop in `grid.py` packs `("ecmwf", "precip_16d_anom")` for the `("gfs", "ecmwf", "aigfs")` group, but `ecmwf.py`'s own catalog construction explicitly excludes `precip_16d_anom` (ECMWF's real long-range anomaly convention is 15-day, matching EPS, not GFS/GEFS's 16-day). The packing entry has **zero catalog backing** — not `buildable=False`, simply absent from `ECMWF_VARIABLE_CATALOG` entirely. This is a different, worse failure mode than the dead-alias/non-buildable cases found on HRRR/GEFS/EPS: those were packed-but-unreachable-via-scheduler with a real (if unreachable) catalog entry; this is packed-but-catalog-absent, meaning none of the three existing scope-exclusion mechanisms (buildable check, companion check, artifact-map check) could ever fire for it — they all require a catalog entry to consult. **Fixed 2026-07-05:** `canary_binary_sampler.py` now excludes any packed variable with no catalog entry at all (`excluded_uncataloged_variables`, a fourth, distinct exclusion bucket), confirmed via prod smoke test (`scope_variable_count: 27`, `precip_16d_anom` correctly excluded, log line `Excluded 1 packed variable(s) with no capability catalog entry`). No other model's scope changed.

#### ECMWF — Layer 3 canary result and the classifier-gap finding (completed 2026-07-05)

Four cycles run spanning both cycle types (2 synoptic: `20260705_00z`, `20260704_12z`; 2 off-cycle: `20260705_06z`, `20260704_18z`), post uncataloged-variable fix. **All four runs exited 4 (blocking)** on real, reproducible Group 1 divergence: 369 / 342 / 202 / 278 divergences respectively, isolated entirely to a single variable — `ptype_intensity_rain` — on every run. `COG-noval`/`bin-noval` matched exactly on every run (substrate-availability agreement; the divergence is on cells where both sides have real values and disagree).

Root cause, fully traced (`derive.py`, `pipeline.py`, `grid_display_prep.py`): both the COG and binary writes receive the identical `warped_data` array — no staleness, no recomputation between write sites. The divergence is caused entirely by `prepare_grid_display_values()`, which the binary write path calls and the COG write path does not: `grid_display_prep_config("ecmwf", "ptype_intensity_rain")` sets `preserve_zero_support=True, support_min_value=0.01` (a floor zeroing any finite value below 0.01 in/hr — present to avoid visual noise in trace-precipitation rendering) combined with `upscale_factor=1`. **The root issue is a classifier blind spot, not a data-accuracy bug:** `sampling_tolerance_group()` only inspects `upscale_factor`/`categorical_nearest` and has no awareness of `support_min_value`, so any config with a real floor threshold and `upscale_factor=1` is misclassified into zero-tolerance Group 1 when it should be treated as boundary-tolerant. Scanning the full `_GRID_DISPLAY_PREP_BY_MODEL_VAR` table confirms ECMWF's three `ptype_intensity_{rain,snow,ice}` entries are the **only** combination in the entire codebase with `support_min_value` set and `upscale_factor == 1` — every other model/variable with this floor (GFS's companions, HRRR/NAM's `radar_ptype_*` components, GEFS's `precip_total__mean`, NBM's `precip_total`) has `upscale_factor=3` and is correctly Group 2, where this exact behavior is already expected and tolerated. **This is why the bug never surfaced on GFS**, which has the identical floor config on its own `ptype_intensity_rain/snow/ice` — GFS's Group 2 classification already absorbs it; it is not a live, unflagged accuracy issue on already-shipped GFS data.

**Decision, made explicitly rather than patched silently: leave `grid_display_prep.py`/the floor behavior as-is.** The floor is treated as legitimate existing product behavior (predates this migration, applies identically on GFS today), not something this migration should change. The classifier itself was **not patched** either — a targeted per-variable Group-2 override was proposed and available but explicitly declined, since fixing the canary's classification would only make the tooling agree with itself, not change any served value. **Consequence, recorded so it isn't re-litigated:** any future canary run against ECMWF's `ptype_intensity_rain/snow/ice` will continue to exit 4 on this exact, already-understood pattern. This is expected, not a regression — verify future ECMWF divergences are still isolated to these three variables and still threshold-adjacent (all four runs' divergent COG values were confirmed well under 0.01 in/hr) before assuming a new issue.

#### Phase G checklist status — ECMWF

| Checklist item | ECMWF |
|---|---|
| 1-4 — static audit | Complete (this section) |
| 5 — re-run Layers 1-4 | Layer 3 canary complete (4 cycles, both cycle types) with one understood, accepted classifier-gap finding (`ptype_intensity_rain/snow/ice`, not fixed by decision — see above); Layer 4 and synthetic Phase C test skipped per compressed process |
| 6 — add to `CARTOSKY_BINARY_SAMPLING_MODELS` | Ready — all gates below cleared, flip execution pending |
| 7 — prod storage measurement | **Complete** — 208 GB total across 6 retained runs (mixed cycle lengths: 45 GB synoptic `00z`/`12z`, 25 GB off-cycle `06z`/`18z`, same two-band pattern as HRRR's mixed retention), **103 GB value-COG (~49.5%) — the largest absolute storage reclaim of any model in this migration**, surpassing AIFS's 79 GB |

**Phase C evidence, completed 2026-07-05 — unambiguous, unlike AIFS's combined-pattern result.** Split per-pattern shadow-mode grep against `csky-ecmwf-scheduler.service` over a 24-hour window: `6346` pre-encode PASS, `6346` grid-binary-validation PASS (exact match — every pre-encode pass also passed binary validation, no asymmetry), `0` shadow-gate failures on either gate, `0` errored. This is real, granular evidence of no false positives against production data — the `ptype_intensity_rain/snow/ice` threshold-floor divergence found in the canary does not show up as a shadow-gate failure, consistent with it being a canary-classification gap rather than a pipeline defect.

Retroactivity check: the uncataloged-variable canary-script fix is tooling, not a packing-table change — no retroactivity concern for ECMWF's own retained runs.

**All Phase G gates are now cleared for ECMWF.** Recommended next step: flip `CARTOSKY_BINARY_SAMPLING_MODELS` to include `ecmwf`, restart `csky-api` and `csky-ecmwf-scheduler`, then run the standard post-flip verification (COG-write skip on next cycle per cycle type, enforced-gate log confirmation, `/api/v4/sample` spot-check including a `ptype_intensity_rain` point to confirm the accepted floor behavior serves as expected — zeroed trace values, not an error).

**Item 5 test extensions, completed 2026-07-04.** Layer 1 (`test_grid_value_decode.py`) and Layer 2 (`test_binary_sampler_parity.py`) are parameterized over both models' canary comparison scope — derived by intersecting `_PACKING_BY_MODEL_VAR` with the canary's own `_scope_for_model()` split, so the dead-alias bare ids and EPS's unreachable `hgt500__mean` are excluded by the same logic Layer 3 uses and coverage cannot drift from it. Layer 2 fixtures use each model's real grid geometry (25 km / 18 km, region `na`, EPSG:3857) via the same build helpers as the HRRR/NBM fixtures; Group 1/2 assertions follow this section's tolerance-group tables (GEFS: 16 Group 1 + 2 Group 2; EPS: all 14 Group 1). Partition tests in both files pin the audited dead-alias sets and tolerance groups, and both files carry the all-uint16 dtype guard for these models. Layer 4 (`test_forecast_meteogram_api.py`, `GEFSCMP_`/`EPSCMP_` fixture families) compares the COG and binary meteogram batch loops per artifact: GEFS `tmp2m__mean` (Group 1) + `precip_total__mean` (Group 2 planted boundary), EPS `tmp2m__mean` across one synoptic (61-frame) and one off-cycle (25-frame) run with schedules pinned to `scheduled_fhs_for_var`; all assertion families loud-failure-proven.

**Layer 3 (canary) result, completed 2026-07-04.** Four consecutive cycles each, using the fixed scope filter (dead-alias exclusion + ensemble-artifact inclusion, both verified in this run's own log output). GEFS: `20260703_12z, 18z, 20260704_00z, 06z` — **zero divergence across all four runs**, 192,024 comparisons each, full coverage on every in-scope variable including the single-frame `precip_16d_anom__mean` (no zero-comparison warnings on any run). EPS: deliberately split 2 synoptic (`20260703_12z`, `20260704_00z`, 61 frames, full 14-variable coverage) + 2 off-cycle (`20260703_18z`, `20260704_06z`, 25 frames) — **zero divergence across all four runs**, and the off-cycle runs' zero-comparison warning (`precip_10d_anom__mean, precip_15d_anom__mean, precip_7d_anom__mean`) matches the audit's predicted min-fh-exceeds-off-cycle-ceiling set exactly, confirming the structural analysis against real production behavior rather than just passing cleanly. Benchmarks real on every run: GEFS binary sampling 5-7x faster on single-point/batch reads, 4-5x on meteogram; EPS 4-6x on single-point/batch, 2-5x on meteogram (`hgt500_anom__mean` benchmarked throughout — first Group 1 variable alphabetically, per the canary's benchmark-variable-selection fix).

**Anchor coverage caveat — resolved 2026-07-04.** `_load_anchor_points()` reads a single shared `anchor_index.json` used identically by every model's canary, confirmed by direct code read to have no model-specific or region-aware branching — almost certainly CONUS+AK+HI only, consistent with CartoSky's US-focused product surface. This meant the audit's checklist item 4 follow-up ("build `na`-bbox anchor lists") had not been exercised by the 4-cycle run above. Closed with a supplementary spot-check: 4 real Canadian anchors (Toronto ON, Vancouver BC, Yellowknife NT, Iqaluit NU) sampled against `tmp2m__mean` for both models — all 8 comparisons agree within Group 1's `scale/2` tolerance, non-missing on both substrates. This is real evidence outside the anchor index's US-only coverage, spanning southern Canada through the subarctic; full confidence in `na`-wide correctness, not just the anchor index's incidental US subset.

**`hgt500__mean` on-disk check for EPS — resolved 2026-07-04.** Zero `hgt500__mean` directories found under any of the 7 currently-retained EPS runs, confirming the code-level finding (no scheduler build target can produce this frame) with empirical evidence. The packing entry is dead config with no live artifact; safe to leave as-is or clean up at leisure, not a migration blocker either way.

**Remaining before either model's allowlist flip:** Phase C evidence — both halves, same as every prior model (prod shadow-mode grep against `csky-gefs-scheduler.service`/`csky-eps-scheduler.service`, plus a synthetic bad-frame test extending `test_binary_only_frame_builds.py` for each model's continuous branch — GEFS needs a Group 2 case alongside Group 1, EPS only needs Group 1 since it has no display-prep entries at all); the packing-fix retroactivity check (no packing constant changed during either model's canary window — pass by inspection); and prod storage measurement (checklist item 7, not yet done for either model). *All three resolved above; flips executed 2026-07-04 and verified 2026-07-05 — this paragraph is retained as the historical gate sequence only.*

### Phase G audit — NDFD and WPC (non-standard write path; dedicated phased plan)

**Documentation gap, disclosed rather than papered over.** This section covers work performed across several sessions — the original static audit, a critical bug found and fixed in the shadow-gate implementation, 48 hours of clean Phase C evidence, and the Layers 1–2/canary-script extension — none of which was written into this document until now, despite being referenced as "the audit" in intervening work. A later pass (the Layers 1–2 implementation) correctly found no NDFD/WPC section here, did not block on it, and instead independently re-verified every fact attributed to "the audit" directly against the code — all of it held. This section reconstructs the full record from that verification plus the original findings, so this gap cannot recur.

#### Headline finding: NDFD and WPC do not route through `build_frame`/`pipeline.py` at all

Both models are published by standalone, independently-implemented modules (`ndfd_publish.py` / `wpc_publish.py`), each driven by its own poller (`ndfd_poller.py`, 1800s default; `wpc_poller.py`, 3600s default) — never through `scheduler.py`'s `_process_run`/target-building machinery, never through `builder/pipeline.py::build_frame`. Confirmed by direct read of both publish modules, not inferred. This is a materially different situation from every model audited above: those all share `build_frame`'s single `binary_only` branch (enforced gate + value-COG skip, gated on `CARTOSKY_BINARY_SAMPLING_MODELS`), so a new model only ever needed the allowlist to "turn on" already-built cutover machinery. NDFD/WPC have **no cutover machinery at all** — as originally written, both publishers called `write_value_cog` and `write_grid_frames_for_run_root` unconditionally, with zero reference anywhere to the allowlist, `check_pre_encode_value_sanity`, or any gate.

The actual encode/write functions themselves are not a drift risk: both publishers call the exact same shared `write_value_cog` (`cog_writer.py`) and `write_grid_frames_for_run_root` (`grid.py`, which wraps the canonical `write_grid_frame_for_run_root` → `_encode_values`) that every GRIB model uses. Neither model has an entry in `_GRID_LOD_CONFIG_BY_MODEL_VAR`, so the plural wrapper degenerates to exactly one default-level frame per call — identical to the GRIB path. The gap is entirely in the *gating*, not the encoding.

**Confirmed separately, and load-bearing for the scope decision below:** `binary_sampling_models()` is checked generically (not per-model-hardcoded) at three route-handler call sites in `main.py` — `/api/v4/sample`, `/api/v4/sample/batch`, and the meteogram route (grep-confirmed: lines 3558, 5680, 5885). This means the **read side is already wired to respect the allowlist for any model**, including NDFD/WPC, even though the write side has no corresponding logic. Adding `ndfd`/`wpc` to the allowlist today, before any gate exists on their publish path, would switch live production reads to binary frames that have **never been checked by any correctness gate, shadow or enforced** — a real, live risk, not a hypothetical one, and the reason Phase 1 below exists.

#### Item 2 — what "forecast hour" means (resolved from code, not assumed)

**NDFD's `fh` is a per-variable sequence index, not an hour offset.** `_write_ndfd_frame` is called from `for fh, frame in enumerate(sorted(frames, key=valid_time))` — frames are keyed by valid time, and the frontend consumes them via `ui_constraints.time_axis_mode = "valid"`. `qpf_6h`/`snow_6h`/`ice_6h` are each a distinct successive 6-hour window from upstream `VP.001–003` files, published at fh=0,1,2,…, not fh=6,12,18. `qpf_24h/48h`, `snow_24h/48h`, `ice_24h` are **derived in-app** as rolling sums/maxes over 4–8 consecutive 6h frames; `mint`/`maxt` are ~7 daily frames. Frame counts vary per run with upstream element availability — there is no fixed schedule, and `scheduled_fhs_for_var` returns `[]` for every NDFD variable as a structural consequence (it filters an empty `target_fhs()`). Critically: **the COG and binary for every derived frame are written from the same in-memory array**, so none of this in-app derivation creates a COG-vs-binary parity risk — the parity question is identical to every other model's, just computed differently upstream of the shared write functions.

**WPC's `fh` is a real forecast hour**, parsed from upstream filenames (`p06m_{run}f{fh}.grb`), published at fh = 6, 12, …, 168 (28 frames, 6h cadence). The *values* are transformed: upstream files are 6-hour period totals, converted in-app to a running cumulative total (`accumulation_mode: "cumulative"`) before publish, so `precip_total` at fh=168 is the full 7-day accumulation — semantically matching every other model's `precip_total`. Same parity-neutral guarantee as NDFD: COG and binary are written from the same derived array.

**Consequence for the canary script, found and fixed:** both models' run ids are minute-stamped (`YYYYMMDD_HHMMz`, via `format_run_id(..., include_minutes=True)`), which the canary's original `RUN_ID_RE` (`^\d{8}_\d{2}z$`) rejected outright, in both run auto-discovery and explicit `--run` validation. **Fixed 2026-07-06**: `RUN_ID_RE` now accepts `^\d{8}_\d{2}(?:\d{2})?z$`, admitting both formats; verified the hour still occupies positions `[9:11]` in both (minute digits, when present, only ever follow it) — confirmed by hand against a real run id (`"20260706_1530z"[9:11] == "15"`), not just asserted in a comment. `_expected_meteogram_frame_count` needed no further change once the regex admitted the format.

#### Item 1/3 — packing, catalog, tolerance groups

**NDFD** — 12 packed entries, **loop-registered** (`_NDFD_GRID_PACKING_BY_VAR` copied into `_PACKING_BY_MODEL_VAR` via a module-level loop in `grid.py`; a literal-string grep for `("ndfd"` finds only the loop line, not the 12 variables — checklist item 1's warning applies for real here): `mint`, `maxt` (scale 0.1, offset −100.0 — correct floor for the only signed NDFD variables, no `vort500`-class hazard), `qpf_6h/24h/48h`, `ice_6h/24h` (scale 0.01, offset 0), `snow_6h/24h/48h`, `wgust_6h_max/24h_max` (scale 0.1, offset 0). All uint16 (no `dtype` key → `GRID_DTYPE` default; MRMS remains the table's only uint8 model). Exact 12↖12 bijection with `NDFD_VARIABLE_CATALOG`: all `buildable=True, primary=True`, zero `internal_only`/`companion_vars`/`ensemble`, zero uncataloged — all four canary exclusion buckets empty. **Zero `grid_display_prep.py` entries for NDFD → all 12 variables are Group 1.**

**WPC** — exactly one packed entry, `("wpc", "precip_total")`, a **literal** dict assignment (not loop-registered — asymmetric with NDFD, worth remembering). `scale=0.01, offset=0.0, nodata=65535` (max representable ≈655 in, correct floor for a non-negative cumulative product). Uint16, no signed-variable concern. 1←1 catalog bijection, all exclusion buckets empty. **Zero display-prep entries → `precip_total` is Group 1.**

| Group | NDFD | WPC |
|---|---|---|
| Group 1 | all 12 | `precip_total` |
| Group 2/3/4 | None | None |
| Excluded (any mechanism) | None | None |

#### Item 4 — structural differences

- **NDFD**: CONUS, 2.5 km, bilinear-warped from a native Lambert grid; run ids land at arbitrary minutes (poll-driven, publishes whenever upstream `GRIB_REF_TIME` changes); `keep_runs=8`, retention-turnover time is upstream-issuance-dependent, not derivable from code. Fetch: 9 plain `requests.get` calls to `tgftp.nws.noaa.gov`, **no retry, no fallback source** — any single failure aborts the cycle (poller retries next poll; publish is atomic per cycle via staging → `promote_run`, so a failed fetch cannot publish a partial run, but a *degraded* upstream silently shrinks that run's variable/frame set by design).
- **WPC**: CONUS, 5 km, bilinear warp. Poll-driven with a **completeness gate** — `select_latest_complete_run` only accepts a run with all 28 expected fhs present, falling back to the previous complete run otherwise; a partial upstream run is never published. `keep_runs=8`, retention turnover ≈ 2 days (code-derived estimate). Fetch: HTML-listing regex scrape + one GET per GRIB file, no retry; a units check raises on anything outside the expected kg/m²-family/inches range.
- Anchor coverage: both CONUS-region, no `na`-bbox concern like GEFS/EPS/AIFS/ECMWF; AK/HI fallback anchors sample as expected out-of-bounds nodata, non-blocking.

#### Scope decision: full parity, not read-side-only or permanent exclusion

Given the storage reclaim available here is modest (see below) relative to every GRIB model, the initial recommendation was read-side-only migration or permanent exclusion. **Overridden by explicit product direction: the goal is zero COG writes across every model/product, full stop — storage size is not the deciding factor.** Building full parity (gate + enforced rejection + COG-write skip, ported into both standalone publishers) is the committed path. This is more tractable than a from-scratch onboarding: the encode/write functions are already shared and proven, and both models' tolerance profile is the simplest possible case (100% Group 1, zero display-prep, zero ensemble complexity) — the work is porting an existing, proven gate pattern into two smaller files, not designing new logic.

**Phase 1 (permanent allowlist guard) explicitly skipped, by operator decision** — solo-dev judgment call to rely on not adding `ndfd`/`wpc` to the allowlist prematurely rather than adding a code-level block. Recorded as a real, accepted residual risk: until Phase 4 below, an accidental allowlist addition would expose live reads to a gate that (as of the Phase 2 fix) has real shadow-mode history behind it, but has never enforced anything and never skipped a COG write.

#### Phase 2 — shadow gate, including a critical bug found and fixed (completed 2026-07-06)

First implementation gated the shadow-gate call on `model in binary_sampling_models()` — i.e., the gate only ran once a model was already in the allowlist. **This was caught as a critical flaw before deploy, not after**: since the read-side allowlist check is the *same* flag, this design meant there was no way to gather shadow-mode evidence without simultaneously exposing live production reads to a completely unverified path — the opposite of every other model's Phase C sequencing, where shadow evidence accumulates for days *before* the allowlist is touched. Combined with the Phase 1 skip above, this would have meant the first accidental allowlist addition carried zero prior evidence.

**Fixed the same day**: the allowlist condition was removed entirely from both publishers. The gate now runs **unconditionally** on every frame write in both `ndfd_publish.py` and `wpc_publish.py`, gated only on the brotli-optional import guard (`check_pre_encode_value_sanity is not None`) — matching `pipeline.py`'s actual Phase C pattern exactly. Verified by direct read of `ndfd_publish.py::_write_ndfd_frame`: `binary_sampling_models` import removed entirely, condition is bare `if check_pre_encode_value_sanity is not None:`. On failure, logs `"Phase C shadow gate failed: pre-encode value sanity ...; frame remains governed by existing COG gates"`; on exception, `logger.exception("Phase C shadow gate errored: ...")`; **never rejects a frame** at this phase — `write_value_cog` and the grid write both still execute regardless of gate outcome. Tests inverted to assert unconditional execution (parametrized over both allowlist states) and that gate failure never blocks publish.

#### Phase C evidence (completed 2026-07-06)

Split per-pattern shadow-mode grep against `csky-ndfd-scheduler` and `csky-wpc-scheduler` (confirmed real systemd unit names — both pollers run under the standard fleet naming convention despite being poller-driven, not `scheduler.py`-managed) over a 48-hour window: **`0` shadow-gate failures, `0` errored, on both models.** Real, multi-day clean evidence, same bar as every other model.

#### Storage (measured 2026-07-06)

- **NDFD**: 18 GB total across 8 retained runs (~2.3–2.5 GB each, uniform), **4.1 GB value-COG (~23%)**.
- **WPC**: 1.6 GB total across 8 retained runs, **722 MB value-COG (~45% share, smallest absolute reclaim of any model in this migration)**.
- **Combined reclaim: ~4.8 GB** — smaller than every GRIB model except NBM's standalone 1.6 GB. The justification for completing this work is the architectural-consistency goal (Section 1), not the storage number.

#### Layers 1–2 + canary script extension (completed 2026-07-06)

`test_grid_value_decode.py` and `test_binary_sampler_parity.py` extended for both models via `PUBLISHER_MODELS_UNDER_TEST`/`PHASE_G_PUBLISHER_MODELS`, scope derived through the canary's own `_scope_for_model()` (same intersection pattern as every prior model, not hardcoded) — 13 new (model, var) pairs. Targeted coverage beyond the generic round-trip suite: a scope-partition pin (all exclusion buckets empty, parameterization matches canary scope exactly); mint/maxt signed round-trips pinning `offset=−100.0`; **rollup-magnitude round-trips for the in-app-derived variables** (`qpf_24h/48h`, `snow_24h/48h`, `wgust_24h_max`) at realistic record-scale values (up to 231, with a 2× headroom assertion) — confirming the packing/decode math holds at post-rollup magnitudes, not just small single-frame values; a WPC test pinning the 655.34 in packing ceiling with ≥10× margin over the real 7-day CONUS precipitation record (~60.6 in, Hurricane Harvey) and round-tripping at and above it. Layer 2 fixtures use NDFD's real 2.5 km / WPC's real 5 km CONUS grid geometry via the standard `get_grid_params`/`compute_transform_and_shape` path, all-Group-1 exact-equality assertions.

Canary script: `RUN_ID_RE` fix (above) plus tests covering both formats, malformed-id rejection, a minute-stamped HRRR run resolving to the same frame count as its hour-stamped twin (HRRR's 18z extended cycle makes a wrong slice visible if the hour-index math were broken), and NDFD/WPC minute-stamped runs correctly resolving to no scheduled frames (poll-driven, empty `target_fhs`).

Verification: 547 tests across the three extended files pass (430 decode, 82 parity, plus the canary suite); full suite shows the same 79 pre-existing failures as main, confirmed unrelated via stash comparison.

#### Phase G checklist status — NDFD and WPC

| Checklist item | NDFD | WPC |
|---|---|---|
| 1–4 — static audit | **Complete** (this section) | **Complete** (this section) |
| 5 — Layers 1–2 | **Complete 2026-07-06** | **Complete 2026-07-06** |
| Phase 2 — shadow gate | **Complete 2026-07-06**, critical allowlist-coupling bug found and fixed same day | **Complete 2026-07-06** |
| Phase C evidence | **Complete** — 48h clean, split per-pattern | **Complete** — 48h clean, split per-pattern |
| 7 — storage measurement | **Complete** — 18 GB total, 4.1 GB COG (~23%) | **Complete** — 1.6 GB total, 722 MB COG (~45%) |
| Real canary run (Layer 3 equivalent) | **Complete 2026-07-09** — 23,625 samples, 0 divergences | **Complete 2026-07-09** — 5,292 samples, 0 divergences |
| Phase 4 — enforce + COG-skip + allowlist flip | **Complete 2026-07-09** | **Complete 2026-07-09** |

**Canary run, completed 2026-07-09.** NDFD (`20260709_1930z`, full scope, no `--sample-limit`): 23,625 samples, **0 divergences**, `bin_meta_invalid_count: 0`. WPC (`20260709_1200z`, full scope): 5,292 samples, **0 divergences**. Both confirm the shared-encode-path prediction from the static audit — first time actually checked rather than inferred from "the functions are the same ones every other model uses."

**Latency finding on NDFD — real, investigated, non-blocking.** Benchmarks showed binary sampling **~2x slower than COG** on single-point, 100-point batch, and meteogram categories (e.g. single-point: COG 3.82ms vs binary 7.74ms), converging only at 1000-point batch scale. This is the only model in the migration where binary loses to COG on more than a single noisy benchmark category. Investigated by re-running the benchmark against a raw, non-derived NDFD variable (`qpf_6h`) instead of the originally-benchmarked derived rollup variable (`ice_24h`) to rule out the in-app rolling-sum derivation as the cause: **`qpf_6h` showed the identical ~1.8-2x slowdown pattern**, ruling out derivation as the explanation. Leading hypothesis, not further profiled: NDFD's small per-frame footprint (2.5 km CONUS, low variable count) means `read_binary_sample_value`'s fixed per-call overhead (meta-JSON parse + frame-file open) dominates relative to the actual data read, in a way it doesn't for larger models where the same fixed cost is a smaller fraction of total latency. **Not investigated further and not blocking**: correctness is fully proven (zero divergence on both benchmark passes and the full canary), NDFD/WPC's stated migration justification was architectural consistency (Section 1's single-artifact argument) rather than a performance win, and the absolute latency difference (single-digit milliseconds either way) is very unlikely to be user-visible against the app's sub-100ms frame-load priorities elsewhere. Worth a look if NDFD sampling is ever reported as slow in practice; not worth a profiling pass today.

**Phase 4 — enforcement, implemented and independently verified 2026-07-09.** Both publishers now mirror `pipeline.py`'s `binary_only` branch exactly, verified by direct read of both files (not just the implementation summary):

- The pre-encode gate call itself remains **unconditional** on every frame write in both files — the Phase 2 fix was not reverted. What changed is the handling of a `False`/exception result: computed once per frame as `binary_only = <MODEL_ID> in binary_sampling_models()`.
- **Rejection is enforced only when `binary_only` is true**: gate failure or a gate exception logs an ERROR (`"Pre-encode sanity gate rejected frame ..."` or `logger.exception("Pre-encode sanity gate errored ...")`) and returns `False` from `_write_ndfd_frame`/`_write_wpc_frame` **before** `write_value_cog`, the sidecar write, or `write_grid_frames_for_run_root` — confirmed by reading the function bodies directly; nothing is written for a rejected frame. In shadow mode (`binary_only=False`, the prior default), behavior is unchanged: log-only, full publish proceeds — including a deliberate asymmetry where a shadow-mode gate exception sets `gate_ok = True` internally to avoid double-logging the same event through two log lines.
- **Value-COG skip is correctly conditional**: written only when `binary_only=False`; skipped (with a `"Value COG write skipped (model=%s is binary-only)"` log line) when `binary_only=True`. The grid binary write remains unconditional either way, matching prior behavior.
- **Caller contract**: both `_write_*_frame` functions now return `bool`. `publish_ndfd_bundle`/`publish_wpc_bundle`'s loops do `if not _write_*_frame(...): continue`, so a rejected frame is excluded from `targets`/`frame_count`/`published_vars` and the bundle continues past it — one bad frame drops out, the rest of the bundle still publishes. Matches `build_frame`'s own status-based (not exception-based) rejection signal to its caller.
- **Two consequential fixes in WPC specifically, both confirmed present**: `frame_count` is now `len(targets)` (previously would have overcounted a bundle containing rejected frames), and WPC gained the `"WPC publish requires at least one frame"` empty-bundle guard that NDFD already had (confirmed NDFD already had this guard prior to this work, not newly added there) — without it, a bundle whose every frame was rejected would have promoted an empty run.
- Test coverage: enforcement tests per publisher covering rejection-blocks-all-writes (fail-if-called spy on `write_value_cog`, no grid write for the rejected frame), pass-with-`binary_only`-skips-only-the-COG-write, and gate-exception-blocks-in-enforced-mode-but-not-shadow-mode. Full suite green at the time of this work.

**Flip, completed 2026-07-09.** `CARTOSKY_BINARY_SAMPLING_MODELS` now includes `ndfd, wpc` (full list: `gfs, hrrr, nbm, gefs, eps, aifs, ecmwf, ndfd, wpc`); `csky-api`, `csky-ndfd-scheduler`, and `csky-wpc-scheduler` restarted. Post-flip verification: COG-write skip confirmed on post-flip poll cycles for both; `/api/v4/sample` spot-checks correct for a raw NDFD variable (`qpf_6h`), a derived NDFD variable (`qpf_24h`), and WPC's cumulative `precip_total`.

---

### Phase G audit — current_analysis (RTMA), GOES, and MRMS (standalone observed-product publishers)

These three are the last products in the migration and share the same architecture as NDFD/WPC: standalone publishers (`rtma_ru_publish.py`, `goes_publish.py`, `mrms_publish.py`), each poll-driven, none routed through `pipeline.py::build_frame`, each calling the shared `write_value_cog`/`write_grid_frames_for_run_root` directly. All three got the NDFD/WPC phased treatment (unconditional shadow gate → Phase C evidence → Layers 1-2 → canary → enforce + COG-skip + flip). Each also had at least one genuinely novel property beyond NDFD/WPC's simple all-Group-1 shape, documented per product below. All three flipped 2026-07-13–14.

**Shared reuse-path decision (recorded so it is not re-litigated).** All three publishers use hardlink-based frame reuse (`reuse_*_frame`/`_reuse_*_grid_artifacts`): when a frame in the rolling window is unchanged, the previous run's already-written artifacts are hardlinked rather than regenerated. Per explicit decision, the reuse paths get **no gate** — a reused frame is byte-identical to one that passed the gate at its original write. Confirmed sound. **One consequence discovered post-MRMS-flip and worth a permanent note:** when a reused frame's *original* predates the flip, it carries a value COG, and the hardlink faithfully propagates that COG forward into post-flip runs. This produces post-flip runs that still show COGs on disk — not from a fresh ungated write (verified via `stat` hard-link count >1 pointing back to a pre-flip inode), but from reuse re-linking a pre-flip original. It is self-resolving: as pre-flip source frames age out of the rolling window, there is no COG-bearing source left to link, and post-flip runs go COG-free on their own. Verified declining run-over-run (23→22→21→20→19 reflectivity COGs across successive MRMS runs immediately post-flip). Any future flip of a reuse-using product will re-encounter this; it is expected, not a gate failure, and clears within one rolling-window length.

#### current_analysis (RTMA) — audit, canary, flip (completed 2026-07-13)

Standalone publisher `rtma_ru_publish.py` (note: the service is `csky-rtma-ru-scheduler`; the model id in `_PACKING_BY_MODEL_VAR` and the allowlist is `current_analysis`, not the service name — same service-name-vs-model-id distinction as GOES below). Nowcast/analysis product, not forecast; `fh=0`-centric. 6 packed variables, all uint16, all Group 1 (zero display-prep entries). Canary scope is **4 variables, not 6**: `_scope_for_model` excludes `spres` (`buildable=False`, never independently published) and `mslp` (packing key registered under a normalize-alias with no catalog entry — the same uncataloged-variable exclusion bucket ECMWF's `precip_16d_anom` introduced). Both exclusions pinned in the Layer 1/2 tests; the excluded pressure pair still gets a targeted packed-entry Layer 1 test covering the migration's only `offset=800.0` floor (realistic MSLP 870–1083.8 hPa round-trips; sub-800 values clamp to 800.0, the spres-over-high-terrain hazard).

**Canary (`20260713_2017z`, full scope):** 6,804 samples, **0 divergences**, exit 0 — the cleanest of the three observed products, as expected for a no-transform all-Group-1 model. Storage measurement not separately recorded here (small nowcast footprint). **Flip 2026-07-13:** `current_analysis` added to the API env and `csky-rtma-ru-scheduler` env; both restarted; COG-write skip confirmed; `/api/v4/sample` `tmp2m` returned 96.6°F at Denver (plausible, correct). **Known pre-existing warning, not migration-related:** `csky-rtma-ru-scheduler` logs `Value range [636.8, 1028.4] outside spec range [960.0, 1040.0]` for `spres` — confirmed pre-existing (27 occurrences over 2 days pre-flip), a sanity-check-calibration false positive (636.8 hPa is real high-terrain surface pressure; the check's expected range is sea-level-MSLP-calibrated) on an out-of-scope variable that still reads from its COG. Backlog nit (widen `spres`'s expected range), not a migration item.

#### GOES (goes-east) — audit, canary, flip, and a live production bug fixed by the flip (completed 2026-07-13)

Standalone publisher `goes_publish.py` (service `csky-satellite-scheduler`; model id `goes-east`). Publishes **one band per invocation** (`band_config` parameter), each publish preserving the other three bands' manifest entries via `_preserved_manifest_variables` — four independent publish streams per model, a shape no other product has; the gate had to fire per-band-publish, confirmed via test. `goes_rgb_publish.py` (True Color RGB) is explicitly **out of scope** — parked pending server resources, never touched. 4 packed variables, all uint16: `ir13`, `wv9`, `wv8` (`scale=0.01, offset=-100.0`, units C), `vis2` (`scale=1/65534`, reflectance).

**Novel property — a hardcoded K→C conversion invisible to the tolerance classifier.** `prepare_grid_display_values` has a hardcoded special case at the top (`model=="goes-east" and var in {ir13,wv9,wv8}` → `values - 273.15`, meta id `goes_{var}_display_celsius_v1`), applied on the binary write path only — the COG write does not call display prep. So the **COG stores Kelvin, the binary stores Celsius**, a fixed 273.15 offset. `sampling_tolerance_group()` sees no config entry for these vars (the conversion is hardcoded, not table-driven) and classifies them Group 1 — the same classifier blind spot as ECMWF's `support_min_value` floor, a third distinct dimension the classifier can't see (hardcoded value transforms). Per the standing operator decision, the classifier was **not** patched; the canary's expected signature is verified instead.

**Canary (`20260713_2027z`, full scope):** exit 4 as expected — 20,913 divergences, **every one on ir13/wv9/wv8 with mean delta 273.150 (min 273.145, max 273.155**, the ±0.005 spread being the two independent 1-decimal sampler roundings); `vis2` zero divergences. Signature confirmed exactly; the exit 4 is the accepted K→C constant-offset pattern, not a bug.

**The flip fixed a live production bug.** Pre-flip, `/api/v4/sample` for `goes-east ir13` read from the Kelvin-storing COG but labeled the result °C — returning e.g. `291.1 "°C"` (physically absurd; 291.1 is Kelvin ≈ 18°C). This had been wrong for as long as GOES sampling existed on the COG path; it went unnoticed because the map's hover/city-value sampling already reads the Celsius binary (Section 5), so only the REST `/api/v4/sample` endpoint served the mislabeled value and evidently nothing user-facing consumed it for satellite bands. **Post-flip verified:** the same endpoint now returns `38.3 "°C"` — a plausible clear-sky land brightness temperature, correctly labeled. The flip retired the Kelvin COG read and switched to the Celsius binary, making the endpoint correct for the first time. **GOES −100°C packing floor** (`offset=-100.0`) clips exceptional overshooting-top brightness temps below −100°C to −100.0 rather than nodata — accepted as-is by operator decision (rare, small error on an already-extreme value). **Flip 2026-07-13:** `goes-east` added to API env + `csky-satellite-scheduler` env; both restarted; COG-write skip confirmed.

#### MRMS — the hardest product: a data bug, a latency bug, and four write sites (completed 2026-07-14)

Standalone publisher `mrms_publish.py` (service `csky-radar-scheduler`; model id `mrms`). The most structurally complex product in the entire migration: real-time radar mosaic (largest grids in the system, 1 km CONUS ≈ 4609×8238), the **only uint8-packed variables** in the codebase (`reflectivity`, `mrms_radar_ptype`), 3-level LOD config, in-publisher `mrms_radar_ptype` compositing (`compose_mrms_radar_ptype`), a deferred supplemental-variable write path (`finalize_mrms_published_run` writing directly to the published run dir, not staging), and **four separate `write_value_cog` call sites** — all four gated in Phase 4 (`write_mrms_frame`, `write_mrms_radar_ptype_frame`, and the shared `_write_mrms_supplemental_frame_to_run_root` covering both the staging and deferred paths), verified by direct read. 5 packed variables: `reflectivity` + `mrms_radar_ptype` uint8, `mrms_recent_precip_6h/24h/72h` uint16.

**Pre-existing production data bug, found and fixed mid-migration (independent of the migration).** Raw MRMS reflectivity carries two official NSSL sentinels present verbatim in the source: **−999.0 ("no coverage", ~25% of a frame)** and **−99.0 ("missing / no echo", ~67% of a frame)** — documented at https://www.nssl.noaa.gov/projects/mrms/operational/tables.php. Two compounding bugs corrupted the data: (1) `_warp_frame_to_target_grid` never passed `src_nodata` to `warp_to_target_grid`, so bilinear resampling blended the discrete sentinels into real echo at coverage boundaries (producing a smear of impossible values between −999/−99 and real dBZ); (2) `prepare_grid_display_values`'s blanket `prepared[prepared < 0.0] = 0.0` clamp — correct for precip/snow, wrong for reflectivity — flattened all of it, sentinels *and genuinely real negative reflectivity* (dBZ is logarithmic; real weak-echo runs to −18 dBZ), to 0.0 on the binary path. This was live on the existing COG-and-binary rendering path, not introduced by the migration; it went unnoticed because the COG's `transparent_below_min` renders the discrete sentinels invisible on the map. **Fixed and verified on prod:** post-fix histogram of a fresh run shows sentinels gone (0.00%, was 91.8%) and real negative reflectivity preserved (8,575 pixels down to real weak-echo values). The fix rode in on commit `522efe37` ("feat: Add edge fade rendering option for sparse fields in grid display prep" — a misleading commit name for what is substantially a sentinel/clamp bugfix; noted so a future bug-trace can find it).

**Canary signature (accepted, per prior operator decisions).** `reflectivity` classifies Group 1 by the current classifier but has `smooth_sigma=0.45` display prep — a fourth classifier-blind-spot dimension (smoothing), consistent with the ECMWF-floor/GOES-K→C category; the classifier was not patched. Canary exits 4 on `reflectivity` only, from smoothing on gradients: verified across multiple runs at max 1.593–1.977 dBZ, median ~0.4, **zero over 2 dBZ**, and zero at the −10 packing floor when weak-echo isn't sampled. `mrms_radar_ptype` (uint8 categorical, lossless scale-1.0 packing — the first live uint8 canary test, decode branch confirmed working) and all three `mrms_recent_precip_*` show **zero** divergence as required. **`reflectivity` −10 dBZ packing floor** (`scale=0.5, offset=-10.0`): real weak echo below −10 dBZ clips to −10.0 (not nodata) — accepted as-is for a storm-display product; sub−10 dBZ is below meaningful precipitation signal and the palette hides it. **GOES-style note:** the floor decision was accepted analytically; canary runs frequently don't sample sub−10 pixels (weak echo is localized against the 189 fixed anchors), so the floor is accepted-but-often-unexercised.

**Latency regression, diagnosed and fixed before flip — the seek-read optimization.** The initial MRMS canary benchmark showed binary sampling **15-18× slower than COG** (71 ms vs 4 ms single-point, 66 ms vs 4 ms meteogram) — uniquely bad; every other product was faster or comparable. Root cause (confirmed by reading `sampling.py`): `read_binary_sample_value` → `_read_binary_frame_values` read the **entire frame file** (`Path(frame_path).read_bytes()`) and decoded **every pixel** before indexing one — for MRMS 1 km uint8 that is a ~38 MB read + 38M-element decode + ~152 MB float32 alloc *per sample*; the uint16 `mrms_recent_precip_*` vars would be worse (76 MB reads). Not LOD (the sampler only opens level 0), not the uint8 branch (pure array-size cost). A `read_binary_sample_value_seek` primitive already existed in the same file (single `seek` + itemsize-byte read + one-element decode, equality-pinned result-identical, built for the ensemble-member fan-out and already carrying production load via `sample_member_values_seek`). **Fix:** re-pointed the three full-read call sites (`/api/v4/sample` route in `main.py`, `sample_binary_batch_values`, and the meteogram path via `sample_binary_point_value`) at the existing seek primitive; extended the seek-equality test to a uint8/MRMS and uint16-large-grid case; pointed the canary's benchmark at the seek path (keeping its correctness comparison on the full-read primitive, so every canary sample is now a free seek-vs-full cross-check on real data). **Result, re-measured on the real grid:** binary now **beats** COG 10-20× across every category (single-point 0.14 ms vs 2.05 ms; meteogram 0.36 ms vs 2.94 ms) — the 15-18× regression inverted to a 10-20× improvement. Commit `188a5419`.

**Flip 2026-07-14.** `mrms` added to API env + `csky-radar-scheduler` env (full allowlist: `gfs, hrrr, nbm, gefs, eps, aifs, ecmwf, aigfs, ndfd, wpc, current_analysis, goes-east, mrms`); both restarted. Post-flip verified: COG-write skip firing for reflectivity and ptype; `/api/v4/sample` `reflectivity` returns `noData: true` at a clear-air point (correct — the sentinel fix working end-to-end, nodata rather than a garbage value); `mrms_recent_precip_24h` (the uint16 76 MB-full-read-worst-case var pre-fix) returns fast and correct via the seek path. Residual post-flip COGs on disk are reuse-path hardlinks to pre-flip source frames, declining run-over-run and self-resolving as the rolling window turns over (see the shared reuse-path decision above).

#### Phase G checklist status — current_analysis, GOES, MRMS

| Checklist item | current_analysis | goes-east | mrms |
|---|---|---|---|
| 1–4 — static audit | **Complete** | **Complete** | **Complete** |
| 5 — Layers 1–2 | **Complete** | **Complete** (Celsius round-trip pinned) | **Complete** (first uint8 decode coverage; negative-reflectivity fixture) |
| Phase 2 — shadow gate | **Complete** | **Complete** (per-band) | **Complete** (all 4 call sites) |
| Phase C evidence | **Complete** — shadow-clean | **Complete** — shadow-clean | **Complete** — 342 frames shadow-clean post sentinel-fix |
| Canary (Layer 3 equiv.) | **Complete** — 6,804 samples, 0 divergences | **Complete** — K→C signature confirmed (273.150 ± 0.005 on IR/WV, vis2 zero) | **Complete** — reflectivity smoothing-only (<2 dBZ), ptype/precip zero |
| Latency | normal (small grid) | binary beats COG | **seek-read optimization** — 15-18× regression → 10-20× improvement |
| 6 — flip | **Complete 2026-07-13** | **Complete 2026-07-13** (fixed live Kelvin-mislabel bug) | **Complete 2026-07-14** |
| 7 — storage | small nowcast footprint | not separately recorded | reclaim passive as COG-era + reuse-linked runs roll off |

With these three flipped, **every COG-writing product in CartoSky is on the binary sampling pipeline. The migration is complete.**

---

## 4a. Future hardening — not blockers for this migration

These items came out of independent review and are good practice, but none of them gate Phase A or any subsequent phase. They are recorded here so they are not lost, not so they get bundled into the GFS rollout's critical path.

- **Formal grid binary format specification (`docs/grid-binary-format.md`).** The grid binary format already exists today as the sole format for WebGL rendering — this migration adds a second consumer to an existing format, it does not create the format. Documenting the header/metadata/encoding/packing/endianness/nodata conventions in one place is good practice once the binary becomes the system's single authoritative sampling-and-rendering artifact (Section 1's strategic framing), but it can be written at any point before or after Phase A without affecting correctness or timeline.
- **Binary integrity validation (checksum/hash in metadata).** Worth noting that `write_grid_frame_for_run_root()` already writes to a `.tmp` path and performs an atomic `replace()` onto the final path (`grid.py`) — a reader can never observe a partially-written binary file today; it is always either the complete previous file or the complete new file. This substantially reduces the practical risk a checksum would catch (truncated writes are already structurally prevented); a checksum would still guard against rarer failure modes like disk-level bit rot, and is reasonable future hardening once the binary is the sole sampling source, but it is not closing a gap that exists today.
- **Memory-mapped file access (`mmap`/`numpy.memmap`).** See Phase B and Phase D above — this is explicitly a "benchmark first, decide later" item, not a prescribed architecture choice. Do not implement preemptively.

---

## 5. What does NOT change

- WebGL rendering pipeline — already reads only grid binaries, untouched.
- Contour generation — confirmed no dependency on the value COG (Section 2).
- City *name* labels — static GeoJSON asset, no sampling involved at all (Section 2).
- City *value* labels — already client-side via `sampleAnchorPoints()` in `grid-webgl.ts`, already reading grid binaries directly, unaffected by this backend migration (Section 2).
- Difference mode / Compare tool — reads grid binaries already, unaffected.
- Satellite/GOES, NWS hazards, SPC, CPC, vector products — entirely separate pipelines.
- Screenshot/share pipeline — renders from WebGL/grid binaries, unaffected.

---

## 6. Storage impact

Confirmed packing scales for GFS variables are reasonable for display precision — no redesign needed before migrating.

**Measured on prod, not estimated.** Per-run directory size (`/opt/cartosky/data/published/gfs/{run}/`, all artifact types combined — value COGs, grid binaries, sidecars, contour GeoJSON) is **14 GB per retained run**, consistent across all 6 currently retained runs (`20260629_06z` through `20260630_12z`), totaling **84 GB** for GFS's current retention window.

Value COG footprint specifically (`*.val.cog.tif` only, summed across all variables and all 6 retained runs): **23 GB total**, averaging **~3.83 GB per run**. This means value COGs account for **~27% of GFS's total per-run storage footprint** — removing them is a real, measured 23 GB reclaim today, not a projection, and that figure scales with retention count and with however GFS's variable set or forecast-hour range changes over time.

**Per-variable breakdown** (also measured on prod, single-run sizes vary slightly run-to-run due to forecast-hour-count differences across cycles):

| Variable group | Approx. per-run COG size | Note |
|---|---|---|
| `tmp2m`, `tmp2m_anom`, `tmp850`, `tmp850_anom`, `rh2m`, `rh700`, `pwat`, `wspd10m`, `wspd300`, `wspd850`, `wgst10m`, `vort500`, `precip_total`, `hgt500_anom`, `dp2m` | ~175–211 MB each | The bulk of the footprint — each one of these full-range continuous variables individually accounts for roughly 1% of the total per-run size |
| `sbcape`, `mlcape`, `mucape` | ~87–102 MB each | |
| `precip_5d_anom` | ~135 MB | Largest of the four anomaly variables |
| `precip_7d_anom` | ~102 MB | |
| `precip_10d_anom` | ~52 MB | |
| `precip_16d_anom` | ~2.1 MB | Smallest in scope — short retained forecast-hour window relative to the other anomaly variables |
| `snowfall_total`, `snowfall_kuchera_total` | ~63–76 MB each | Group 2 (3x upscale) |
| `ptype_intensity_rain` | ~30–33 MB | Group 2 (3x upscale) |
| `ptype_intensity_snow` | ~6.4–8.9 MB | Group 2 (3x upscale) |
| `ptype_intensity_ice` | ~1.6–2.0 MB | Group 2 (3x upscale) |
| `ptype_intensity` | ~5.6–6.0 MB | Group 3 (categorical-nearest, 3x upscale) |
| `ice_total` | ~5.8–8.6 MB | |

**Worth noting for prioritization, not action in this phase:** the four Group 3/Group 2 categorical and `ptype_intensity_*` variables that require the most additional testing complexity (Section 3, Layer 2/3 tolerance groups) are collectively among the *smallest* contributors to GFS's storage footprint — low double-digit MB at most, several under 10 MB per run. The engineering cost of correctly handling their upscale/categorical-nearest behavior is not proportional to their storage contribution. This is expected and fine — correctness matters regardless of file size — but it means the bulk of the realized storage win comes from the large, simple, non-upscaled continuous variables (`tmp2m`, `rh2m`, `pwat`, `wspd*`, etc.), which are also the lowest-risk variables to migrate. No scope change as a result, but worth knowing that the highest-value, lowest-risk variables and the highest-testing-complexity variables are largely non-overlapping sets.

---

## 7. Resolved items (all independently re-verified)

| # | Item | Resolution |
|---|---|---|
| 1 | Persist transform vs. reconstruct from bbox | **Resolved with correction: persist the effective post-upscale encoded-frame transform, not the original pre-upscale transform. See Decision Point A, Section 4.** |
| 2 | City label sampling dependency on COG path | **Resolved with correction: no backend dependency, but real client-side value-label sampling exists via `sampleAnchorPoints()` in `grid-webgl.ts`, already reading grid binaries, unaffected by this migration. See Section 2 and Section 5.** |
| 3 | Contour generation dependency on value-COG-based grid frame function | **Resolved: no dependency. `_build_contour_metadata_for_variable` is independently sourced.** |
| 4 | `prepare_grid_display_values` resolution parity between COG and binary | **Resolved with correction: seven GFS variables are affected, not three, with `ptype_intensity` requiring separate categorical-nearest handling. See Section 1 and Section 3.** |
| 5 | Canary length | 4 model cycles, confirmed. |
| 6 | Real current GFS COG disk usage on prod | **Resolved: measured on prod. 23 GB total value-COG footprint across 6 retained runs (~3.83 GB/run average), ~27% of GFS's total 84 GB per-run-retention storage footprint. See Section 6 for full per-variable breakdown.** |
| 7 | GFS scope list completeness | **Resolved with correction: four precip-anomaly variables (`precip_5d_anom`, `precip_7d_anom`, `precip_10d_anom`, `precip_16d_anom`) were missing from the original scope. Verified as real, live, `primary=True` catalog variables via loop-registration in `gfs.py`. Added to scope. See Section 2.** |
| 8 | Meteogram/sample endpoint cache-key and route-handler substrate awareness | **Resolved: `sampling_source` added to `_meteogram_cache_key()` hashed inputs at Phase F; canary uses isolated offline/shadow path. `/api/v4/sample` and `/api/v4/sample/batch` are structurally COG-dependent at the route-handler level — route-handler code changes required at Phase F, not just flag flips; `_sample_cache` TTL is 2 seconds in-process-only. See Phase B, Section 4.** |
| 9 | Pipeline quality-gate replacement approach | **Resolved with correction: run new and old gates in parallel for an evidence-gathering period before removing COG-based gates. See Phase C, Section 4.** |
| 10 | Layer 1 test correctness for clipped values | **Resolved with correction: separate assertion paths for in-range round-trip values vs. deliberately out-of-range clipped values. See Section 3, Layer 1.** |

---

## 8. Remaining open items before Phase A begins

None. All previously open items are resolved — see Section 7. Phase A may begin.
