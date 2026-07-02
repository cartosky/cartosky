# Value COG → Grid Binary Sampling Migration Plan

**Status:** All gating items resolved. Ready to begin Phase A. Targets GFS as the first model, with a 4 cycle parallel-write canary before cutover. All Phase A-F infrastructure is required to be built model-parameterized (see the cross-cutting requirement at the start of Section 4) so that staged rollout to additional models is a configuration change plus a per-model audit (Phase G's checklist), not a re-implementation.

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
- Ensemble per-member sampling — separate workstream, sequenced after this migration completes in full.
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

Tolerance groups for the generalized canary:

| Group | HRRR variables |
|---|---|
| Group 1 | `dp2m`, `mlcape`, `mucape`, `precip_total`, `pwat`, `rh2m`, `rh700`, `sbcape`, `snowfall_kuchera_total`, `snowfall_total`, `tmp2m`, `tmp850`, `tmp850_anom`, `vort500`, `wgst10m`, `wspd10m`, `wspd300`, `wspd850` |
| Group 2 | `radar_ptype_frzr`, `radar_ptype_rain`, `radar_ptype_sleet`, `radar_ptype_snow` |
| Group 3 | None |
| Group 4 | `radar_ptype` |

The `radar_ptype` Group 4 classification is intentional and structurally distinct from GFS's old categorical group: `grid_display_prep_config("hrrr", "radar_ptype")` has `upscale_factor=1` and `categorical_nearest=True`. There is no resolution difference between the value COG and the grid binary for this variable, so the canary should require strict integer-category equality and treat any divergence as blocking. The four `radar_ptype_rain/snow/sleet/frzr` component variables each have `upscale_factor=3` and `categorical_nearest=False`, so they are Group 2 continuous-upscale variables.

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
