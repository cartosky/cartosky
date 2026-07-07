# Build Pipeline Audit — 2026-07-07

Scope: the builder pipeline under `backend/app/services/builder/` — `derive.py` (7,350 lines, two lenses: numerical correctness and structure/performance), `pipeline.py` + `services/scheduler.py` (orchestration), `fetch.py` (data acquisition), and the output stages (`cog_writer.py`, `colorize.py`, `members.py`). ~16.5k lines total, read in full by five parallel audit passes.

**TLDR:** The derive dispatch architecture is clean and the core math (Kuchera SLR, unit conversions, APCP differencing) verified correct — but there is one confirmed high-severity data bug (ECMWF ptype thermal signals silently zero out in warped-component mode, making ice storms render as rain), a cluster of "silently wrong frame" failure modes that ship as full quality, and structural explanations for two known prod incidents: the viewer 404s (non-atomic publish swap + manifests never evicted) and the EPS memory/swap issue (GDAL block cache never bounded + memory-prune allowlist skipping the heaviest derive strategies + float64 member warps). There is also a live instance of the same model-id-leak class that caused the July 6 eps/ifs outage.

Severity legend: **HIGH** = wrong data shipped or prod-incident cause; **MED** = latent correctness trap, meaningful perf/build-time cost, or divergence risk; **LOW** = cleanup, minor cost, or informational.

---

## 1. Data-accuracy findings (derive logic)

### 1.1 HIGH — ECMWF ptype thermal signals silently drop to zero in warped-component mode

`_ptype_intensity_ecmwf_phase_signals` (`derive.py:2031-2057`, called from `derive.py:2267-2276` and `derive.py:5174-5183`) fetches tmp2m/tmp925/tmp850 **without** the warp parameters (`use_warped`/`target_region`/`target_grid_id`/`resampling`), while the precip/snow steps it combines with *are* warped. The temperature grids come back native-shape, fail the `values.shape != expected_shape` check at `derive.py:2041`, and are silently skipped — `deep_cold`, `surface_cold`, `warm_nose` all become zeros (`derive.py:2055-2057`).

Consequence: classification in `_ptype_intensity_family_rates_ecmwf` (`derive.py:2100-2110`) reduces to `snow_frac`-only. Freezing rain/sleet (`ice_mask` requires `surface_cold >= 0.45 & warm_nose >= 0.35`) can **never** be produced — an ice storm renders as plain rain, and ECMWF ice accumulation totals ≈ 0. The GFS counterpart (`derive.py:2573-2586`) forwards warp params correctly; this is copy-paste divergence.

Fix (S): thread the four warp params through, exactly as `_ptype_intensity_thermal_fields` does for GFS; add a warped-mode ECMWF test mirroring `test_ptype_intensity_uses_warped_component_fetches_when_requested`.

### 1.2 HIGH — Fail-open fallbacks produce confidently wrong frames with no quality flag

- `_kuchera_frozen_fraction_for_step` (`derive.py:2729-2737`) returns **all-ones** when csnow fetch fails — the ptype gate opens fully, so all precip is counted as snow and a warm rain event paints multi-inch SLR-boosted "snow". The returned fallback flag is **discarded** at the call site (`derive.py:6492` — `_ptype_fallback_used` never read), so `quality_flags` (`derive.py:6871-6875`) omit it and the frame ships `quality=full`.
- ECMWF phase-signal fetches are individually swallowed at debug level (`derive.py:1658-1660`); total failure returns zeros → everything classifies as rain.
- ECMWF snow-component fallback (`derive.py:2248-2257`, `derive.py:5162-5172`): `except Exception` → snow_step=zeros, debug-level log only → snow rendered as rain in intensity/accumulation.

Fix (S — highest correctness-per-line payoff in the file): thread the existing `_record_derive_quality` mechanism (`derive.py:812-830`) through these paths with `ptype_gate_fallback`, `phase_signals_missing`, `snow_component_missing` flags; promote logs to warning. Consider failing the Kuchera gate closed (zeros) rather than open (ones).

### 1.3 MED — Accumulation validity is OR-merged across steps; missing mid-run data silently undercounts

`derive.py:4034`, `4039` (`_cumulative_apcp_loop`), `5212` (ECMWF ptype accumulation), `6740` (Kuchera subset loop): each step's invalid pixels contribute 0 and the final mask is `logical_or` of per-step validity. A pixel missing in N-1 of N steps still renders a confident finite total. Example: corrupt GFS APCP record for fh 30-36 over a swath → `precip_total` at fh 384 shows a finite value that omits 6 h of precip with no NaN and (except Kuchera) no quality flag — precip_total/10to1/ptype accumulation record no `quality_flags` at all.

Fix: AND-merge validity (NaN where any contributing step was invalid) or record a per-var degraded-quality flag.

### 1.4 MED — No guard that the accumulation step sequence ends at the requested fh

`_resolve_cumulative_step_fhs` (`derive.py:3046-3097`) and all four callers: `range(step_hours, fh+1, step_hours)` silently drops the tail partial window when `fh % step_hours != 0` or when `step_hours_after_fh` transition hints don't land on fh. The derive returns an accumulation valid through the last step but published/labeled as valid at `fh`. If cadence hints drift from upstream reality (model cadence change), users see precipitation "pause" on off-cadence frames.

Fix (S): assert `step_fhs and step_fhs[-1] == fh` (raise or flag degraded) — catches hint/cadence drift permanently.

### 1.5 MED — Snow "component" planes carry a hidden 2× display boost

`derive.py:2465-2486`, `2629-2635` (GFS `snow_display = 2.0 * snow_rate` stored as the family's `snow` plane), `derive.py:5039-5041` (ECMWF ×2). The value grid published for the `snow` ptype component is 2× the 3-h-equivalent liquid intensity while rain/ice are unboosted. Any downstream consumer sampling these planes as physical values (binary sampler, meteograms, compare/diff) reads 2× the model LWE, and the three family planes are mutually inconsistent. Given the binary-sampling migration, verify whether ptype planes are sample-exposed.

Fix: keep the boost strictly inside index binning; store unboosted rates in family/component planes, or document the plane as display-scaled and exclude it from value sampling. The ×2.0 constant is also hardcoded in four places (`derive.py:2171`, `2486`, `2629`, `2997/5040`) — hoist it.

### 1.6 MED — Percent-vs-fraction probability detection is a data-dependent heuristic

`_normalize_ptype_probability` (`derive.py:1601-1608`): `scale = 100 if nanmax > 1.5 else 1`. A percent-encoded field whose domain max is ≤ 1.5 (light/sparse event, warped subregion) is treated as fractional — 1.2% becomes probability 1.0 → frozen fraction 1.0 in drizzle areas → one-frame snow flash in animations. Also frame-to-frame inconsistency when the max hovers around 1.5.

Fix: carry units metadata from the component spec (`probability_units=percent|fraction`) instead of inferring from data.

### 1.7 MED — Ptype accumulation binarizes fractional masks; snowfall doesn't

`_derive_ptype_accumulation_cumulative` (`derive.py:7133-7137`) unconditionally binarizes `interval_mask >= threshold → 1.0/0.0`, while snowfall (`derive.py:5777-5782`) binarizes only when `snow_mask_threshold` is explicitly configured and otherwise uses the fractional mean (behavior pinned by `test_gefs_snowfall_derive_uses_fractional_mean_csnow_without_binary_threshold`). Consequence: GEFS-mean ice accumulation (cfrzr mean is fractional, rarely ≥ 0.5) collapses to ~0 while GEFS-mean snowfall correctly scales.

Fix: mirror the snowfall behavior — binarize only when the threshold hint is present.

### 1.8 MED — Radar-ptype argmax not NaN-guarded

`derive.py:4837-4841`: `np.argmax(mask_stack, axis=0)` on the raw stack — NaN compares as maximal, so a NaN in any categorical mask claims the pixel for that type; with default `min_mask_value=0.0` the nanmax gate still passes. The equivalent argmax in the intensity paths already got the fix (`np.nan_to_num(stack, nan=-1.0)` at `derive.py:2139`, `2450`); this one didn't.

Fix (S): apply the same `nan_to_num` guard.

### 1.9 MED — Three different step-validity definitions for the same physical input

`_derive_precip_total_cumulative._process_step` (`derive.py:5365-5372`) accepts any finite value (negatives clamped to 0); snowfall (`derive.py:5724-5727`) and the inventory path (`derive.py:3687`) require `isfinite & >= 0`. A negative sentinel surviving fetch nodata scrubbing renders as valid 0.00" in precip_total but invalid elsewhere.

Fix: standardize on `isfinite & >= 0` via a shared helper.

### 1.10 LOW — additional derive accuracy items

- **Cumulative cache key omits derive hints** (`derive.py:487-501`, `655-718`): `run/var/fh/grid[:version]` only. Changing `slr`, `min_step_lwe_kgm2`, ptype-gate settings, or Kuchera levels mid-run reuses prior-fh caches computed under old semantics unless `cumulative_cache_version` is manually bumped. Fold a hash of accuracy-relevant hints into the key.
- **Rain bin table has 17 levels but `count=16`** (`derive.py:2152/2157`, `2467/2472`): rates ≥ 3.0 in/3h clip into the 2.5–3.0 bin; the top color is unreachable. Display-only.
- **`min_step_lwe_kgm2` (default 0.01) trims drizzle from snowfall/Kuchera/ptype accumulations but not precip_total** — masked accumulations can never exactly reconcile with precip_total even at 100% mask. Intentional noise filter, but undocumented asymmetry.
- **vort500 dateline/pole handling** (`derive.py:4657-4679`): `np.gradient` has no longitude wraparound at the dateline and NaN in u/v erodes a 1–2 px halo. Fine for CONUS, visible for global regions.
- **Kuchera per-pixel SLR silently defaults to 10.0** for below-ground/masked levels (`derive.py:408`, `6640`); the `slr_fallback_10to1` quality flag is only set when *zero* levels resolve for a step, not for partial per-pixel fallback (e.g. high terrain fully masked by the surface-pressure filter).

### 1.11 Verified correct (recorded to avoid re-auditing)

- Temperatures arrive in °C: fetch normalizes via GDAL `GRIB_NORMALIZE_UNITS` + explicit `[k]`-tag handling (`fetch.py:1631-1634`, `1684-1690`), so °C thresholds in ptype thermal signals, Kuchera caps (F conversion at `derive.py:427`, `6676`), and RH `temp_units="c"` are consistent.
- m→in (39.37, ECMWF tp/sf in meters) vs kg/m²→in (0.03937, GFS APCP in mm) split is correct per source units, mirrored by `m_to_in` vs `kgm2_to_in` in fetch.py.
- Kuchera SLR formula (`derive.py:388-396`) matches the published formulation (T0=271.16 K, warm branch ×2, cold ×1, clamp 5–30).
- `f_to_c_delta` is a pure ×5/9 with no offset (`fetch.py:4152-4154`) — correct for deltas, distinct from the absolute F→C at `derive.py:4286`.
- The tmp850_anom ±17°C ladder is exactly right: `colormaps.py:608-616` — 41 levels / 40 colors, 0.5°C inner steps, legend_stops match the digitize bins; packing (scale 0.1 / offset −80 / units C) quantizes at 0.1°C vs the 0.5°C finest bin, so no edge banding.
- GFS cumulative-APCP differencing (`_resolve_apcp_step_data`, `derive.py:3398-3909`) correctly seeds `consumed_sum` from the prior run-cumulative cache and re-raises grid mismatches into full rebuilds rather than clamping; heavily tested.
- The July 6 fail-closed readiness fix is intact: `product_hour_has_any_idx` fails closed on unclassified errors (`fetch.py:2502-2519`).

---

## 2. Known-incident structural causes

### 2.1 HIGH — Non-atomic publish swap creates a 404 window on every publish

`_promote_run` (`scheduler.py:1363-1368`): builds `tmp_run`, then `shutil.rmtree(published_run)` followed by `shutil.move(tmp_run, published_run)`. Between rmtree and move, the published run directory does not exist — and rmtree of a large run tree (thousands of frames for ensembles) can take seconds. Every publish (initial + progress publish every ~4 new frames + member-pass promotes) opens this window. This means the *live* run flickers out on every snapshot — a direct contributor to the viewer-404 incident pattern, alongside stale run ids.

Fix (S): rename-swap — `os.rename(published, trash)` → `os.rename(tmp, published)` → delete trash in background. Two renames = milliseconds of exposure.

### 2.2 HIGH — Manifests are never evicted with their runs

`_manifest_path` writes `data_root/manifests/<model>/<run>.json` (`scheduler.py:1082-1084`), but `_enforce_run_retention` (`scheduler.py:2521-2523`) prunes only `staging/<model>` and `published/<model>`. Clients (or caches) resolving a run via an old manifest get a valid-looking manifest whose frames were rmtree'd — exactly the documented "stale client-resolved run ids vs backend run retention" incident.

Fix (S): prune `manifests/<model>` with the same `effective_keep_runs` in `_process_run`; optionally return 410 from the frames route for manifest-known-but-evicted runs.

### 2.3 HIGH — GDAL block cache never bounded (matches the EPS swap incident)

No file in `backend/` sets `GDAL_CACHEMAX` / `CPL_CACHE` / `SetConfigOption` (repo-wide grep). GDAL defaults to 5% of RAM for the block cache and it is never trimmed; combined with glibc arena growth this is the multi-GiB-swap suspect from the EPS memory audit. Current mitigations are band-aids: `malloc_trim` after runs (`scheduler.py:1689-1701`) and full process restart after successful runs for big models (`RESTART_ON_SUCCESS_MODELS`, `scheduler.py:178`, `2850-2855`).

Fix (S): set `GDAL_CACHEMAX` (256–512 MB) via `rasterio.Env`/env var at scheduler startup; consider `MALLOC_ARENA_MAX=2` in the unit file.

### 2.4 HIGH — Memory-prune allowlist silently skips the heaviest derive strategies

`prune_fetch_context_after_frame` (`derive.py:237-247`): `handled_derive_kinds` omits `precip_total_cumulative`, `snowfall_total_10to1_cumulative`, the `ptype_accumulation` siblings, `radar_ptype_component`, `ptype_intensity_component*`, and the anomaly strategies. Snowfall 10to1 fetches APCP + csnow for **every step fh** of the run (up to fh384 GFS) into `ctx.fetch_cache`/`warp_cache` and is never pruned between frames — caches grow unbounded across the frame loop for exactly the strategies that fetch the most bands. Matches the multi-GiB RSS symptoms. Note: Python-side lifecycle is otherwise disciplined (`destroy_fetch_context` clears all caches, per-frame prune for listed kinds, RSS checkpoints) — consistent with the prior audit's "leak is native" conclusion, but this allowlist gap is a real Python-side contributor for cumulative vars.

Fix (S): invert to opt-out (prune for every derived var) or key off `DeriveStrategy` metadata; new strategies currently default to "never pruned".

### 2.5 HIGH — Model-id leak class (July 6 eps/ifs incident) has a live instance

`_component_precheck_available` (`scheduler.py:804-819`): constructs `request = plugin.herbie_request(...)` then **discards it**, calling `fetch_variable(model_id=model_id, ...)` with the raw caller-supplied internal id. `fetch.py` passes `model_id` verbatim to `Herbie(model=...)` in 5 places (`fetch.py:3510`, `1954`, `2184`, `2351`, `2453`) with no guard. Latent today only because the Kuchera-precheck models (`scheduler.py:856/869`) have internal id == Herbie id — exactly how the eps probe bug stayed hidden until the probe went fail-closed. Corroborating smell: `_eps_full_file_cache_enabled` (`fetch.py:585`) defensively accepts both `{"ifs", "eps"}`.

Fix (S): use `request.model`/`request.product` at `scheduler.py:813`; add a guard in `fetch_variable` rejecting known internal-only ids (e.g. `eps`).

### 2.6 MED — Readiness-probe cache key omits `fh`

`_ensure_products_ready` (`pipeline.py:1412-1428`): cache keys are `f"{request.model}|{request.product}"` and bare `product_name`; `fh` is not in the key, and the scheduler passes one `readiness_cache` per (region, var) across all fhs (`scheduler.py:2238`). After fh N probes ready, all later hours **skip the fail-closed readiness gate entirely** and fall through to `fetch_variable` failure paths. Same gate class as the 18z EPS readiness incident. The bare `product_name` key can also collide across sub-models.

Fix (S): include `fh` in the cache key (idx presence is not monotonic for models publishing hours incrementally).

---

## 3. Build time & performance

### 3.1 HIGH — COG encode pays compression 2–3×, single-threaded, no predictor

`cog_writer.py:114`, `580-612`, `666-684`: every frame writes a DEFLATE base GTiff via rasterio (single-thread), gdaladdo decompresses/recompresses to add overviews (**two** full gdaladdo passes for continuous RGBA, `cog_writer.py:645-657`), then `gdal_translate -of COG` re-reads and re-encodes everything a final time. No `NUM_THREADS`, no `PREDICTOR` (2 for uint8 RGBA, 3 for float32 value COGs — typically 20–40% smaller value artifacts), default ZLEVEL 6 everywhere.

Fix (S–M): intermediate `base.tif` → `COMPRESS=NONE` (it's deleted anyway); final translate adds `-co NUM_THREADS=ALL_CPUS -co PREDICTOR=...`; optionally ZSTD if deployed GDAL supports it. Largest win on MRMS-scale (4609×8238) grids. No dataset-handle leaks found — all rasterio opens are context-managed.

### 3.2 HIGH — Forecast hours build serially per variable

`scheduler.py:2285-2397` (normal catch-up branch): `_submit_single` keeps exactly one in-flight fh per (region, var); the next fh submits only when the previous completes. Within a round, concurrency = number of distinct variables regardless of `workers` — a 2–3-variable model uses 2–3 of 4 workers on a 240 h run; a single-var model is fully serial. `fh_lookahead=4` further caps per-round work. The serialization protects the shared per-target `FetchContext`, but non-derived vars (plain fetch→warp) have no cross-fh dependency, and cumulative derives need ordered *completion*, not one-at-a-time execution.

Fix (M): allow 2+ in-flight fhs per target for non-derived vars (readiness cache needs a lock or per-fh keys — see 2.6), or split FetchContext per fh for non-cumulative vars. **Instrument first** (see 3.7).

### 3.3 HIGH — Single range-request failure escalates to a full multi-GB GRIB download, then thrown away

`_download_subset_with_inventory_byte_range` fallback (`fetch.py:3291-3305`) → `_fetch_subset_bytes_from_full_source` (`fetch.py:3046-3066`) → full download (`fetch.py:722`, 90 s per-chunk timeout, no total deadline). There is **no per-range retry** (`fetch.py:3107`), so one transient 500 on a ~2 MB range triggers a full-file download (GFS pgrb2 ~500 MB, EPS enfo multi-GB) to extract one message — and `finally` deletes the temp file with no reuse for the next variable in the same frame, which repeats the download. No size guard; can hold a build slot for the duration.

Fix (M): retry the range request 2–3× with short backoff before the full-file fallback; cap fallback by Content-Length; route the fallback through the EPS full-file cache when enabled.

### 3.4 MED — Byte-range correctness: HTTP 200 passes as a "subset"

`_network_fetch_range_bytes` (`fetch.py:3038-3044`) + `_validate_grib_range_payload` (`fetch.py:3084-3104`): if an origin/proxy ignores the Range header and returns 200, `response.content` is the entire file — which starts with `GRIB`, so validation passes. The payload is written as the subset and rasterio reads band 1 = first message of the file, i.e. potentially the **wrong variable/level rendered on the map**. Also buffers multi-GB `.content` in RAM. `expected_size` (`fetch.py:3169-3177`) only gates cacheability, not correctness.

Fix (S, one line): assert `status_code == 206` (or `len(payload) == expected_size`); raise `_InvalidGribSubsetError` otherwise.

### 3.5 MED — Subset reuse disabled by default; identical subsets re-downloaded

With the disk lock env off (default, `fetch.py:2672`), cached-GRIB reuse exists only in the locked branch (`fetch.py:3634-3652`); the default branch calls `H.download(..., overwrite=True)` (`fetch.py:3785`). Identical subsets are fetched multiple times per run (UGRD/VGRD shared across wspd/barbs, accumulation loops, invalid-subset retries). `BundleFetchCache` covers only the byte-range paths.

Fix (S): decouple cache-reuse from the locking flag — `_subset_file_status` check + `overwrite=False` in both branches.

### 3.6 MED — Full RGBA colorization computed per frame, then discarded

`pipeline.py:1781-1785`: `_, colorize_meta = float_to_rgba(display_data, ...)` — the `(4, H, W)` uint8 array is thrown away; only legend metadata is kept. Tens of MB of allocation + a full-grid colorize pass per frame (×51 in member passes), feeding native-heap churn.

Fix (S–M): metadata-only path (`colorize_meta_for(color_map_id, var_key)`).

### 3.7 MED — No per-phase timings inside `build_frame`

`pipeline.py:1632/1666/1728/1761/1774/1841` log "Step N/6" with no durations and mostly no model/var/fh, so interleaved thread logs are unattributable. Only whole-frame `elapsed_ms` and completion-only run duration exist. "Is it fetch, warp, or COG-write?" is unanswerable from prod logs — which is exactly what sizing 3.1/3.2/3.6 needs.

Fix (S): per-step `perf_counter` deltas in the existing step logs + include model/var/fh. **Do this before 3.2.**

### 3.8 MED — Contour and pressure-center paths independently re-fetch/re-warp the same component

`_build_contour_metadata_for_variable` (`pipeline.py:194-391`) and `_build_pressure_center_metadata_for_variable` (`pipeline.py:414-614`): both resolve the same component (center falls back to `contour_component`), and each does its own `fetch_variable` + `warp_to_target_grid` when the warped-component cache misses. The cache is only consulted when `derive_target_grid_id` is non-empty, so plain mslp/height contour vars fetch+warp twice per frame. The contour warp also hardcodes `resampling="bilinear"` (`pipeline.py:319`) while requesting `contour_resampling` from the cache — key-mismatch risk.

Fix (M): compute the warped component once in `build_frame` and pass to both helpers, or populate the cache on miss.

### 3.9 MED — Failed frames re-attempted every 60 s forever, no backoff or cap

`blocked_targets` is per-`_process_run` state (`scheduler.py:2386-2388`, `2463-2464`); the incomplete-run poll (`INCOMPLETE_RUN_POLL_SECONDS=60`, `scheduler.py:2860-2868`) starts each cycle with fresh state and re-fetches/re-warps/re-fails the same poison frame every minute until the run is superseded (up to ~6 h at GFS cadence). Also pins the fast 60 s poll by keeping the run "incomplete". Contrast: SLR rebuilds carry `rebuild_max_attempts=2`.

Fix (M): persist per-(run, var, fh) failure counts across `_process_run` calls with a cap or exponential backoff; distinguish deterministic from transient failures.

### 3.10 MED — Progressive publish re-copies the entire run tree per snapshot

`_promote_run` (`scheduler.py:1341-1368`) called on first promote then every ≥4 new frames (`scheduler.py:2503-2509`): copytree(published→tmp, hardlink) + copytree(staging→tmp overlay) + rmtree + move — O(total frames) work to publish 4 new frames, dozens of times per run. Multiplies the 2.1 404-window count; heavy inode churn on ensembles.

Fix (M): incremental promote — frame files are immutable once written, so additive in-place hardlinking is safe; at minimum keep the 2.1 rename-swap.

### 3.11 MED — Herbie-internal network calls have no timeout control

`Herbie(...)` construction, `H.index_as_dataframe` (`fetch.py:1378`), `H.download` (`fetch.py:3671/3785`) use Herbie's internal requests with no timeout wrapper; the inventory in-flight follower wait is bounded by `max(5.0, inventory_cache_ttl)` = **600 s default** (`fetch.py:1367`). A hung remote read blocks a build slot up to 10 minutes. This file's own requests calls are covered (45/90 s) — the gap is exclusively the Herbie surface.

Fix (M): run Herbie calls under a deadline; cap the follower wait at 60–90 s independent of cache TTL.

### 3.12 MED/LOW — smaller perf items

- **No `requests.Session`/connection pooling anywhere** (`fetch.py:3040`, `1181`, `725`): every range request is a fresh TCP+TLS handshake; EPS pf-mean = ~51 ranges per variable per fh. Fix (S): module-level pooled Session sized ≥ range workers.
- **`np.savez_compressed` on every cumulative frame's hot path** (`derive.py:701`): zlib on a CONUS float32 grid ≈ 100–300 ms/frame × 4 strategies. Fix (S): uncompressed `np.savez` or zstd.
- **Full-grid unicode ptype arrays** (`derive.py:4840-4841`, `2140-2141`, `2451-2452`): `np.array(["ice","snow","rain"])[idx]` ≥ 20 B/px (~38 MB transient on HRRR) + 3–4 full-grid string scans. Integer codes are ~5× smaller and faster.
- **float64 promotion on climatology-grid warps** (`derive.py:2996`): `raw_data.astype(np.float64)` per component per fh; the generic warp path doesn't do this.
- **Repeated inventory round-trips on cache miss** (`derive.py:3563`, `1775`): a full rebuild at fh240 issues ~40 sequential network inventory calls before any data fetch. Batch per (run, product) into the ctx.
- **`_fetch_inventory_index_text` downloads the idx twice** (`fetch.py:1311-1316` sniffs, then the parser re-fetches) — ECMWF `.index` files are multi-MB.
- **Kuchera rebuild precheck downloads full GRIBs and discards them** (`scheduler.py:787-826`): up to 5 levels × (temp+rh) full `fetch_variable` calls before each SLR rebuild; use idx probing instead.
- **Memory-audit instrumentation logs at INFO unconditionally** (`_log_fetch_context_memory`, per-strategy entry/exit + per APCP step, `derive.py:3841`): O(cache entries) × steps × frames; gate behind `CARTOSKY_FRAME_MEMORY_AUDIT`.
- **Frontier re-scan stat storm** (`scheduler.py:2126-2145`, `1407-1417`): thousands of stats per 60 s poll for ensembles; harmless on SSD, measurable on network filesystems.
- **Member pending/promote scans** stat+JSON-parse every expected frame (~2.8k for GEFS) each scheduler poll (`members.py:1305-1349`).
- **Parallel pf prefetch holds all ~51 range payloads in memory simultaneously** (`fetch.py:1801-1811`) before writing — tens-to-hundreds of MB spikes; stream to disk with a bounded window.
- **members.py warps at default `working_dtype=float64`** (`members.py:979,1066,1220,1238,1254`): pass float32 at member call sites; cap GDAL cache for the pass (see 2.3).
- **Fixed 0.6 s retry sleeps, no jitter/backoff** in all four fetch retry loops.
- **Colorize `transpose().copy()` doubles the RGBA transient** (`colorize.py:179/242/301`, ~145 MiB extra at MRMS scale); already mitigated for MRMS via `colorize_metadata()`. The LUT approach itself is good (256-entry, no per-pixel work).

---

## 4. Robustness & latent hazards

### 4.1 HIGH — Cumulative member scheduling assumes derived fhs align with step_hours multiples

`members.py:508`, `1026-1071`, `1146-1177`. Two unguarded failure modes: (a) a scheduled derived fh that isn't a `step_hours` multiple is never in `step_fhs`, so `_bundle_fields_for_fh` never requests it — the frame is never written and `member_pass_pending` (`members.py:1321`) stays True forever; the scheduler re-runs the pass indefinitely. (b) Resume picks `base_fh = prior_steps[-1]` from the **step grid**, not from scheduled/written frames; sparser cumulative scheduling (e.g. 24 h vars on 6 h steps) → `_decode_member_frame` hits a missing file → `STATUS_ERROR` every pass, member permanently wedged. Latent only because GEFS schedules happen to align. Fix (S) before Tier-2 percentiles: plan-time validation in `build_member_plan` that scheduled derived fhs ⊆ step_fhs; rebase from the last complete *scheduled* frame.

### 4.2 MED — pf band→member mapping guards don't pin the actual invariant

`members.py:722-751`: the index-derived mapping is correct only if `_download_subset_with_inventory_rows` (fetch.py) writes unique byte ranges sorted by (start,end) AND GDAL exposes bands in file order. The count/uniqueness validations would still pass if a future fetch.py change reorders writes — silently relabeling all 50 EPS members. Fix (S–M): cross-check each band's perturbation number from GRIB band metadata (`GRIB_PDS_TEMPLATE_NUMBERS`/`GRIB_IDS`) against the derived number, or pin fetch.py's sort contract with a test.

### 4.3 MED — EPS pf-mean can silently average fewer than 50 members

`fetch.py:1827-1828`: an empty local-read payload is `continue`d; `_aggregate_grib_subset_mean` counts whatever bands exist; `meta["member_count"]` is recorded (`fetch.py:2075`) but never validated against the expected pf count (EPS = 50). A partial subset yields a plausible but wrong mean. Fix (S): compare `member_count` to `len(pf_inventory)`, raise on mismatch. (Band subsetting itself is correct — only pf rows' ranges are fetched; the 51-band cost is aggregation read, not over-download.)

### 4.4 MED — EPS mean fetchers have a fourth, divergent copy of the retry loop

`_fetch_ecmwf_pf_mean_variable` (`fetch.py:2080-2083`) and `_fetch_ecmwf_direct_mean_variable` (`fetch.py:2288-2291`): bare `except Exception` → sleep → retry, no transient/permanent classification, no idx negative-cache, no jitter; `direct_mean_or_pf_mean` (`fetch.py:2293-2311`) then repeats the whole budget in pf-mean. This is the 4th copy of the priority/retry walk (alongside `fetch_variable`, `inventory_lines_for_pattern`, `product_hour_has_any_idx`), each with different semantics — the drift is the incident-generator. Fix (M): reuse `_is_*_error` classification + negative cache; extract one shared priority-walk helper.

### 4.5 MED — wgrib2-style idx: last message in a file is unfetchable via byte ranges

`fetch.py:1305-1306`: the last record gets no `end_byte`; `_inventory_row_byte_range` returns None (`fetch.py:1678-1680`) and the row is silently skipped (`fetch.py:1767-1770`). A variable that is the final GRIB message deterministically fails byte-range → escalates to the 3.3 full-file path or hard failure. Fix (S): emit open-ended `Range: bytes={start}-`.

### 4.6 MED — `.part` download path is deterministic and unlocked by default

`_download_full_grib_to_path` (`fetch.py:724`): `out_path.with_suffix(".part")`; the guarding `_path_download_lock` is a no-op with the lock env off (default). Concurrent writers interleave into the same `.part`; the size check (`fetch.py:741`) misses equal-size interleavings. Also unbounded total download time. Fix (S): unique temp name + atomic `replace`; wall-clock deadline. When the lock IS enabled, the 8 s lock timeout (`fetch.py:2712`) is far shorter than a multi-GB download held under it — spurious `TimeoutError`s for waiters.

### 4.7 MED — `np.to_numeric` doesn't exist; pf member sorting is dead code

`fetch.py:2002`: raises `AttributeError` on every call, swallowed at `fetch.py:2004`. Members aggregate in raw inventory order — harmless for a mean, but if this block is copied into the Phase-3/4 member/percentile pipeline (where order matters) it silently misorders members. Fix (S): `pd.to_numeric`.

### 4.8 MED — `COPY_SRC_OVERVIEWS=YES` is not a COG-driver option

`cog_writer.py:681`: it's a GTiff option; the COG driver ignores it. The pipeline works only because the COG driver's default `OVERVIEWS=AUTO` happens to reuse existing overviews. Pin with `-co OVERVIEWS=FORCE_USE_EXISTING`, else a GDAL default change silently regenerates overviews and destroys the per-band nearest/average policy.

### 4.9 MED — Dark halos on continuous RGBA overviews

`cog_writer.py:644-657` + `colorize.py:176`: colorize zeroes RGB where alpha==0; overview averaging pulls edge RGB toward black while alpha stays nearest → opaque near-black fringe on every continuous layer's zoomed-out tiles. Fix (M): average alpha too (thresholded), or average premultiplied and unpremultiply.

### 4.10 MED — Discrete colormap specs never validate `len(colors) == len(levels)-1`

`colorize.py:248-257`: digitize+clip silently absorbs a mismatch (top bins collapse into the last color; `legend_stops` zip truncates and diverges from the render). The tmp850_anom ladder is verified correct, but the next hand-built ladder has no guard. Fix (S): one-line assertion.

### 4.11 MED — Failure cleanup deletes the variable's whole shared contours dir

`build_frame` binds the contours *directory* into `contour_geojson_path` (`pipeline.py:391`), and `_cleanup_artifacts` (`pipeline.py:2198-2212`) deletes directories recursively — a failure after contour generation deletes all previously built fhs' contour geojsons for that var in staging. Mitigated by published copies surviving, but a staging/published divergence trap. Fix (S): return/clean only the per-fh geojson path.

### 4.12 LOW — additional items

- **Exception classification by exact string match** (`fetch.py:2583-2626`): a Herbie/GDAL version bump silently reclassifies transient→non-transient. Pin versions or match exception types.
- **Uncleaned temp artifacts**: `/tmp/twf_subset_*` (`fetch.py:2642-2648`), `eps_subset_fallbacks` (`fetch.py:602-612`), `.cartosky_pf`/`.cartosky_em_fhNNN` subsets (`fetch.py:1734-1736`) have no TTL/cleanup (only the EPS full-file cache is swept). Disk creep.
- **`_decode_member_frame` hardcodes `<u2`** (`members.py:1013-1023`) while computing `packing_dtype` for the path; a uint8-packed member var fails loudly at reshape. Align with the decode-authority dtype branch.
- **Precheck "fail open"** for `idx_empty`/`pattern_missing`/`no_inventory` (`fetch.py:3596-3606`) proceeds to a doomed download+fallback+sleep per priority. Deliberate for progressive publishes; add a per-reason cap.
- **~130 lines of retry/backoff/lock scaffolding duplicated** between `_fetch_member_bundle` and `_resolve_pf_subset` (`members.py:582-690` vs `754-893`), mirroring fetch.py's mean path — a retry-policy change must land in 3 places. (The cumulative step-math duplication vs derive.py is deliberate and parity-pinned by test — fine.)

---

## 5. Structure & maintainability (derive.py)

**Architecture is sound at the top**: `DERIVE_STRATEGIES` registry (`derive.py:7247-7350`, 16 strategies, all live — no dead strategies) dispatched by `derive_variable()` (`derive.py:721-755`). Coupling is one-directional (derive → fetch; fetch never imports derive; pipeline imports 5 symbols, scheduler 2). The rot is *inside* the strategy implementations.

### 5.1 Incremental-cumulative skeleton duplicated 5×, with shipped behavioral divergence

`_derive_precip_total_cumulative` (`derive.py:5293-5514`), `_derive_snowfall_total_10to1_cumulative` (`5610-5897`), `_derive_ptype_accumulation_cumulative` (`7004-7244`), `_derive_ptype_accumulation_ecmwf` (`5096-5255`), `_derive_snowfall_kuchera_total_cumulative` (`6113-6812`, bespoke `while True` variant). Each repeats the same ~120–150-line skeleton (prior-load → seed → loop → ValueError full-rebuild retry — copy-pasted 3× inside precip_total alone: `5375-5395`, `5413-5431`, `5448-5466` — → mismatch check → NaN-aware merge → store).

**Shipped divergence**: on incremental base-grid mismatch, precip_total retries a full rebuild (`5437-5476`) while snowfall_10to1 (`5854-5862`) and ptype accumulation (`7204-7212`) fail the frame; Kuchera has a fourth behavior (while-loop restart, `6754-6769`). Same failure, four behaviors.

Fix (L, behind per-model parity canaries per Phase G practice): extract `_run_incremental_cumulative(...)` taking a `process_step` callback (the pattern `_cumulative_apcp_loop` already proves). ~500–600 lines removed; every future incremental fix applied once instead of 5×.

### 5.2 Mechanical dedup (~400 lines, near-zero risk)

- **Warped-vs-raw fetch plumbing hand-rolled in 4 strategies** despite `_fetch_step_component` (`derive.py:3167-3197`) existing: `_derive_wspd10m` (`4184-4266`, twice), RH from dewpoint (`4386-4433`), RH from specific humidity (`4465-4512`), vort500 (`4590-4633`). Also re-derive `_resolve_warped_state` inline. Pure mechanical substitution, ~200 lines.
- **Ptype index-binning + palette tables duplicated verbatim**: `_ptype_intensity_index_from_family_rates` (`2126-2183`) vs `..._from_gfs_family_rates` (`2438-2498`) — identical tables mirroring frontend palette offsets (rain 0–15, snow 16–25, ice 26–43); a palette change must now be edited in two places (three counting `colormaps`) and drift renders wrong colors with no error. Delete the GFS copy, hoist tables to constants.
- **Sample-mask averaging triplicated** (`5736-5786`, `7098-7141`, `2688-2753`) + duplicated interval-plan builders (`5578-5608`, `6010-6039`, `6963-6981`) + twin log throttlers (`4089-4134`) + **byte-identical duplicate pruners** (`_prune_cache_dict_by_forecast_hours` vs `_prune_kuchera_cumulative_cache`, `197-226`).

### 5.3 Dead/misleading elements

- `DeriveStrategy.required_inputs` / `output_var_key` (`derive.py:296-301`): never read anywhere in the repo — 16 strategies carry unverified metadata that looks load-bearing. Enforce or delete. (Enforcing would also absorb pipeline's one strategy-specific special case, the Kuchera readiness check at `pipeline.py:1378`, as a `required_products` field.)
- `_kuchera_load_prior_cumulative` `scale_divisor` is `del`'d immediately (`derive.py:579-583`) while 5 call sites pass meaningful-looking values. Delete the parameter.
- `_derive_ptype_accumulation_cumulative` unpacks the prior-cache tuple directly (`7040-7041`) instead of via `_unpack_kuchera_cumulative_cache_entry` like its 3 siblings — breaks when the entry format changes again (it already went 3→4 fields once).
- Dead double-raise in `_derive_wspd10m` (`4263-4266`).
- **~200 lines of dead loop-pregeneration plumbing in scheduler** (`scheduler.py:1737-1746` — 8 `loop_*` params unused; `1940-1941` `del pregenerate_loops`; `1782-1788` prewarm fhs computed, never consumed) while `DEFAULT_LOOP_PREGENERATE_ENABLED = True` misleads operators. Loop WebP cache is evidently on-demand now — first viewer pays generation latency. Re-wire or delete.
- **`rebuild_existing` parallel branch is unreachable** (`scheduler.py:2728-2733` forces `workers=1`, so the ThreadPool branch at `2081-2118` — which submits with *no* fetch_ctx/readiness_cache — can't run). Delete or align.

### 5.4 Coupling wrinkles

- **Stringly-typed grid-id contract**: pipeline builds `"climatology:{source}:{region}:{grid_m:.1f}m"` (`pipeline.py:1467`) and derive parses it back (`derive.py:2981-2992`); a format tweak on either side silently falls through to the generic warp path.
- **Duck-typed FetchContext**: `data_root` and `kuchera_cumulative_cache` are not dataclass fields; pipeline injects via `setattr` (`pipeline.py:1531`), derive reads via defensive `getattr` at ~10 sites. Declare them as fields; removes ~30 lines of guards.

### 5.5 Verified sound (orchestration)

- Scheduler-overlap protection is real: per-model `fcntl` lock (`scheduler.py:1371-1404`).
- Manifest/LATEST ordering within one publish is correct: frames → manifest → LATEST pointer last (`scheduler.py:1940-1980`); manifest lists only fhs whose sidecars exist. JSON writes are atomic (tmp→rename).
- Transient-vs-failed distinction exists; transient targets pause rather than block.
- `BundleFetchCache` leader/follower dedup is correct (event set in `finally`; errors propagate; invalid entries evicted).
- EPS statistics-file URL rewrite is anchored and step-filtered with an exactly-one-record assertion (`fetch.py:2098-2232`).
- Output nodata handling is correct: RGBA gets alpha-0, value COGs use NaN, value-grid overviews use nearest (safe for sampling).
- Note: no percentile code exists anywhere in `builder/` — GEFS/EPS means are fetched as upstream ens-mean products; Tier-2 percentile correctness is N/A until implemented (fix 4.1/4.2 first, since percentiles would consume member frames).

---

## 6. Test-coverage gaps

Strong existing coverage: Kuchera (SLR formula, cumdiff, windows, incremental-vs-full parity, ptype gate, surface cap, pressure mask), GFS/NAM/NBM inventory differencing and overcount prevention, GFS snowfall/ice, GFS+ECMWF ptype-intensity classification, RH Magnus, precip anomaly windows/units, GEFS fractional csnow.

Gaps mapping to findings:
- No ECMWF ptype test in warped-component mode (would catch 1.1)
- No `step_fhs[-1] == fh` / off-cadence fh test (1.4)
- No missing-mid-step accumulation test asserting NaN/flag semantics (1.3 — the existing test covers csnow skip only, not APCP-step loss)
- No `_normalize_ptype_probability` percent/fraction boundary test (1.6)
- No NaN-in-categorical-mask radar test (1.8)
- No cross-strategy step-validity consistency test (1.9)
- No assertion that component snow planes are physically scaled (1.5)
- Ptype accumulation with fractional ensemble masks untested (1.7)
- No test pinning fetch.py's byte-range write-sort contract for member band mapping (4.2)
- No discrete-spec `len(colors) == len(levels)-1` validation (4.10)

---

## 7. Recommended sequence

**Quick wins, high impact (all S effort):**
1. ECMWF ptype warp-params fix + warped-mode test (1.1 — wrong ice/rain classification in prod today)
2. Publish rename-swap (2.1) + manifest eviction (2.2) — kills the 404 incident class
3. `GDAL_CACHEMAX` (2.3) + invert prune allowlist (2.4) + float32 member warps — the swap incident
4. Scheduler `request.model` fix + internal-id guard in `fetch_variable` (2.5) — July 6 incident class
5. HTTP 206 assertion (3.4), pf member-count validation (4.3), `step_fhs[-1] == fh` assertion (1.4), quality-flag threading for fail-open fallbacks (1.2)

**Medium projects:**
- COG encode overhaul (3.1)
- Per-step build timings (3.7), then fh-level parallelism (3.2)
- Range retry before full-file fallback + subset reuse (3.3, 3.5)
- Readiness-cache fh keying (2.6); failed-frame backoff (3.9); member scheduling validation (4.1)

**Larger refactor (behind per-model parity canaries):**
- Extract the incremental-cumulative orchestrator (5.1) — ~1,000 lines of dedup across 5 strategies including the mechanical items (5.2), eliminates the shipped mismatch-handling divergence.
