# Phase 3A sizing — global anomaly variables (ERA5 global 4326 baselines)

Status: **analysis only, 2026-07-30.** No pipeline changes, no downloads, nothing committed.
Answers scope item 1 of "Phase 3A — Global anomaly variables" in
`docs/MAX_WEEK_EXECUTION_PLAN_2026-07-27.md:572-575` ("sizing estimate first,
before any ERA5 download").
Grid authority: `docs/GLOBAL_DOMAIN_4326_CONTRACT.md`.

---

## 0. Grid arithmetic used throughout

| | NA canonical (baseline grid today) | Global native (Phase 3A target) |
|---|---|---|
| CRS | EPSG:3857 | EPSG:4326 |
| bbox | `(-19814869.36, 557305.26, -2782987.27, 16967796.94)` `raster_grid.py:48` | `(-180.125, -90.125, 179.875, 90.125)` `raster_grid.py:63` |
| Resolution | 25 000 m `climatology.py:18-23` | 0.25° |
| Shape (h × w) | **657 × 682** | **721 × 1440** |
| Cells | 448 074 | 1 038 240 |
| Baseline file, float32 raw | 1 792 296 B = **1.709 MiB** | 4 152 960 B = **3.961 MiB** |
| **Cell ratio global : NA** | — | **2.317×** |

NA shape computed by running `compute_transform_and_shape(REGION_BBOX_3857["na"], 25000.0)`
(`backend/app/services/builder/raster_grid.py:335`), the same call the baseline
loader makes at `backend/app/services/climatology.py:242-245`.

Baseline assets are written `dtype="float32"`, `compress="deflate"`, **no
predictor** — `build_climatology_baseline_assets.py:225-233` and
`build_precip_accumulation_climatology_assets.py:217-227`. Every GiB number
below is **raw float32** (deflate on float32 without `predictor=3` saves little;
see §5 R8). Treat compression as headroom, not budget.

---

## 1. Baseline asset footprint

### 1a. Structure that exists today (read from the code, not assumed)

Two distinct baseline shapes, both region-keyed and **model-agnostic** — the path
is `climatology/{version}/{source}/baseline/{field}/{region}/{period}/`
(`climatology.py:100-109`), with no model component.

| Kind | Filename | Buckets | Consumer |
|---|---|---|---|
| Instantaneous | `doy_{001..366}_h{00,06,12,18}.tif` `climatology.py:152` | 366 doy × 4 synoptic hours = **1464 / field** | `load_climatology_baseline` `climatology.py:185` |
| Precip accumulation | `doy_{001..366}.tif` `climatology.py:174` | 366 doy, no hour = **366 / window** | `load_accumulation_climatology_baseline` `climatology.py:287` |

- 4 hours only: `SUPPORTED_HOURS = (0, 6, 12, 18)` `build_climatology_baseline_assets.py:32`.
  Off-synoptic forecast hours are served by the round-down fallback
  `_synoptic_bucket_valid_time` `climatology.py:177-182`.
- 366 (not 365) is deliberate and leap-day-aware — `ERA5_CLIMATOLOGY_RUNBOOK.md:155-163`.
- Values are smoothed with a 15-day circular window by default
  (`build_climatology_baseline_assets.py:346`, `_smooth_doy_series:187`).

### 1b. The anomaly variables in the current GFS catalog

| Var | `derive` | `baseline_field` | Baseline kind | Files / field |
|---|---|---|---|---|
| `tmp2m_anom` `gfs.py:505` | `anomaly_departure` | `tmp2m` | instantaneous | 1464 |
| `tmp850_anom` `gfs.py:415` | `anomaly_departure` | `tmp850` | instantaneous | 1464 |
| `hgt500_anom` `gfs.py:478` | `anomaly_departure` | `hgt500` | instantaneous | 1464 |
| `precip_5d_anom` | `precip_accum_anomaly_departure` `gfs.py:1066` | `precip_5d` | accumulation | 366 |
| `precip_7d_anom` | ″ | `precip_7d` | accumulation | 366 |
| `precip_10d_anom` | ″ | `precip_10d` | accumulation | 366 |
| `precip_16d_anom` | ″ | `precip_16d` | accumulation | 366 |

GFS uses the 384h precip set (`PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY` `gfs.py:1031-1036`,
applied `gfs.py:1072-1078`). The 360h set (`precip_15d`) exists for AIFS/EPS/ECMWF
(`aifs.py:164`, `eps.py:420`, `ecmwf.py:238`) and is **not** needed for GFS global.

### 1c. Cost of the same structure at global 1440 × 721

| Field | Files | NA today (raw f32) | **Global (raw f32)** |
|---|---:|---:|---:|
| `tmp2m` | 1464 | 2.443 GiB | **5.663 GiB** |
| `tmp850` | 1464 | 2.443 GiB | **5.663 GiB** |
| `hgt500` | 1464 | 2.443 GiB | **5.663 GiB** |
| `precip_5d` | 366 | 0.611 GiB | **1.416 GiB** |
| `precip_7d` | 366 | 0.611 GiB | **1.416 GiB** |
| `precip_10d` | 366 | 0.611 GiB | **1.416 GiB** |
| `precip_16d` | 366 | 0.611 GiB | **1.416 GiB** |
| **Total** | **5856** | **9.77 GiB** | **22.65 GiB** |

Sub-totals: instantaneous fields **16.99 GiB**, precip windows **5.66 GiB**.

**One-time, shared, not per-model.** Because the baseline path carries no model
key (`climatology.py:100-109`), this same 22.65 GiB serves GFS, AIGFS, AIFS and
ECMWF global anomalies. It is paid once.

---

## 2. Per-run artifact delta

**Frame cadence.** `GFS_INITIAL_FHS = range(0,241,3) + range(246,385,6)`
`gfs.py:204` → **105 frames** per full-cycle variable.
Precip anomalies are fh-constrained: `GFS_CONSTRAINTS_BY_VAR_KEY[var] = {"min_fh": target_fh}`,
plus `"max_fh"` only for the static one (`gfs.py:1192-1198`,
`PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY` `gfs.py:1037-1038`). So
`precip_16d_anom` is exactly one frame at fh384; the other three have a floor but
no ceiling in the constraint dict, so they are bounded below by 1 frame and above
by the count of fhs ≥ their `min_fh`.

**No display-prep.** `_GRID_DISPLAY_PREP_BY_MODEL_VAR`
(`backend/app/services/grid_display_prep.py:32-231`) has GFS entries only for
`precip_total`, `snowfall_total`, `snowfall_kuchera_total`, `ptype_intensity*`.
**No anomaly variable appears.** The ×3 upsample (contract §7) therefore does not
apply — this is why the delta is small.

**Contours: `hgt500_anom` only.** It carries `contour_component: hgt500`,
`contour_interval: 6`, `contour_start: 480`, `contour_end: 624` (`gfs.py:490-497`).
`tmp2m_anom` (`gfs.py:505-523`), `tmp850_anom` (`gfs.py:415-437`) and the precip
anomalies (`_precip_anomaly_var_spec` `gfs.py:1042-1069`) carry no contour hints.

| Component | Low (precip anoms single-frame) | High (precip anoms full range) |
|---|---:|---:|
| `tmp2m/tmp850/hgt500_anom` frames | 3 × 105 = 315 | 315 |
| `precip_*_anom` frames | 4 | 65 + 49 + 25 + 1 = 140 |
| **Frames total** @ 2 076 480 B (contract §1) | 319 → **0.62 GiB** | 455 → **0.88 GiB** |
| `hgt500_anom` contours, 105 frames | ~0.1 GiB | ~0.3 GiB |
| Sidecar JSON | negligible | negligible |
| **Per-run delta** | **≈0.7 GiB** | **≈1.2 GiB** |

Contour band is an estimate, not a measurement: the sizing docs put *all* contour
GeoJSON at 2-4% of a converted run (`GLOBAL_MODEL_SIZING_SPIKE_2026-07-22.md:62,71`),
i.e. 0.45-0.9 GiB across every contoured variable in a 22.5 GiB run; `hgt500_anom`
is one of them. Note this set is a byte-for-byte duplicate of the existing global
`hgt500` contour set (same component, interval, start, end) — see §5 R9.

### Steady state

| | Runs | GiB |
|---|---:|---:|
| GFS global today, non-anomaly (operator-measured 23 GiB/run) | 6 | 138 |
| \+ anomaly per-run delta | 6 | +4.2 … +7.2 |
| \+ global baseline assets (one-time) | — | +22.65 |
| **New steady-state total** | | **≈165 … 168 GiB** |

**Reconciliation of the two published global figures.** `GLOBAL_DOMAIN_4326_CONTRACT.md:107`
says ≈161 GiB; the operator figure is 138 GiB. Both are right for different
retention: the 161 GiB derives from the measured **7-run** 3857 total 390.7 GiB
÷ 2.478 per-frame ratio (`GLOBAL_STORAGE_G1_INVESTIGATION_2026-07-29.md:136-143`),
which is 22.5 GiB/run — the same run size as the 23 GiB measurement, just ×7
instead of ×6. **The two numbers do not disagree about run size.** This doc uses
keep_runs = 6.

---

## 3. Disk position

Operator-supplied inputs: prod volume 2 TB (≈1863 GiB), currently projecting
≈40% (≈745 GiB) with GFS global at 138 GiB.

| Scenario | Added GiB | Volume used | % of 2 TB |
|---|---:|---:|---:|
| Baseline (stated) | — | 745 | 40.0% |
| \+ global baselines only (22.65) | +22.7 | 768 | 41.2% |
| \+ per-run anomaly delta, low | +4.2 | 772 | 41.4% |
| **\+ per-run anomaly delta, high** | **+7.2** | **775** | **41.6%** |
| Stress: precip baselines doubled by a 2nd reference period | +5.7 | 781 | 41.9% |
| Stress: anomalies wrongly given display-prep (×3 upsample, ~9× cells) | +50 | 825 | 44.3% |

**Nothing in Phase 3A pushes past 50%.** The worst single-assumption stress case
lands at 44.3%.

The >50% risk lives elsewhere and should be flagged: each *additional* global
model (AIGFS, AIFS, ECMWF) adds another ~138 GiB of run artifacts at keep_runs=6.
Four global models ≈ 552 GiB → ≈70%. Baselines do not multiply (§1c), run
artifacts do. That is a Phase 3 rollout decision, not a Phase 3A one.

---

## 4. ERA5 download and compute scope for the VM

### Fields (from `stage_era5_climatology_source.py:33-55`)

| Baseline field | ERA5 dataset | Variable | Level | Staged units |
|---|---|---|---|---|
| `tmp2m` | single-levels | `t2m` | — | K |
| `tmp850` | pressure-levels | `t` | 850 hPa | K |
| `hgt500` | pressure-levels | `z` | 500 hPa | m (÷ 9.80665, `:21,:127-128`) |
| `precip_*d` | single-levels | `tp` | — | inches (`stage_era5_precip_daily_source.py:30-33`) |

### Year range used by the NA baselines

**1991-2020**, hard-wired as the reference-period label on every GFS anomaly
VarSpec (`gfs.py:429, 488, 515, 1054`) and as the runbook's build invocation
(`ERA5_CLIMATOLOGY_RUNBOOK.md:111, 136-138`). 30 years.

### Download volume, global 0.25°

| Field set | Cadence | Time slices (30 yr) | Uncompressed at 3.961 MiB/slice |
|---|---|---:|---:|
| `t2m`, `t850`, `z500` | 4 /day `:246` | 43 830 × 3 = 131 490 | **≈509 GiB** |
| `tp` (precip) | **hourly** — accumulation field, summed to UTC daily `stage_era5_precip_daily_source.py:334-343` | ≈262 980 | **≈1 017 GiB (≈1.0 TiB)** |
| **Total raw** | | | **≈1.5 TiB** |

CDS delivers packed/compressed NetCDF, so realistic *wire* volume is roughly half
these numbers (order 0.7-0.8 TiB); the uncompressed figure is what matters for VM
scratch after decode. **Precip is ~2/3 of the entire download** because it is the
only hourly field.

### Staged intermediates on the VM

| Stage output | Files | Size (raw f32 GeoTIFF) |
|---|---:|---:|
| Instantaneous staged rasters (`{YYYYMMDDHH}_{field}.tif` `:101-110`) | 131 490 | ≈509 GiB |
| Daily precip staged rasters (`{YYYYMMDD}_precip_daily.tif` `:105-113`) | 10 958 | ≈42 GiB |
| Final baseline assets | 5 856 | 22.65 GiB |

**Plan VM scratch at 2 TB** (raw ERA5 + staged + output, minus whatever is deleted
between stages). If precip is deferred (§6 D2), 1 TB suffices.

### Compute shape (orders of magnitude, from script structure)

- Instantaneous build reprojects each source raster once per (doy, hour) bucket
  pass: `_bucket_mean` `build_climatology_baseline_assets.py:144-184`, called
  1464 times/field over ~30 rasters each = 43 830 reprojects/field. Each is a
  1.04 M-cell **4326 → 4326 identity-grid** warp (§5 R6) — milliseconds of CPU;
  the wall clock is I/O and NetCDF decode, not the warp. Order: **hours per
  field**, single-threaded, not days.
- Precip build: 10 958 warps + 366 bucket means + 4 rolling-window sums
  (`_rolling_accumulations:185-199`). Trivially cheap in CPU; see R2 for its
  memory problem.
- Dominant wall-clock cost end-to-end is the **CDS download queue**, which is
  rate-limited per user and historically the multi-day part of this workflow.

---

## 5. Structural risks — what makes this more than a grid swap

| # | Risk | Evidence | Severity |
|---|---|---|---|
| R1 | **Both baseline loaders hard-require EPSG:3857** and reject anything else. | `climatology.py:256-257`, `climatology.py:327-328` | Known, planned (plan §3) |
| R2 | **Precip build holds every warped daily raster in RAM.** `_daily_normals_by_doy` appends all 10 958 arrays to `buckets` before averaging. At global that is 10 958 × 3.961 MiB ≈ **42 GiB resident** (≈19 GiB even at NA). | `build_precip_accumulation_climatology_assets.py:162-181` | **Blocker for precip. Must be made streaming/incremental first.** |
| R3 | **`baseline_region` is hard-coded `"na"` in every GFS anomaly VarSpec.** The derive path uses it as *both* the baseline lookup region and the derive target region, so a global build would load NA baselines and then die on the shape check rather than degrade. | `gfs.py:427, 486, 513, 1053`; consumed `pipeline.py:1438` and `derive.py:1145-1152`; shape check `derive.py:1187-1191` | **High — the largest code change in the phase, and it is not in the plan's §2-4 list.** Needs per-domain baseline-region resolution. |
| R4 | `get_baseline_grid_params` only knows metre grids (`conus`, `na`) and raises `KeyError` for anything else; a degrees analogue is needed. | `climatology.py:18-23, 62-70` | Medium |
| R5 | `_resolve_derive_target_grid` currently swallows that `KeyError` for native-geographic regions and returns "no shared grid", disabling the component warp cache. With real baselines it must resolve a real shared grid. Correctness is fine either way; this is a build-time perf cost. | `pipeline.py:1444-1461` | Low (perf) |
| R6 | **Same-grid reproject.** Staged ERA5 rasters land on *exactly* the target grid, so the build script's `bilinear` `reproject` is a no-op warp. ~~May still perturb float bits.~~ **Corrected 2026-07-30 (Wave 1 implementation):** the float-perturbation premise did NOT reproduce — a same-grid `reproject` was measured bit-identical to the input, NaNs included (the staged raster carries `nodata=NaN`, so nothing bleeds). The same-grid fast path shipped anyway, justified on (a) skipping 43 830 pointless warps per field and (b) being a *guaranteed* no-op rather than one that happens to be a no-op for this alignment. Do not chase a bit-perturbation bug here. Note the serving path is different: there the component arrives with `src_nodata=None`, and a reproject **does** bleed NaNs (100 → 121 cells measured), which is why `_warp_component_to_target_grid` must delegate to the index roll. | `build_climatology_baseline_assets.py:130-140` | Low; shipped for speed, not correctness |
| R7 | `_convert_values` only accepts `tmp2m`/`tmp850`/`hgt500` and raises otherwise — fine, but confirms the instantaneous build has no precip route (they are separate scripts with separate CLIs). | `build_climatology_baseline_assets.py:81-102, 342` | Informational |
| R8 | Deflate is applied **without `predictor=3`**. On a synthetic smooth field, adding it cut the file from 0.68× to 0.33× of raw at 1440×721 — i.e. roughly halves the 22.65 GiB. *Synthetic measurement, not real ERA5; treat as directional only.* Changing it would diverge new global assets from existing NA assets' encoding. | `build_climatology_baseline_assets.py:225-233` | Optional optimisation |
| R9 | `hgt500_anom` emits a contour set byte-identical to `hgt500`'s (same component/interval/start/end). Global doubles that duplication. | `gfs.py:490-497` vs the `hgt500` spec | Low; dedupe candidate |
| R10 | Leap day: doy 366 has only 8 samples in 1991-2020. The 15-day circular smoothing covers it. Unchanged from NA. | `build_climatology_baseline_assets.py:187-207, 346` | None |
| R11 | Monthly ERA5 precip files are loaded as whole cubes (`data_array.values`): 744 h × 1 038 240 cells × 4 B ≈ **2.9 GiB per file** at global. Fine for monthly files, **fatal (≈35 GiB) if the archive is fetched as yearly files.** Fetch monthly. | `stage_era5_precip_daily_source.py:322` | Medium — an operator instruction, not a code fix |

### R-units: unit conventions per variable (checked, not assumed)

| Var | Baseline stored as | `base_conversion` | `anomaly_conversion` | Output units |
|---|---|---|---|---|
| `tmp2m_anom` | **°F** (`_convert_values:86-93` always emits °F) | — | — | F `gfs.py:522` |
| `tmp850_anom` | **°F** (same code path) | `c_to_f` `gfs.py:423` | `f_to_c_delta` `gfs.py:424` | C `gfs.py:436` |
| `hgt500_anom` | **dam** (`_convert_values:95-100`, gpm ÷ 10) | — | `dam_to_m` `gfs.py:489` | m `gfs.py:503` |
| `precip_*_anom` | **inches** (`build_precip…:236`) | — | — | in `gfs.py:1068` |

Conclusion: **only `tmp850_anom` and `hgt500_anom` carry conversions**, and both
sit on the *anomaly* side, not the storage side. Global baselines must reproduce
the identical storage conventions — °F for both temperature fields (do **not**
"fix" `tmp850` to °C; the °C ladder is produced by `f_to_c_delta` at derive time),
dam for heights, inches for precip. All four are already enforced by the shared
build scripts provided the same `--units-in` flags are used
(`--units-in K` for temps, `--units-in m` for `hgt500` since the stage script has
already divided by *g*, `--units-in inches` for precip).

### R-poles and R-longitude: both already correct

- **Longitude.** ERA5 CDS delivers 0…359.75. Both stage scripts normalize with
  `((lon + 180) % 360) - 180` then `argsort`
  (`stage_era5_climatology_source.py:69-73`, `stage_era5_precip_daily_source.py:49-53`),
  producing exactly −180 … +179.75. That **is** the contract's rolled layout
  (`GLOBAL_DOMAIN_4326_CONTRACT.md:38-45`), with no duplicate wrap column. The
  roll is index arithmetic, no interpolation — matching contract §5.
- **Latitude / poles.** `_normalize_latitudes` flips to north-up
  (`:76-80`), and `_transform_from_latlon` (`:93-98`) computes
  `west = lon[0] − 0.125 = −180.125`, `north = lat[0] + 0.125 = +90.125`. That is
  **byte-identical to `REGION_BBOX_4326["global"]` at `raster_grid.py:63`.** Both
  poles are literal rows; no pole synthesis is needed or performed.
- Consequence: **the staged ERA5 rasters already land on the contract grid.** For
  the global path the "warp" in the build script is an identity transform (R6).
  That is the single biggest reason this phase is cheaper than it looks.

---

## 6. Recommendation

**GO with caveats**, sequenced in two waves. Disk is not the constraint —
22.65 GiB of baselines and ≤7.2 GiB of run artifacts move prod from ~40% to
~41.6%. The constraints are (a) one unlisted code change, (b) one memory blocker
confined to precip, and (c) ~1 TiB of hourly ERA5 download that buys only 5.66 GiB
of the 22.65 GiB.

**Wave 1 — `hgt500_anom`, `tmp2m_anom`, `tmp850_anom`.** 16.99 GiB baselines,
≈509 GiB ERA5 download, ≈0.65 GiB/run. Covers the stated driver (hgt500 is the
most-cited TWF variable, plan `:555-556`). Needs R1, R3, R4 fixed; R2 does not
apply. VM scratch ≈1 TB.

**Wave 2 — the four precip anomaly windows.** +5.66 GiB baselines but ≈1.0 TiB
of hourly ERA5 and a mandatory rewrite of `_daily_normals_by_doy` (R2, 42 GiB
resident). Only +0.03 to +0.26 GiB/run. Poor cost/benefit to run concurrently
with Wave 1.

Do not skip R3 in planning: the plan's scope list (`:576-593`) covers the CRS
validation and the derive target grid, but **not** the hard-coded
`baseline_region: "na"` in the four GFS VarSpec hint blocks. That is the change
that actually makes an anomaly resolve a global baseline.

### Decisions Brian must make

**D1 — Reference period.** *Recommendation: keep 1991-2020.* Any other period
makes global and NA anomalies numerically non-comparable at their overlap, which
would invalidate the parity spot-check the plan requires (`:592`). Shortening the
year range to cut the precip download would save proportionally (a 10-year
precip baseline is ~340 GiB instead of ~1017 GiB) but produces a baseline that
disagrees with the NA one over the same geography. Not recommended.

**D2 — Variable subset for the first global anomaly ship.** *Recommendation:
Wave 1 only (the three instantaneous fields).* It delivers the go-live driver,
avoids the R2 rewrite, and cuts the ERA5 download by two thirds. Deferring precip
anomalies means the G6 checklist line inverts partially — global capabilities
would declare three anomaly variables, not seven — so the test pins
(`test_gfs_global_domain.py:149-161`) must express a per-variable allowlist
rather than a blanket "no anomaly declares global". Confirm that partial
inversion is acceptable before writing the tests.

### Flagged uncertainties

- The contour byte estimate for `hgt500_anom` (§2) is scaled from an aggregate
  2-4% share, not measured per-variable. `measure_global_sizing.py:596-598,
  875-888` computes `per_var_bytes` / `per_suffix_bytes` at runtime but no doc
  records the breakdown. If the ≈1.2 GiB high case matters, measure it.
- Whether `precip_5d/7d/10d_anom` publish one frame or a range: `gfs.py:1192-1198`
  sets only `min_fh` for those three (no `max_fh`), so the range is the code-level
  upper bound; actual scheduler behaviour was not traced. Both bounds are carried
  through §2, and the difference is ≤0.3 GiB/run either way.
- The predictor=3 compression figure (R8) is from a synthetic smooth field, not
  real ERA5. It is a reason to *test*, not a number to budget against.
