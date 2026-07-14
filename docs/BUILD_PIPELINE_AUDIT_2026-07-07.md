# Build Pipeline Audit — 2026-07-07 (last re-verified 2026-07-09)

Scope: the builder pipeline under `backend/app/services/builder/` — `derive.py` (7,350 lines, two lenses: numerical correctness and structure/performance), `pipeline.py` + `services/scheduler.py` (orchestration), `fetch.py` (data acquisition), and the output stages (`cog_writer.py`, `colorize.py`, `members.py`). ~16.5k lines total, read in full by five parallel audit passes. Cross-reviewed by an independent second pass (Codex, 2026-07-07); its corrections — prune-allowlist scope (2.4), ptype component exposure qualifier (1.5) — are incorporated. Revised 2026-07-07 after operator review: COG-writer findings removed (the pipeline is migrating off COGs to sole binary sampling, with only a few models/products remaining), and the `GDAL_CACHEMAX` finding revised after confirmation that prod scheduler units set it (see 2.3).

**TLDR:** The derive dispatch architecture is clean and the core math (Kuchera SLR, unit conversions, APCP differencing) verified correct — but there is one confirmed high-severity data bug (ECMWF ptype thermal signals silently zero out in warped-component mode, making ice storms render as rain), a cluster of "silently wrong frame" failure modes that ship as full quality, and structural explanations for two known prod incidents: the viewer 404s (non-atomic publish swap + manifests never evicted) and the EPS memory/swap issue (memory-prune allowlist skipping two of the heaviest derive strategies + float64 member warps + RAM-buffered full-file/pf downloads; the GDAL block cache is bounded to 256 MB in prod via systemd env, though only as undocumented drift from the repo's unit templates). There is also a live instance of the same model-id-leak class that caused the July 6 eps/ifs outage.

Severity legend: **HIGH** = wrong data shipped or prod-incident cause; **MED** = latent correctness trap, meaningful perf/build-time cost, or divergence risk; **LOW** = cleanup, minor cost, or informational.

**Progress (2026-07-07):** the first four items of the quick-wins list (§7) are implemented, tested, and verified against the unfixed code (each new test confirmed to fail pre-fix): 1.1 (ECMWF ptype warp params), 2.6 (readiness-cache `fh` keying), 3.3 (HTTP 206 range guard), and 2.1 + 2.2 (publish rename-swap + manifest eviction). Findings below are left as originally written for the record; each fixed one is marked `STATUS: FIXED`.

**Progress (2026-07-10):** quick-win #5 done — 1.2 (quality-flag threading for fail-open fallbacks: `ptype_gate_fallback`, `phase_signals_missing`, `snow_component_missing`; logs promoted to warning; fallback data semantics deliberately unchanged and now pinned by tests). Five new tests, each confirmed to fail pre-fix. Incidental find while regression-sweeping: `test_derive_bundle_cache.py::test_derive_bundle_reuses_fetch_and_warp_cache` fails on unmodified main (inventory-step expectation, pre-existing, unrelated to 1.2; fixed the same day in a spun-off session). Quick-win #6 also done the same day — 2.4 prune-allowlist inversion, 3.11 float32 member warps, 2.3 `GDAL_CACHEMAX` template codification; `MALLOC_ARENA_MAX=2` intentionally left for its own measured canary. Quick-win #7 done as well — 2.5 model-id leak: precheck now uses `request.model`, and `eps`/`ecmwf` are rejected at all three public Herbie-facing fetch entry points (see 2.5).

**Live-log validation (2026-07-08):** a real in-progress EPS (`ifs`) run's scheduler logs were reviewed against this audit, then cross-checked against ECMWF's real public feed (`data.ecmwf.int`). Findings 3.1 and 4.4 were confirmed, sharpened, and extended — no new bugs were found, but several mechanisms were pinned down precisely with first-hand data: the catch-up round is a hard barrier (a fast variable's worker sits idle waiting for the slowest variable in the same round, not just capped at one in-flight fh); only one of ~13 EPS mean variables (`hgt500__mean`) ever attempts the cheap precomputed-mean path, and it fails on every observed hour of a still-in-progress run — **confirmed** (not hypothesized) to be expected behavior, since ECMWF publishes that statistics product as a single bundle per horizon only once the run substantially completes, not incrementally; and `tmp850__mean` is a missed candidate for the same cheap path once available.

**Full re-verification pass (2026-07-09):** every citation into `fetch.py`, `scheduler.py`, and `members.py` was re-checked against current disk state, since 27 commits (659 lines across those three files) landed after this doc was first written — none of them fixes to findings here, all unrelated/adjacent feature work. `derive.py`, `pipeline.py`, `cog_writer.py`, and `colorize.py` were untouched by any of those commits, so every citation into those files below is still exact. Outcome of the re-check:
- **Line numbers updated throughout** §2–§5 to match current file state (drift ranged 5–300+ lines depending on how much intervening code landed above each citation).
- **Two items resolved by unrelated commits**, not by this audit's own fixes: the `_fetch_inventory_index_text` double-fetch bullet in §3.11 (fixed — now a single fetch+parse), and a related-but-distinct eccodes `end_byte` off-by-one bug (fixed by `636c3573`/`37fb767b`, "fix: ... end byte ... eccodes-style inventories" — see the note under 4.5; this is very likely our own 3.3 fix's strict payload-size check surfacing a real pre-existing bug that then got fixed properly).
- **Finding 4.1 was deep-dive re-verified** against `675a5883` ("feat: implement mean coverage cap and backfill logic for member frames", 2026-07-08), the one commit that substantially touched the exact area 4.1 describes. **Verdict: still fully open, both failure modes unresolved** — see 4.1 for the detailed reasoning; the new commit solves an unrelated problem (superseded-run mean recovery) and, if anything, slightly widens 4.1's exposure surface rather than closing it.
- Everything else: confirmed unchanged in behavior, citations refreshed.

**Independent review (Codex) + corrections, 2026-07-09.** A second review of this doc found three real staleness/overclaim issues the drift-check pass above missed (it checked *modifications* to already-known files, not *new files*), and offered reasonable process suggestions on two more. Corrected:
- **§5.5 was wrong, not just stale.** "No percentile code exists anywhere in `builder/`" is false — `backend/app/services/builder/stats.py` (added by `8db4b298`, 2026-07-08) implements the Tier-2 percentile/probability pipeline and is wired into the scheduler (`_maybe_run_stats_pass`). Per this session's own memory of an earlier audit pass, it passed rollout gates 6A–6C and is **live in production on GEFS+EPS with a viewer Product selector already exposing it to users** — not a future/hypothetical dependency. See the corrected §5.5 note and 4.1 below.
- **§4.1's severity is kept HIGH, not downgraded to MED as suggested**, for a reason neither review had before this check: `stats.py` has a hard per-fh completeness gate (`_roster_complete`, every member frame must exist before any percentile is computed) that protects against 4.1 producing a *wrong* percentile — but a member permanently wedged by 4.1's failure mode (b) means that member's frames stop being written for every fh past the wedge point, so the roster never completes again, and the stats pass silently and permanently stops producing percentile/probability frames for that variable for the rest of the run. That's a live, user-facing degradation path today, not a latent internal one. See 4.1 for the full reasoning.
- **§2.1's fix is not fully atomic** — corrected. Between the two `os.rename` calls (`scheduler.py:1378`, `1380`) there is a genuine (though microseconds-scale) window where `published_run` doesn't exist. "Kills the 404 incident class" overclaimed; softened throughout to "sharply reduces" with the residual window called out. If 404s are still observed after this fix ships, the next step is a fully atomic pointer/symlink indirection rather than in-place directory renames.
- **§6 had a stale test-coverage gap** — "No ECMWF ptype test in warped-component mode" — even though 1.1's own status note says that test was added. Removed.
- **§2.4 / §2.3 process note adopted:** the causal link from the prune-allowlist gap to the actual observed EPS swap incident is plausible, not measured — no RSS/cache evidence ties them together yet. `MALLOC_ARENA_MAX=2` is now explicitly framed as a controlled canary to test in isolation, not a change to bundle blindly into the same pass as the prune-policy fix.
- **Not adopted as new findings:** the production `GDAL_CACHEMAX` confirmation, live-log timing analysis, and ECMWF-feed observations in this doc are based on operator-reported config and this session's own direct log/network verification (see the 2026-07-08 callouts above) — they're accurate as recorded, just (correctly, per the reviewer) not re-derivable from the repository alone.

---

## 1. Data-accuracy findings (derive logic)

### 1.1 HIGH — ECMWF ptype thermal signals silently drop to zero in warped-component mode

**STATUS: FIXED 2026-07-07.** `_ptype_intensity_ecmwf_phase_signals` now accepts and forwards `use_warped`/`target_region`/`target_grid_id`/`resampling` to its component fetches, matching the GFS path; both call sites (intensity + accumulation) pass their in-scope warp state through. New test `test_ecmwf_ptype_intensity_uses_warped_component_fetches_when_requested` in `backend/tests/test_ecmwf_ptype_intensity_derive.py` asserts every thermal/precip fetch carries the warp params and that a cold/no-sf profile classifies as snow (confirmed to fail against the pre-fix code).

`_ptype_intensity_ecmwf_phase_signals` (`derive.py:2031-2057`, called from `derive.py:2267-2276` and `derive.py:5174-5183`) fetches tmp2m/tmp925/tmp850 **without** the warp parameters (`use_warped`/`target_region`/`target_grid_id`/`resampling`), while the precip/snow steps it combines with *are* warped. The temperature grids come back native-shape, fail the `values.shape != expected_shape` check at `derive.py:2041`, and are silently skipped — `deep_cold`, `surface_cold`, `warm_nose` all become zeros (`derive.py:2055-2057`).

Consequence: classification in `_ptype_intensity_family_rates_ecmwf` (`derive.py:2100-2110`) reduces to `snow_frac`-only. Freezing rain/sleet (`ice_mask` requires `surface_cold >= 0.45 & warm_nose >= 0.35`) can **never** be produced — an ice storm renders as plain rain, and ECMWF ice accumulation totals ≈ 0. The GFS counterpart (`derive.py:2573-2586`) forwards warp params correctly; this is copy-paste divergence.

Fix (S): thread the four warp params through, exactly as `_ptype_intensity_thermal_fields` does for GFS; add a warped-mode ECMWF test mirroring `test_ptype_intensity_uses_warped_component_fetches_when_requested`.

### 1.2 HIGH — Fail-open fallbacks produce confidently wrong frames with no quality flag

**STATUS: FIXED 2026-07-10** (quality-flag threading; fail-open data behavior intentionally unchanged — see note). All three paths now record degraded quality via the existing `_record_derive_quality` mechanism and log at warning level:
- Kuchera: the ptype-gate fallback flag is no longer discarded at the call site — it accumulates across steps (mirroring `apcp_cumulative_fallback_used`) and ships as `ptype_gate_fallback` in `quality_flags`.
- ECMWF phase signals: `_ptype_intensity_ecmwf_phase_signals` now returns the list of missing thermal components; any missing component (not just total failure — a missing surface-temp component alone silently kills ice classification) flags `phase_signals_missing`. The per-component debug swallow in `_ptype_intensity_fetch_optional_component` is promoted to a warning with the failure reason (this also benefits the GFS thermal path, log-level only).
- ECMWF snow-component fallback: both the intensity and accumulation copies flag `snow_component_missing` and log at warning (previously debug).
- Both ECMWF ptype intensity strategies and the ECMWF ptype accumulation strategy now always record quality (explicit `full` when clean), flowing through the existing generic `ctx.derive_quality` read in `build_frame`.

New tests (each confirmed to fail against the pre-fix code): `test_kuchera_ptype_gate_fallback_records_quality_flag` in `backend/tests/test_kuchera_ptype_gate.py`; `test_ecmwf_ptype_intensity_records_{full_quality_when_all_components_resolve,snow_component_missing_quality_flag,phase_signals_missing_quality_flag}` and `test_ecmwf_ice_total_records_quality_flags_when_snow_component_missing` in `backend/tests/test_ecmwf_ptype_intensity_derive.py`. The fallback *data* semantics (Kuchera gate opens to all-ones; ECMWF snow/signals substitute zeros) are deliberately unchanged and pinned by the new tests — failing the Kuchera gate closed instead is a shipped-data behavior change that needs its own decision + parity check, not something to bundle into a flags-only pass. Known limitation (same semantics as the pre-existing `apcp_cumulative_fallback` flag): for cumulative strategies the flags reflect only the steps computed during the current frame's build — contamination carried forward via the prior-cumulative cache is not re-flagged on later frames (that persistence question belongs to 1.3's validity-semantics fix).

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

### 1.5 MED (exposure unconfirmed) — Snow "component" planes carry a hidden 2× display boost

`derive.py:2465-2486`, `2629-2635` (GFS `snow_display = 2.0 * snow_rate` stored as the family's `snow` plane), `derive.py:5039-5041` (ECMWF ×2). The value grid for the `snow` ptype component is 2× the 3-h-equivalent liquid intensity while rain/ice are unboosted, so the three family planes are mutually inconsistent.

**Severity qualifier:** the `ptype_intensity_*` component vars are marked `internal_only` / `buildable=False` in both `gfs.py:1233-1236` and `ecmwf.py:1205-1208`, so they are not directly user-selectable. However, they are built as companions of `ptype_intensity` (`companion_vars`/`composite_layers` hints) with packing configs, and per the canary-script findings buildable=False does not imply unpublished — confirm whether these planes are exposed via the sample/binary-sampling API before treating this as a user-facing value bug. If they are only ever consumed for compositing/display, the remaining issue is the maintenance trap, not wrong data.

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

**STATUS: FIXED 2026-07-07, but not fully atomic** (correction 2026-07-09, per independent review). `_promote_run` now swaps via two `os.rename` calls (published → `.trash` at `scheduler.py:1378`, tmp → published at `1380`) instead of `rmtree` + `move`; if the second rename fails, the previous published run is restored before the error propagates. This cuts the exposure window from seconds (a full-tree rmtree) to the gap between two syscalls — but that gap is real: between the two renames, `published_run` does not exist, so a reader resolving frames in that exact microsecond window can still 404. New tests in `backend/tests/test_scheduler_promote_retention.py`, including `test_promote_run_replaces_existing_run_via_rename_swap` (confirmed to fail against the pre-fix code, which rmtree'd the live published dir in place) and `test_promote_run_restores_previous_run_when_swap_fails`. If 404s are still observed in prod after this fix, the next step is a fully atomic pointer/symlink indirection (publish writes a new target, then atomically repoints one symlink) or `renameat2(RENAME_EXCHANGE)` rather than two sequential renames.

`_promote_run` (originally `scheduler.py:1363-1368`, function now spans `1341-1386` after the fix below): builds `tmp_run`, then `shutil.rmtree(published_run)` followed by `shutil.move(tmp_run, published_run)`. Between rmtree and move, the published run directory does not exist — and rmtree of a large run tree (thousands of frames for ensembles) can take seconds. Every publish (initial + progress publish every ~4 new frames + member-pass promotes) opens this window. This means the *live* run flickers out on every snapshot — a direct contributor to the viewer-404 incident pattern, alongside stale run ids.

Fix (S): rename-swap — `os.rename(published, trash)` → `os.rename(tmp, published)` → delete trash in background. Two renames = milliseconds of exposure.

### 2.2 HIGH — Manifests are never evicted with their runs

**STATUS: FIXED 2026-07-07.** New `_enforce_manifest_retention` prunes `manifests/<model>/<run>.json` with the same `effective_keep_runs` as staging/published retention, called alongside them in `_process_run`; `LATEST.json` and non-run files are left untouched. Tests `test_enforce_manifest_retention_prunes_only_old_run_manifests` and `test_enforce_manifest_retention_noops_below_keep_count` in `backend/tests/test_scheduler_promote_retention.py`.

`_manifest_path` writes `data_root/manifests/<model>/<run>.json` (`scheduler.py:1087`), but `_enforce_run_retention` (called at `scheduler.py:2571-2572`) prunes only `staging/<model>` and `published/<model>` — the fix below adds a third call at `2573`. Clients (or caches) resolving a run via an old manifest get a valid-looking manifest whose frames were rmtree'd — exactly the documented "stale client-resolved run ids vs backend run retention" incident.

Fix (S): prune `manifests/<model>` with the same `effective_keep_runs` in `_process_run`; optionally return 410 from the frames route for manifest-known-but-evicted runs.

### 2.3 REVISED (was HIGH) — GDAL block cache is bounded in prod, but only via unmanaged unit-file drift

Original finding: nothing in the repo sets `GDAL_CACHEMAX` / `CPL_CACHE` / `SetConfigOption` (repo-wide grep, including the checked-in `deployment/systemd/*.service` templates and env examples). **Operator confirmation 2026-07-07:** the live prod units DO set `GDAL_CACHEMAX=256` in their systemd `Environment=` (verified on `csky-gfs-scheduler` and `csky-eps-scheduler`), so the block cache is bounded at 256 MB per scheduler process — it is **not** the unbounded native-heap suspect.

Two follow-ups remain:
- **Config drift (S):** ~~the cap exists only on the live hosts; the repo's `deployment/systemd/` unit files and `scheduler*.env.example` files don't carry it. A rebuilt or newly provisioned host from the repo templates would silently lose the cap. Codify `GDAL_CACHEMAX` in the checked-in templates (and audit the other scheduler units for the same drift).~~ **DONE 2026-07-10:** `Environment=GDAL_CACHEMAX=256` added to all 17 `deployment/systemd/csky-*-scheduler.service` templates with a comment noting it matches the operator-set prod cap. `csky-api.service` and the canary unit were left untouched — the operator confirmation covered scheduler units only; extending the cap to the API process is a separate decision.
- **Narrowed swap suspects, causal link not yet measured** (caveat added 2026-07-09, per independent review): with the block cache bounded, the remaining native-heap candidates from the EPS memory audit are glibc arena growth, the Python-side prune-allowlist gap (2.4), float64 member warps, and RAM-buffered payloads — full-file `.content` responses (3.2) and the ~51 simultaneous pf range payloads (3.11 bullets). None of these is confirmed against measured RSS/cache evidence to be *the* cause of the original swap incident — they're plausible candidates from code inspection, not a proven root cause. `MALLOC_ARENA_MAX=2` specifically should be tested as an isolated, measured canary (before/after RSS comparison on one host) rather than bundled into the same deploy as the 2.4 prune-policy fix — bundling them would make it impossible to attribute any RSS improvement to either change. The `malloc_trim`-after-runs (`scheduler.py:1737-1750`) and restart-on-success (`RESTART_ON_SUCCESS_MODELS`, `scheduler.py:183`, check at `1773`, invoked at `3148`) mitigations should stay until those are addressed.

### 2.4 HIGH — Memory-prune allowlist silently skips two of the heaviest derive strategies

**STATUS: FIXED 2026-07-10** (audit's option a — allowlist inverted to prune-for-every-derived-kind). `prune_fetch_context_after_frame` now prunes whenever the var spec carries a non-empty `derive` kind; the six-kind allowlist is gone, so `precip_total_cumulative`, `snowfall_total_10to1_cumulative`, the component strategies, and both anomaly strategies are covered, and new strategies default to pruned instead of never-pruned. Safety was verified per strategy before inverting: all ctx caches are fh-keyed memoization; incremental cumulative seeds survive via `keep_fhs={fh}` (the just-stored entry is what the next frame loads as prior) plus the staging-npz disk fallback in `_kuchera_load_prior_cumulative`; `anomaly_departure` touches only the current fh (climatology baselines load from disk, not ctx); `precip_accum_anomaly_departure`'s window endpoints are frame-specific keys, with the disk fallback covering recursive cumulative seeds. Worst case of over-pruning is a disk/npz reload, never wrong data. New tests in `backend/tests/test_fetch_context_lifecycle.py`: `test_prune_fetch_context_after_frame_covers_previously_unpruned_strategies` (parameterized over all seven previously-skipped kinds, each confirmed to fail pre-fix) and `test_prune_fetch_context_after_frame_noops_without_derive_kind` (pins the non-derived gate). The float32 member-warp companion fix (3.11 bullet) and the `GDAL_CACHEMAX` template codification (2.3) landed in the same pass; `MALLOC_ARENA_MAX=2` was deliberately NOT bundled, per 2.3's isolated-canary note.

`prune_fetch_context_after_frame` (`derive.py:237-247`): `handled_derive_kinds` covers `snowfall_kuchera_total_cumulative`, `ptype_accumulation_ecmwf`, `ptype_accumulation_cumulative`, `ptype_intensity_ecmwf`, `ptype_intensity_gfs`, and `radar_ptype_combo` — but **omits `precip_total_cumulative`, `snowfall_total_10to1_cumulative`, the component strategies (`ptype_intensity_component`, `radar_ptype_component`), and the anomaly strategies**. Snowfall 10to1 fetches APCP + csnow for **every step fh** of the run (up to fh384 GFS) into `ctx.fetch_cache`/`warp_cache` and is never pruned between frames — caches grow with run length for two of the heaviest cumulative strategies. Matches the multi-GiB RSS symptoms. Note: Python-side lifecycle is otherwise disciplined (`destroy_fetch_context` clears all caches, per-frame prune for listed kinds, RSS checkpoints) — consistent with the prior audit's "leak is native" conclusion, but this allowlist gap is a real Python-side contributor for the omitted cumulative vars.

Fix (S): invert to opt-out (prune for every derived var) or make pruning an explicit per-strategy policy on `DeriveStrategy`; new strategies currently default to "never pruned".

### 2.5 HIGH — Model-id leak class (July 6 eps/ifs incident) has a live instance

**STATUS: FIXED 2026-07-10.** Both halves landed:
- `_component_precheck_available` now fetches with `request.model` from the `plugin.herbie_request(...)` it constructs (the raw `model_id` param is explicitly `del`'d with a comment naming the incident class, matching the codebase's del-idiom rather than cascading a signature change through `_kuchera_rebuild_profile_ready`).
- New guard in `fetch.py`: `INTERNAL_ONLY_MODEL_IDS = frozenset({"eps", "ecmwf"})` + `_reject_internal_model_id`, called at the entry of **all three** public Herbie-facing entry points — `fetch_variable`, `product_hour_has_any_idx`, and `inventory_lines_for_pattern` — not just `fetch_variable` as originally suggested, because the July 6 incident actually flowed through the readiness probe. `ecmwf` is included alongside `eps` because both plugins map to Herbie's `ifs`, verified against the model registry: they are the only two of the 18 plugins whose internal id differs from their Herbie id (base default is `model=self.id`). Precision note from independent verification: the installed Herbie *does* carry `ecmwf` as a deprecated alias for `ifs` (only `eps` hard-errors), so rejecting `ecmwf` closes a drift-prone alias rather than a hard failure. All callers of the three entry points were audited: every primary path passes `request.model` or a literal Herbie id. One live path does fire the guard — ECMWF Kuchera's `_kuchera_inventory_lines(model_id="ecmwf")` — and was empirically shown to be behaviorally identical pre/post-fix: the hardcoded `:APCP:surface:` pattern matches nothing in ECMWF inventories (`tp`/`sf` naming), so the helper returned `[]` before (after a wasted network attempt) and returns `[]` now (immediately, via its existing catch-all).
- **Residual (recorded, not fixed here):** the Kuchera helpers (`_kuchera_inventory_lines`, `_resolve_apcp_step_data`, `_ptype_intensity_fetch_step_intensity`) still receive the raw internal `model_id` rather than resolving via `herbie_request().model`. Benign today only because ECMWF's APCP inventory is empty; if ECMWF Kuchera were ever repointed at an APCP-bearing product, the guard would silently suppress inventory-driven selection behind the bare `except Exception: return []`. Future-proofing fix: resolve the Herbie id at those sites too (fits naturally into the 5.1/5.2 dedup refactor).
- New tests in `backend/tests/test_internal_model_id_guard.py` (all 9 confirmed to fail pre-fix): the three entry points reject `eps`/`ecmwf` (case/whitespace-insensitive), and `test_component_precheck_fetches_with_request_model_not_internal_id` pins that the precheck passes `ifs`, not `eps`, to `fetch_variable`. `_component_precheck_available` (`scheduler.py:792-830`): constructs `request = plugin.herbie_request(...)` then **discards it**, calling `fetch_variable(model_id=model_id, ...)` with the raw caller-supplied internal id. `fetch.py` passes `model_id` verbatim to `Herbie(model=...)` in 5 places (`fetch.py:3780`, `2622`, `2521`, `2342`, `2110`) with no guard. Latent today only because the Kuchera-precheck models (`scheduler.py:861-867`, `872-878`) have internal id == Herbie id — exactly how the eps probe bug stayed hidden until the probe went fail-closed. Corroborating smell: `_eps_full_file_cache_enabled` (`fetch.py:607-614`) defensively accepts both `{"ifs", "eps"}`.

Fix (S): use `request.model`/`request.product` within `scheduler.py:792-830`; add a guard in `fetch_variable` rejecting known internal-only ids (e.g. `eps`).

### 2.6 MED — Readiness-probe cache key omits `fh`

**STATUS: FIXED 2026-07-07.** Both cache-key forms in `_ensure_products_ready` now include `fh` (`{model}|{product}|fh{NNN}` and `{product_name}|fh{NNN}`), so a ready probe at one fh no longer bypasses the fail-closed gate for later hours of the same target; negative results are also now scoped per fh. New test `test_ensure_products_ready_readiness_cache_is_scoped_per_forecast_hour` in `backend/tests/test_pipeline_readiness_gate.py` (confirmed to fail against the pre-fix code). Note: the bare `product_name` cross-sub-model collision risk mentioned below is not addressed by this fix.

`_ensure_products_ready` (`pipeline.py:1412-1428`, unmoved — `pipeline.py` untouched by any commit since this audit): cache keys are `f"{request.model}|{request.product}"` and bare `product_name`; `fh` is not in the key, and the scheduler passes one `readiness_cache` per (region, var) across all fhs (`scheduler.py:2287, 2314, 2344`, one per submission branch). After fh N probes ready, all later hours **skip the fail-closed readiness gate entirely** and fall through to `fetch_variable` failure paths. Same gate class as the 18z EPS readiness incident. The bare `product_name` key can also collide across sub-models.

Fix (S): include `fh` in the cache key (idx presence is not monotonic for models publishing hours incrementally).

---

## 3. Build time & performance

*(A finding on redundant COG encode passes was removed 2026-07-07: the pipeline is migrating off COGs to sole binary sampling, so COG encode tuning is moot.)*

### 3.1 HIGH — Forecast hours build serially per variable, and the round is barrier-synchronized on the slowest target

`scheduler.py:2333-2447` (normal catch-up branch): `_submit_single` keeps exactly one in-flight fh per (region, var); the next fh submits only when the previous completes. Within a round, concurrency = number of distinct variables regardless of `workers` — a 2–3-variable model uses 2–3 of 4 workers on a 240 h run; a single-var model is fully serial. `fh_lookahead=4` further caps per-round work. The serialization protects the shared per-target `FetchContext`, but non-derived vars (plain fetch→warp) have no cross-fh dependency, and cumulative derives need ordered *completion*, not one-at-a-time execution.

**Confirmed and sharpened against live prod logs, 2026-07-08.** The round itself is a hard barrier, not just a per-target cap: `scheduler.py:2254` opens a fresh `with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:` for every catch-up round, and the `with` block's exit implicitly blocks (`shutdown(wait=True)`) until *every* future submitted that round completes — so a variable that finishes early cannot start its next fh until the slowest variable in the same round also finishes. Observed in a live EPS (06z, `20260708_06z`) catch-up round: `tmp2m__mean` finished fh114 in 4.6 s (`Frame timing: ... var=tmp2m fh114 ... elapsed_ms=4618`, 08:48:04.968) but its next build (fh120) didn't start until 08:48:29.665 — idle for ~24 of the round's ~29 s wall-clock — because `pwat__mean`+`rh2m__mean` (chained, ~13.7 s + ~15.5 s) and the `hgt500_anom__mean`+`tmp850__mean` bundle (~26 s, see 4.4) were still running. The next round (08:48:29–08:48:57) repeats the identical pattern. Over a long EPS run this means wall-clock per round ≈ slowest variable's time, not average — a fast variable's worker capacity sits ~85% idle in the observed rounds.

Fix (M): allow 2+ in-flight fhs per target for non-derived vars (readiness cache needs a lock or per-fh keys — see 2.6), or split FetchContext per fh for non-cumulative vars. The round-barrier specifically needs its own fix independent of per-target fh depth: let each target's thread resubmit its own next fh as soon as *it* finishes, instead of waiting for `pool.__exit__` to release the whole round (e.g. a rolling/streaming submission model instead of one-`ThreadPoolExecutor`-per-round). **Instrument first** (see 3.6).

### 3.2 HIGH — Single range-request failure escalates to a full multi-GB GRIB download, then thrown away

`_download_subset_with_inventory_byte_range` fallback (`fetch.py:3481-3499`) → `_fetch_subset_bytes_from_full_source` (`fetch.py:3236-3256`) → full download (`fetch.py:747-778`, 90 s per-chunk timeout, no total deadline). There is **no per-range retry** (`fetch.py:3297-3377`), so one transient 500 on a ~2 MB range triggers a full-file download (GFS pgrb2 ~500 MB, EPS enfo multi-GB) to extract one message — and `finally` deletes the temp file with no reuse for the next variable in the same frame, which repeats the download. No size guard; can hold a build slot for the duration.

Fix (M): retry the range request 2–3× with short backoff before the full-file fallback; cap fallback by Content-Length; route the fallback through the EPS full-file cache when enabled.

### 3.3 MED — Byte-range correctness: HTTP 200 passes as a "subset"

**STATUS: FIXED 2026-07-07** (now at `fetch.py:3181-3234`, 206 check at `3194-3223`; re-verified in place 2026-07-09). `_network_fetch_range_bytes` now streams the response and rejects non-206 responses before buffering the body, unless `Content-Length` exactly matches the requested slice; it also rejects a 206 payload whose length doesn't match the requested range (truncation). New metrics `range_request_not_honored`/`range_payload_truncated`; new tests `test_network_fetch_range_bytes_rejects_full_file_200_response`, `test_network_fetch_range_bytes_accepts_200_when_body_is_exactly_the_slice`, `test_network_fetch_range_bytes_rejects_truncated_206_payload` in `backend/tests/test_fetch_range_cache.py`.

`_network_fetch_range_bytes` (originally `fetch.py:3038-3044`) + `_validate_grib_range_payload` (originally `fetch.py:3084-3104`): if an origin/proxy ignores the Range header and returns 200, `response.content` is the entire file — which starts with `GRIB`, so validation passes. The payload is written as the subset and rasterio reads band 1 = first message of the file, i.e. potentially the **wrong variable/level rendered on the map**. Also buffers multi-GB `.content` in RAM. `expected_size` (`fetch.py:3169-3177`) only gates cacheability, not correctness.

Fix (S, one line): assert `status_code == 206` (or `len(payload) == expected_size`); raise `_InvalidGribSubsetError` otherwise.

### 3.4 MED — Subset reuse disabled by default; identical subsets re-downloaded

With the disk lock env off (default, `fetch.py:2816`), cached-GRIB reuse exists only in the locked branch (`fetch.py:3866-3884`); the default branch calls `H.download(..., overwrite=True)` (`fetch.py:4021`). Identical subsets are fetched multiple times per run (UGRD/VGRD shared across wspd/barbs, accumulation loops, invalid-subset retries). `BundleFetchCache` covers only the byte-range paths.

Fix (S): decouple cache-reuse from the locking flag — `_subset_file_status` check + `overwrite=False` in both branches.

### 3.5 MED — Full RGBA colorization computed per frame, then discarded

`pipeline.py:1781-1785`: `_, colorize_meta = float_to_rgba(display_data, ...)` — the `(4, H, W)` uint8 array is thrown away; only legend metadata is kept. Tens of MB of allocation + a full-grid colorize pass per frame (×51 in member passes), feeding native-heap churn.

Fix (S–M): metadata-only path (`colorize_meta_for(color_map_id, var_key)`).

### 3.6 MED — No per-phase timings inside `build_frame`

`pipeline.py:1632/1666/1728/1761/1774/1841` log "Step N/6" with no durations and mostly no model/var/fh, so interleaved thread logs are unattributable. Only whole-frame `elapsed_ms` and completion-only run duration exist. "Is it fetch, warp, colorize, or artifact write?" is unanswerable from prod logs — which is exactly what sizing 3.1 and 3.5 needs.

Fix (S): per-step `perf_counter` deltas in the existing step logs + include model/var/fh. **Do this before 3.1.**

### 3.7 MED — Contour and pressure-center paths independently re-fetch/re-warp the same component

`_build_contour_metadata_for_variable` (`pipeline.py:194-391`) and `_build_pressure_center_metadata_for_variable` (`pipeline.py:414-614`): both resolve the same component (center falls back to `contour_component`), and each does its own `fetch_variable` + `warp_to_target_grid` when the warped-component cache misses. The cache is only consulted when `derive_target_grid_id` is non-empty, so plain mslp/height contour vars fetch+warp twice per frame. The contour warp also hardcodes `resampling="bilinear"` (`pipeline.py:319`) while requesting `contour_resampling` from the cache — key-mismatch risk.

Fix (M): compute the warped component once in `build_frame` and pass to both helpers, or populate the cache on miss.

### 3.8 MED — Failed frames re-attempted every 60 s forever, no backoff or cap

`blocked_targets` is per-`_process_run` state (init `scheduler.py:1819`, added/checked `2436`, logged `2512-2541`); the incomplete-run poll (`INCOMPLETE_RUN_POLL_SECONDS=60`, `scheduler.py:3157`) starts each cycle with fresh state and re-fetches/re-warps/re-fails the same poison frame every minute until the run is superseded (up to ~6 h at GFS cadence). Also pins the fast 60 s poll by keeping the run "incomplete". Contrast: SLR rebuilds carry `rebuild_max_attempts=2`.

Fix (M): persist per-(run, var, fh) failure counts across `_process_run` calls with a cap or exponential backoff; distinguish deterministic from transient failures.

### 3.9 MED — Progressive publish re-copies the entire run tree per snapshot (partially addressed by 2.1)

`_promote_run` (`scheduler.py:1341-1386`) called on first promote then every ≥4 new frames (`scheduler.py:2548-2558`, `DEFAULT_PROGRESS_PUBLISH_MIN_NEW_FRAMES=4` at `scheduler.py:170`): copytree(published→tmp, hardlink) at `1347-1352` + copytree(staging→tmp overlay) at `1353-1359` — both still full-cost, O(total frames) work to publish 4 new frames, dozens of times per run, heavy inode churn on ensembles. **The final step is no longer `rmtree + move`** — 2.1's fix replaced it with the rename-swap (`1377-1386`), so the 404-window multiplication this finding originally flagged is resolved. The two copytree calls' O(total frames) cost is not; that part remains open.

Fix (M): incremental promote — frame files are immutable once written, so additive in-place hardlinking is safe, avoiding the full-tree copytree on every snapshot. (The rename-swap half of the original fix suggestion is done via 2.1.)

### 3.10 MED — Herbie-internal network calls have no timeout control

`Herbie(...)` construction, `H.index_as_dataframe` (`fetch.py:1497`), `H.download` (`fetch.py:3903/4021`) use Herbie's internal requests with no timeout wrapper; the inventory in-flight follower wait is bounded by `max(5.0, inventory_cache_ttl)` = **600 s default** (`fetch.py:1486`). A hung remote read blocks a build slot up to 10 minutes. This file's own requests calls are covered (45/90 s) — the gap is exclusively the Herbie surface.

Fix (M): run Herbie calls under a deadline; cap the follower wait at 60–90 s independent of cache TTL.

### 3.11 MED/LOW — smaller perf items

- **No `requests.Session`/connection pooling anywhere** (`fetch.py`, several `requests.get(` call sites — drifted from the original `3040`/`1181`/`725`, not individually re-pinned): every range request is a fresh TCP+TLS handshake; EPS pf-mean = ~51 ranges per variable per fh. **Measured in live prod logs, 2026-07-08:** one variable's single-fh PF-mean burst (`hgt500__mean` fh102, all `FETCH_CACHE event=miss ... url_hash=4458dafab34e` lines) ran 08:48:17.595→08:48:23.911 — ~6.3 s for ~50 small (300–700 KB) range requests to Azure blob storage, ~125 ms/request average, plausibly dominated by handshake overhead rather than transfer given the small payload sizes. Fix (S): module-level pooled Session sized ≥ range workers.
- **`np.savez_compressed` on every cumulative frame's hot path** (`derive.py:701`): zlib on a CONUS float32 grid ≈ 100–300 ms/frame × 4 strategies. Fix (S): uncompressed `np.savez` or zstd.
- **Full-grid unicode ptype arrays** (`derive.py:4840-4841`, `2140-2141`, `2451-2452`): `np.array(["ice","snow","rain"])[idx]` ≥ 20 B/px (~38 MB transient on HRRR) + 3–4 full-grid string scans. Integer codes are ~5× smaller and faster.
- **float64 promotion on climatology-grid warps** (`derive.py:2996`): `raw_data.astype(np.float64)` per component per fh; the generic warp path doesn't do this.
- **Repeated inventory round-trips on cache miss** (`derive.py:3563`, `1775`): a full rebuild at fh240 issues ~40 sequential network inventory calls before any data fetch. Batch per (run, product) into the ctx.
- ~~**`_fetch_inventory_index_text` downloads the idx twice**~~ **RESOLVED (unrelated commit, re-verified 2026-07-09):** the function (`fetch.py:~1295-1305`) now fetches once and parses from that single fetch — the double-fetch this bullet described no longer exists.
- **Kuchera rebuild precheck downloads full GRIBs and discards them** (`scheduler.py:807-830`): up to 5 levels × (temp+rh) full `fetch_variable` calls before each SLR rebuild; use idx probing instead.
- **Memory-audit instrumentation logs at INFO unconditionally** (`_log_fetch_context_memory`, per-strategy entry/exit + per APCP step, `derive.py:3841`): O(cache entries) × steps × frames; gate behind `CARTOSKY_FRAME_MEMORY_AUDIT`.
- **Frontier re-scan stat storm** (`scheduler.py:2182-2194`, `1425-1435`): thousands of stats per 60 s poll for ensembles; harmless on SSD, measurable on network filesystems.
- **Member pending/promote scans** stat+JSON-parse every expected frame (~2.8k for GEFS) each scheduler poll (`members.py:1354-1381` `_iter_expected_member_frames`, `1384-1407` `member_pass_pending`, `1410-1434` `member_promote_pending`). **Note (2026-07-09):** the new `_mean_frame_available` helper (`members.py:475-484`, added by `675a5883`'s backfill logic) adds *more* of this same per-(var,fh) stat+parse cost during backfill scans — this bullet's cost is now somewhat worse under `mean_coverage_only`, not better.
- **Parallel pf prefetch holds all ~51 range payloads in memory simultaneously** (`fetch.py:1939-1949`) before writing — tens-to-hundreds of MB spikes; stream to disk with a bounded window.
- ~~**members.py warps at default `working_dtype=float64`** (`members.py:1028,1115,1269,1287,1303`): pass float32 at member call sites (the GDAL block cache is already capped at 256 MB in prod — see 2.3).~~ **FIXED 2026-07-10:** all five member warp call sites now pass `working_dtype=np.float32` (same opt-in MRMS/NDFD already use). Note the theoretical parity caveat: member values are packed to uint16, and float32-vs-float64 bilinear differences (~1e-7 relative) can flip a rare pixel across a packing quantization boundary (≤1 LSB); the deterministic main pipeline still warps at the float64 default, unchanged.
- **Fixed 0.6 s retry sleeps, no jitter/backoff** in all four fetch retry loops.
- **Colorize `transpose().copy()` doubles the RGBA transient** (`colorize.py:179/242/301`, ~145 MiB extra at MRMS scale); already mitigated for MRMS via `colorize_metadata()`. The LUT approach itself is good (256-entry, no per-pixel work).

---

## 4. Robustness & latent hazards

### 4.1 HIGH — Cumulative member scheduling assumes derived fhs align with step_hours multiples

**RESOLVED 2026-07-14.** `build_member_plan` now rejects a cumulative derived
schedule containing forecast hours outside its configured step grid. Resume now
selects the latest prior forecast hour that was both scheduled for every
cumulative variable and complete for that member, then replays every intervening
cumulative step before the first missing output. When no common complete
checkpoint exists, the member rebuilds cumulative state from the first step while
leaving already-complete outputs untouched. This is deliberately fail-fast at the
whole-model member-plan boundary: invalid schedule configuration returns a logged
`STATUS_ERROR` on each attempted pass rather than silently skipping one variable;
detection remains log-dependent until the separate persistent
`skipped_incomplete` observability follow-up lands. Regressions:
`test_build_member_plan_rejects_off_cadence_derived_fhs` and
`test_cumulative_resume_replays_steps_after_last_complete_scheduled_frame`.

**Re-verified 2026-07-09, still fully open — deep-dive checked against `675a5883` ("feat: implement mean coverage cap and backfill logic for member frames", 2026-07-08), the one commit that substantially touched this area since the audit was written.** That commit adds a `mean_coverage_only` cap and an idle-backfill pass for a *different, orthogonal* problem — recovering mean coverage on superseded runs whose upstream data was defective (see `docs/ENSEMBLE_MEMBER_SCHEDULER_DESIGN.md` §14, the EPS `20260708_00z` rh700 incident it documents). It does not touch, validate, or harden the `step_fhs`/`prior_steps` mechanics this finding describes.

`members.py:487-573` (`build_member_plan`), `587` (`_bundle_fields_for_fh`, empty-return gate `583-589`), `1204-1226` (resume/rebase). Two unguarded failure modes, both confirmed still present at current line numbers: (a) `step_fhs` is still computed as a pure synthetic grid (`step_fhs = list(range(cumulative.step_hours, max_step_fh + 1, cumulative.step_hours))`, `members.py:557`) independent of the derived var's actual scheduled fhs — a scheduled derived fh that isn't a `step_hours` multiple is never in `step_fhs`, so `_bundle_fields_for_fh` never requests it and returns `{}` — the frame is never recorded (not written, not FETCH_FAILED, not ERROR) and `member_pass_pending` (`members.py:1384-1407`) stays True forever; the scheduler re-runs the pass indefinitely. (b) Resume still picks `base_fh = prior_steps[-1]` (`members.py:1206, 1211`) from the **step grid**, not from scheduled/written frames; sparser cumulative scheduling (e.g. 24 h vars on 6 h steps) → `_decode_member_frame` (`members.py:1062-1072`) hits a missing file, raises, caught by the generic `except Exception` (`1223-1226`) → `STATUS_ERROR` every pass, member permanently wedged (next pass recomputes the identical `resume_fh` and hits the same missing file). Latent only because GEFS/EPS schedules happen to align.

**Worth flagging:** `mean_coverage_only=True` fires only from the new `_maybe_run_member_backfill` (`scheduler.py:2728-2797`) on idle passes over superseded runs, and even there it filters `fhs_by_var` but *not* `step_fhs` — so a backfill pass over an irregular superseded run could still exercise this exact rebase-from-grid path. The new backfill loop is an additional code path running the same `build_member_plan`/`_process_member` logic more often (every idle iteration), which if anything slightly widens 4.1's exposure surface rather than narrowing it. Speculative, not a confirmed new incident.

**Severity discussion, 2026-07-09 (independent review raised, and resolved by checking the new downstream consumer).** An independent review argued this should read MED today, not HIGH: GEFS/EPS schedules currently align with `step_hours`, no active corruption has been demonstrated, and the original "fix before Tier-2 percentiles" framing is stale now that Tier-2 has shipped — so the urgency argument as originally written no longer holds. That's fair as far as it goes, but checking what Tier-2 actually does with member frames points the other way:

`backend/app/services/builder/stats.py` — the Tier-2 percentile/probability pipeline, live in production on GEFS+EPS per this session's own record of an earlier audit pass (rollout gates 6A–6C passed, viewer Product selector already exposes it) — has a **hard per-fh completeness gate**: `_roster_complete()` requires every member's frame to exist before `_process_stats_unit` will compute a percentile; if any is missing, the unit is recorded `skipped_incomplete` and simply retried later (`stats.py:159-167`, `260-265`). This is good design — it protects Tier-2 from ever *computing a wrong percentile* off a partial roster, so 4.1 does not threaten Tier-2's correctness directly.

But it does threaten Tier-2's **completeness**, permanently: 4.1's failure mode (b) — a member wedged by a missing-file resume error — means that member never writes another frame for any later fh in the run. Once one member is wedged, `_roster_complete()` can never return True again for that variable at any fh past the wedge point, so the stats pass silently and permanently skips producing percentile/probability frames for that variable for the rest of the run. From the viewer's Product selector, this reads as "the percentile map stopped updating partway through the run" — a live, user-facing product gap, not an abstract scheduler pathology. `skipped_incomplete` is explicitly *not* treated as a failure condition in `StatsPassSummary.complete` (`stats.py:219-229`), so nothing currently pages or alerts on this happening.

Net: kept **HIGH**. The reviewer's factual point (schedules currently align, no confirmed incident) is correct and is why this has stayed *latent* rather than *active* — but the downstream consequence, now that there's a real consumer to point at, is more severe than "member pipeline wedges," it's "a live percentile/probability product can silently and permanently stop updating for a variable, with no alerting." That is a legitimate MED→HIGH argument the original write-up didn't have, because Tier-2 didn't exist yet when 4.1 was first written.

~~Fix (S), now a live-production prerequisite rather than a pre-Tier-2 gate — plan-time validation in `build_member_plan` that scheduled derived fhs ⊆ step_fhs; rebase from the last complete *scheduled* frame instead of the raw step grid.~~ **DONE 2026-07-14.** Still consider alerting on `skipped_incomplete` units that persist across N consecutive stats passes for the same (var, fh) as a cheap detection layer independent of the underlying fix.

**Scope note:** `stats.py` itself has not been audited to the depth of the rest of this document (it wasn't in scope when this audit was written, and this pass only read it to answer the 4.1 severity question). A dedicated audit pass of the Tier-2 pipeline — its own retry/error handling, the `sorted_nanpercentile`/`prob_exceedance` math in `stats_math`, RSS behavior at `_process_stats_unit`'s member-stack decode, and interaction with run retention — is recommended as follow-up work, not covered here.

### 4.2 MED — pf band→member mapping guards don't pin the actual invariant

`members.py:771-800` (`_pf_band_member_numbers`, called from `_resolve_pf_subset` at `803-941`, call site `895`; re-verified 2026-07-09, guard logic byte-for-byte unchanged): the index-derived mapping is correct only if `_download_subset_with_inventory_rows` (fetch.py) writes unique byte ranges sorted by (start,end) AND GDAL exposes bands in file order. The count/uniqueness validations would still pass if a future fetch.py change reorders writes — silently relabeling all 50 EPS members. Fix (S–M): cross-check each band's perturbation number from GRIB band metadata (`GRIB_PDS_TEMPLATE_NUMBERS`/`GRIB_IDS`) against the derived number, or pin fetch.py's sort contract with a test.

### 4.3 MED — EPS pf-mean can silently average fewer than 50 members

`fetch.py:1965-1966`: an empty local-read payload is `continue`d; `_aggregate_grib_subset_mean` counts whatever bands exist; `meta["member_count"]` is recorded (`fetch.py:2213`) but never validated against the expected pf count (EPS = 50). A partial subset yields a plausible but wrong mean. Fix (S): compare `member_count` to `len(pf_inventory)`, raise on mismatch. (Band subsetting itself is correct — only pf rows' ranges are fetched; the 51-band cost is aggregation read, not over-download.)

### 4.4 MED (upgraded evidence) — EPS mean fetchers have a fourth, divergent copy of the retry loop; only one variable even tries the cheap path, and it's failing every hour in the live/incremental phase

`_fetch_ecmwf_pf_mean_variable` (`fetch.py:2080-2083`) and `_fetch_ecmwf_direct_mean_variable` (`fetch.py:2288-2291`): bare `except Exception` → sleep → retry, no transient/permanent classification, no idx negative-cache, no jitter; `direct_mean_or_pf_mean` (`fetch.py:2293-2311`) then repeats the whole budget in pf-mean. This is the 4th copy of the priority/retry walk (alongside `fetch_variable`, `inventory_lines_for_pattern`, `product_hour_has_any_idx`), each with different semantics — the drift is the incident-generator.

**Confirmed against live prod logs, 2026-07-08, plus routing code.** `app/models/eps.py:129-147` (`EPSPlugin.herbie_request`) shows only `hgt500__mean` is tagged `ecmwf_direct_mean_or_pf_mean` (try the cheap precomputed ensemble-mean product, fall back to PF-mean on failure). Every other EPS mean variable — `tmp2m__mean`, `tmp850__mean`, `tmp850_anom__mean`, `rh700__mean`, `rh2m__mean`, `dp2m__mean`, `10u__mean`, `10v__mean`, `pwat__mean`, `precip_total__mean`, and all four precip-anomaly means — is hardcoded to `ecmwf_pf_mean` and *never attempts* the cheap path; it always downloads and averages all ~50 perturbed-member byte ranges.

Live logs show `hgt500__mean`'s direct-mean attempt failing on every observed forecast hour of an in-progress run: `WARNING ECMWF EPS direct mean unavailable; falling back to PF mean aggregation for ifs fh102 pattern=:gh:500: reason=RuntimeError: ... idx_empty` (repeated at fh108, fh114). Code-level explanation: `_ecmwf_eps_statistics_file_fh` (`fetch.py:2213-2214`) unconditionally rewrites the request to a single terminal statistics file named by the run's *final* horizon (`-240h-enfo-ep` for any fh ≤ 240, `-360h-enfo-ep` beyond), regardless of the fh actually being built.

**Confirmed against ECMWF's real public feed (`data.ecmwf.int`), 2026-07-08.** This is expected, unavoidable behavior, not a pattern bug — and the product is correctly scoped to exist, just not yet at the time it's requested:
- A completed run's `enfo` directory (`20260707/00z`) contains **exactly two** `-ep` files total: `240h-enfo-ep.{grib2,index}` and `360h-enfo-ep.{grib2,index}` — no intermediate hourly `-ep` files. ECMWF genuinely publishes this ensemble-statistics product as a single combined bundle per horizon, not incrementally per step, confirming `_ecmwf_eps_statistics_file_fh`'s design.
- The `-240h-enfo-ep.index` for that completed run *does* contain `type=em` (ensemble mean) records for `param=gh, levelist=500` — 65 of them, one per output step from fh0 through fh240 (3-hourly through fh144, 6-hourly after) — bundled in the one file. So `hgt500__mean`'s pattern and routing are correct; ECMWF simply hasn't published the bundle yet at the point our live/incremental builder requests it (the run was only at fh~114–120 of 240+ when the observed warnings fired). `idx_empty` during the live catch-up phase is therefore expected, and will resolve itself once ECMWF finishes and publishes the terminal file — but the code still pays for the doomed attempt every time: `_priority_candidates` × `_retry_count()` (default 3 priorities × 2 retries = up to 6 attempts, each constructing a `Herbie` object and fetching/parsing an idx) before falling through to PF-mean, on every `hgt500_anom`/`hgt500` frame for as long as the run is incomplete.
- **New finding, also confirmed from the same real index:** the `em`/`es` product's full field list is `gh@1000/300/500`, `msl`, `t@250/500/850`, `ws@250/850` — a small, curated set. This correctly excludes `tmp2m`, `pwat`, `rh2m`/`rh700`, `dp2m`, `10u`/`10v`, and `precip_total`/its anomalies from ever having a cheap direct-mean option (eps.py's hardcoded `ecmwf_pf_mean` routing for those is right). **But `t@850` *is* in the product**, and `tmp850__mean`/`tmp850_anom__mean` (`eps.py:131-147`) are hardcoded to `ecmwf_pf_mean` and never attempt direct-mean at all — a missed optimization once the terminal file is available (e.g. on rebuild/backfill passes over already-complete runs, where it's always present).
- **New finding:** each of the 65 steps for `gh@500/em` lives in the *same* idx file. Once the terminal file becomes available, the current code still re-fetches/re-parses that idx separately for every fh (one `_fetch_ecmwf_direct_mean_variable` call per fh) — a single cached inventory fetch per run could serve every subsequent fh's lookup for the rest of that run's life.

Fix (M): reuse `_is_*_error` classification + negative cache; extract one shared priority-walk helper. **Higher-value fixes now confirmed by real data:** (1) skip the direct-mean attempt while the run is still incomplete — track a per-run negative-cache flag once the first `idx_empty` is observed for the terminal file, and only re-probe once, e.g. after the run's build frontier passes some late fh threshold — removing up to 6 doomed attempts per frame for the entire live/incremental phase; (2) once the terminal file *is* confirmed available, cache its parsed inventory once per run instead of re-fetching per fh; (3) extend `t@850` (`tmp850__mean`, `tmp850_anom__mean`) onto the same `ecmwf_direct_mean_or_pf_mean` path now that (1) makes the attempt cheap to skip when premature. Separately, since PF-mean remains unavoidable for the other ~10 EPS mean variables regardless, the highest-leverage fix for those is still making PF-mean itself cheaper: connection pooling (3.11) and confirming/raising the parallel range-fetch worker count for the ~50-member download.

### 4.5 MED — wgrib2-style idx: last message in a file is unfetchable via byte ranges

**Re-verified 2026-07-09, still open** (line numbers updated; a related-but-distinct bug nearby was fixed). `fetch.py:1424-1425`: the last record gets no `end_byte` (the wgrib2 idx-text parser's final `pending_record` append is never followed by the `setdefault("end_byte", ...)` that only fires inside the loop when a *next* record's start becomes known). `_inventory_row_byte_range` (`fetch.py:1765-1818`) returns `None` for it and the row is silently skipped. A variable that is the final GRIB message deterministically fails byte-range → escalates to the 3.2 full-file path or hard failure. Fix (S): emit open-ended `Range: bytes={start}-`.

**Related fix landed nearby, not the same bug:** `_inventory_row_byte_range` grew a `_length`-based fallback this week (`636c3573`/`37fb767b`, "fix: ... end byte ... eccodes-style inventories") that fixes a *different* off-by-one — ECMWF's eccodes-style rows report an exclusive `end_byte` (offset+length), which was being treated as inclusive and over-reading one byte past EOF on a file's last message, tripping this audit's own 3.3 strict-payload-size fix. That fallback only rescues rows carrying `_offset`/`_length` (eccodes-style, i.e. ECMWF). The wgrib2-style records this finding describes never populate a `_length` key (only `start_byte`/`search_this`/`inventory_line`/`line`), so they still can't be rescued — this finding remains open and distinct from the fixed one.

### 4.6 MED — `.part` download path is deterministic and unlocked by default

`_download_full_grib_to_path` (`fetch.py:749`): `out_path.with_suffix(".part")`; the guarding `_path_download_lock` is a no-op with the lock env off (default). Concurrent writers interleave into the same `.part`; the size check (`fetch.py:772`) misses equal-size interleavings. Also unbounded total download time. Fix (S): unique temp name + atomic `replace`; wall-clock deadline. When the lock IS enabled, the 8 s lock timeout (constant `fetch.py:109`, used at `2855`) is far shorter than a multi-GB download held under it — spurious `TimeoutError`s for waiters.

### 4.7 MED — `np.to_numeric` doesn't exist; pf member sorting is dead code

`fetch.py:2140`: raises `AttributeError` on every call, swallowed at `fetch.py:2142`. Members aggregate in raw inventory order — harmless for a mean, but if this block is copied into the Phase-3/4 member/percentile pipeline (where order matters) it silently misorders members. Fix (S): `pd.to_numeric`.

*(Two COG-specific findings — a `COPY_SRC_OVERVIEWS` creation-option mismatch and dark halos on RGBA overview tiles — were removed 2026-07-07 due to the COG → binary-sampling migration.)*

### 4.8 MED — Discrete colormap specs never validate `len(colors) == len(levels)-1`

`colorize.py:248-257`: digitize+clip silently absorbs a mismatch (top bins collapse into the last color; `legend_stops` zip truncates and diverges from the render). The tmp850_anom ladder is verified correct, but the next hand-built ladder has no guard. Fix (S): one-line assertion.

### 4.9 MED — Failure cleanup deletes the variable's whole shared contours dir

`build_frame` binds the contours *directory* into `contour_geojson_path` (`pipeline.py:391`), and `_cleanup_artifacts` (`pipeline.py:2198-2212`) deletes directories recursively — a failure after contour generation deletes all previously built fhs' contour geojsons for that var in staging. Mitigated by published copies surviving, but a staging/published divergence trap. Fix (S): return/clean only the per-fh geojson path.

### 4.10 LOW — additional items

- **Exception classification by exact string match** (`fetch.py:2726-2770`): a Herbie/GDAL version bump silently reclassifies transient→non-transient. Pin versions or match exception types.
- **Uncleaned temp artifacts**: `/tmp/twf_subset_*` (`fetch.py:2785-2791`), `eps_subset_fallbacks` (`fetch.py:630-637`), `.cartosky_pf`/`.cartosky_em_fhNNN` subsets (`fetch.py:2161`, `2393`) have no TTL/cleanup (only the EPS full-file cache is swept). Disk creep.
- **`_decode_member_frame` hardcodes `<u2`** (`members.py:1062-1072`, hardcode at `1070`) while computing `packing_dtype` via `grid_dtype(...)` at `1066` for path resolution only, still ignored by the actual decode; a uint8-packed member var fails loudly at reshape. Align with the decode-authority dtype branch.
- **Precheck "fail open"** for `idx_empty`/`pattern_missing`/`no_inventory` (`fetch.py:2928`, `3028`) proceeds to a doomed download+fallback+sleep per priority. Deliberate for progressive publishes; add a per-reason cap.
- **~130 lines of retry/backoff/lock scaffolding duplicated** between `_fetch_member_bundle` and `_resolve_pf_subset` (`members.py:631-739` vs `803-941`), mirroring fetch.py's mean path — a retry-policy change must land in 3 places. Re-verified 2026-07-09: internal duplication (backoff loop, `_subset_download_lock`, priority iteration) untouched by intervening commits. (The cumulative step-math duplication vs derive.py is deliberate and parity-pinned by test — fine.)

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
- **~200 lines of dead loop-pregeneration plumbing in scheduler** (`scheduler.py:1831-1837` — 8 `loop_*` params unused, prewarm fhs computed here too, never consumed; `1989-1990` `del pregenerate_loops`) while `DEFAULT_LOOP_PREGENERATE_ENABLED = True` misleads operators. Loop WebP cache is evidently on-demand now — first viewer pays generation latency. Re-wire or delete.
- **`rebuild_existing` parallel branch is unreachable** (`scheduler.py:2995-3000` forces `workers=1`, so the ThreadPool branch at `2130-2168` — which submits with *no* fetch_ctx/readiness_cache — can't run). Delete or align.

### 5.4 Coupling wrinkles

- **Stringly-typed grid-id contract**: pipeline builds `"climatology:{source}:{region}:{grid_m:.1f}m"` (`pipeline.py:1467`) and derive parses it back (`derive.py:2981-2992`); a format tweak on either side silently falls through to the generic warp path.
- **Duck-typed FetchContext**: `data_root` and `kuchera_cumulative_cache` are not dataclass fields; pipeline injects via `setattr` (`pipeline.py:1531`), derive reads via defensive `getattr` at ~10 sites. Declare them as fields; removes ~30 lines of guards.

### 5.5 Verified sound (orchestration)

- Scheduler-overlap protection is real: per-model `fcntl` lock (`scheduler.py:1389-1422`).
- Manifest/LATEST ordering within one publish is correct: frames → manifest → LATEST pointer last (`scheduler.py:2008-2019`, within `_publish_run_snapshot`: `_promote_run` → `_write_run_manifest` → `_write_latest_pointer`); manifest lists only fhs whose sidecars exist. JSON writes are atomic (tmp→rename). **Re-verified 2026-07-09 against 2.2's fix:** `_enforce_manifest_retention` runs later, at `scheduler.py:2573`, outside `_publish_run_snapshot` — the ordering guarantee here is unaffected by that fix.
- Transient-vs-failed distinction exists; transient targets pause rather than block.
- `BundleFetchCache` leader/follower dedup is correct (event set in `finally`; errors propagate; invalid entries evicted).
- EPS statistics-file URL rewrite is anchored and step-filtered with an exactly-one-record assertion (`fetch.py:2098-2232`).
- Output nodata handling is correct: RGBA gets alpha-0; value grids use NaN.
- **Correction, 2026-07-09 (this line was wrong, per independent review):** Tier-2 percentile/probability code *does* exist — `backend/app/services/builder/stats.py`, added 2026-07-08, live in production on GEFS+EPS. It's well-designed for the correctness half of the problem: a hard per-fh completeness gate (`_roster_complete`) refuses to compute a percentile from a partial member roster, deferring instead (`skipped_incomplete`) — so it cannot silently produce a *wrong* value from missing members. What it doesn't guard against is 4.1's wedge failure mode causing it to silently and permanently stop producing values at all for an affected variable — see the severity discussion under 4.1. Separately: the `__mean` display variables in §4.4 (`tmp2m__mean`, `pwat__mean`, etc.) are a different pipeline — fetched/PF-averaged via `fetch.py`, not derived from `stats.py`'s member-roster percentiles; conflating the two in the original text was itself part of this error.

---

## 6. Test-coverage gaps

Strong existing coverage: Kuchera (SLR formula, cumdiff, windows, incremental-vs-full parity, ptype gate, surface cap, pressure mask), GFS/NAM/NBM inventory differencing and overcount prevention, GFS snowfall/ice, GFS+ECMWF ptype-intensity classification, RH Magnus, precip anomaly windows/units, GEFS fractional csnow.

Gaps mapping to findings:
- ~~No ECMWF ptype test in warped-component mode~~ **Closed 2026-07-07** — added as part of 1.1's fix (`test_ecmwf_ptype_intensity_uses_warped_component_fetches_when_requested`); left here as a stale leftover until caught by independent review 2026-07-09.
- No `step_fhs[-1] == fh` / off-cadence fh test (1.4)
- No missing-mid-step accumulation test asserting NaN/flag semantics (1.3 — the existing test covers csnow skip only, not APCP-step loss)
- No `_normalize_ptype_probability` percent/fraction boundary test (1.6)
- No NaN-in-categorical-mask radar test (1.8)
- No cross-strategy step-validity consistency test (1.9)
- No assertion that component snow planes are physically scaled (1.5)
- Ptype accumulation with fractional ensemble masks untested (1.7)
- No test pinning fetch.py's byte-range write-sort contract for member band mapping (4.2)
- No discrete-spec `len(colors) == len(levels)-1` validation (4.8)

---

## 7. Recommended sequence

**Quick wins, high impact (all S effort):**
1. ~~ECMWF ptype warp-params fix + warped-mode test (1.1 — wrong ice/rain classification in prod today)~~ **DONE 2026-07-07**
2. ~~Readiness-cache `fh` keying (2.6) — closes the July 6 gate-bypass class for all later forecast hours~~ **DONE 2026-07-07**
3. ~~HTTP 206 / range-size guard (3.3) — one-line wrong-variable protection~~ **DONE 2026-07-07**
4. ~~Publish rename-swap (2.1) + manifest eviction (2.2) — sharply reduces the 404 incident class~~ **DONE 2026-07-07** (not fully atomic — see 2.1's residual-window note; revisit with a symlink-indirection design if 404s persist)
5. ~~Quality-flag threading for fail-open fallbacks (1.2)~~ **DONE 2026-07-10** (flags only; the fail-closed-gate question is deliberately deferred — see 1.2's status note)
6. ~~Prune-policy fix (2.4) + float32 member warps — plausible (not yet measured) contributors to the swap incident; also codify `GDAL_CACHEMAX` in the repo unit templates.~~ **DONE 2026-07-10** (all three parts; see 2.4/2.3/3.11 status notes). Run `MALLOC_ARENA_MAX=2` as its own isolated, measured canary — don't bundle it into this same deploy (2.3) — **still open, deliberately not included here**
7. ~~Scheduler `request.model` fix + internal-id guard in `fetch_variable` (2.5) — July 6 incident class~~ **DONE 2026-07-10** (guard extended to all three public Herbie-facing entry points, incl. the readiness probe the actual incident went through — see 2.5's status note)
8. ~~**Member scheduling validation (4.1)** — plan-time `derived fhs ⊆ step_fhs` validation + rebase-from-last-scheduled-frame.~~ **DONE 2026-07-14** (resume replays every cumulative step after the selected checkpoint; no-checkpoint recovery replays from the first step; persistent incomplete-roster alerting remains a separate follow-up).
9. pf member-count validation (4.3), `step_fhs[-1] == fh` assertion (1.4)

Each quick win should land with a narrow regression test for its incident class: ECMWF warped ptype ice, readiness by fh, stale-manifest eviction, HTTP 200 range rejection, ptype fallback quality flags.

**Medium projects:**
- Per-step build timings (3.6) **first** — current logs cannot attribute frame time to fetch vs warp vs colorize vs artifact write, and fh-parallelism sizing needs that data
- fh-level parallelism (3.1), sized from the 3.6 data
- Range retry before full-file fallback + subset reuse (3.2, 3.4)
- Failed-frame backoff (3.8)
- Dedicated audit pass of `stats.py` (the Tier-2 pipeline) — not covered by this document; see the scope note under 4.1

**Larger refactor (behind per-model parity canaries):**
- Extract the incremental-cumulative orchestrator (5.1) — ~1,000 lines of dedup across 5 strategies including the mechanical items (5.2), eliminates the shipped mismatch-handling divergence.

**Alternative sequencing (independent review, 2026-07-09):** a second reviewer proposed batching strictly by risk category instead of by effort tier — all silent-correctness fixes first (1.2, 1.3, 1.4, 1.7, 1.8, 4.3), then incident-class boundary fixes (2.5, true publish atomicity if 404s persist), then re-auditing `stats.py` and hardening 4.1/4.2/the `<u2` decoder, then instrumentation, then prune-policy/float32 changes behind canaries, and only then concurrency/refactor work. That ordering is defensible and arguably more aligned with this doc's own stated priority (see the unifying principle below) than the effort-tiered list above. The sequence above is kept because it also encodes real technical dependencies (e.g. instrument before you parallelize, canary before you bundle changes) that a pure risk-ordering doesn't capture — but a team optimizing purely for "stop shipping wrong data fastest" should follow the risk-first order instead.

**Unifying principle for new code:** the dominant risk class in this pipeline is the *silently plausible wrong frame*. Anywhere the code substitutes zeros/ones for missing components, OR-merges validity, clamps out-of-range data, or infers units from data values, it should instead fail the frame, mark it degraded via `_record_derive_quality`, or propagate NaN — never render a confident-looking value.
