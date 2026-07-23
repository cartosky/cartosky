# Global Model Sizing Spike — 2026-07-22

Disk (and incidental capacity) sizing for expanding the published domain of the four
global-capable models — **GFS, ECMWF, AIFS, AIGFS** — from NA to full global extent,
plus first-pass sizing of two SST candidate sources. Measurement only: this doc makes
**no hardware-tier recommendation and no go/no-go call**; it produces the numbers those
decisions are made from. GEFS/EPS ensembles are out of scope (see
`ENSEMBLE_MEMBER_PIPELINE_PLAN.md`); HRRR/NBM/RTMA are CONUS-only and have no global variant.

**Method.** Two-actor workflow: scripts developed on the Mac and committed
(`backend/scripts/measure_global_sizing.py`, commits `47655776`→`f958ef37`;
`backend/scripts/measure_sst_sizing.py`, commit `534a01ce`), executed manually on prod by
Brian as the `cartosky` user, confined to `/opt/cartosky-dev`, resource-wrapped
(`systemd-run --scope MemoryHigh=4G/MemoryMax=6G CPUWeight/IOWeight=50` + `nice`/`ionice`).
One representative full run per model (two for ECMWF — one per cycle type) was fetched and
converted through the **production binary path** (`build_frame` →
`write_grid_frame_for_run_root`) at a runtime-injected `global` region: world Web Mercator
bbox (±85.05° lat, unrepresentable poles dropped — same projection limits as the current
viewer), at each model's existing NA target resolution. Converted totals were compared
against the **same run's** live NA published directory (read-only). All figures below are
measured from the pasted run summaries (JSON detail in `/opt/cartosky-dev/reports/` and
`/opt/cartosky-dev/sst/reports/`); anything derived by arithmetic is labeled as such.

---

## 1. Phase 1 — baseline currency (2026-07-21)

| Volume | Size | Used | Avail | Notes |
|---|---|---|---|---|
| `/dev/vda4` (root) | 2.0T | **594G** | 1.4T | hosts `/opt/cartosky/data/{staging,published,manifests}` |
| `/dev/vdb` (`herbie_cache_ssd`) | 492G | **134G** | 338G | raw Herbie GRIB cache only |

NA published at check time: GFS **62G** (7 runs), ECMWF **106G**, AIFS **61G** (7 runs),
AIGFS **9.5G** (7 runs). All within the ±10% gate vs. the 2026-07-21 manual baseline
(ECMWF −9.4% — explained below by the retention window's cycle mix, not drift).

**ECMWF retention mix (measured 2026-07-22):** the published window held **6 runs at a
structural 1:1 mix** — 3 full-horizon (00z/12z, ~23G each) + 3 short-horizon (06z/18z,
~13G each) ≈ 108G. The plan-time "8 runs / 117G" baseline was a different moment of the
age-based retention. Extrapolations below use the observed 6-run window, with the 8-run
(4+4) variant shown alongside.

## 2. Phase 2 — measured global-domain results

One full run per row, all published variables + companions, through the production
conversion path. "NA published" is the same run's live directory. Zero frame failures in
the final dataset (transients resumed to completion).

| Model / run | Global grid | Frames | Converted global | NA published (same run) | **Multiplier** | Raw GRIB (global) | Peak RSS | Wall (handicapped) |
|---|---|---|---|---|---|---|---|---|
| GFS 20260721_06z | 25 km, 1604² | 2 761 | **55.82 GiB** | 10.24 GiB | **5.45×** | 1.95 GiB | 1 181 MB | 1h27m¹ |
| AIFS 20260721_06z | 9 km, 4454² | 948 | **54.56 GiB** | 9.99 GiB | **5.46×** | 0.64 GiB | 1 862 MB | 3h59m |
| ECMWF short 20260721_06z (144 FH) | 9 km, 4454² | 1 127 | **69.22 GiB** | 12.47 GiB | **5.55×** | 0.75 GiB | 1 952 MB | 4h39m |
| ECMWF full 20260721_12z (360 FH) | 9 km, 4454² | 2 046 | **124.21 GiB** | 22.57 GiB | **5.50×** | 1.31 GiB | 1 880 MB | 7h28m |
| AIGFS 20260722_06z | 25 km, 1604² | 757 | **8.40 GiB** | 1.57 GiB | **5.37×** | 0.87 GiB | 986 MB | 1h36m² |

¹ second pass; 525 of 2 761 frames were pre-built by the interrupted first pass.
² paced at `--frame-delay 3` for NOMADS courtesy; excludes the earlier aborted attempt.

**Headline: the NA→global converted multiplier is ~5.5× (5.37–5.55) for every model and
both grid resolutions** — consistent with the pixel-count ratio of the world-mercator grid
vs. the NA bbox at equal meters-per-pixel, slightly under it because contour GeoJSON and
sidecars don't scale with area. The ensemble plan's estimated "~5.8×" global multiplier is
now a measured 5.4–5.55×.

**Raw GRIB / vdb: no change from global.** Herbie byte-range subsets are already global
extent (the NA crop happens at warp time), so raw per-run footprints (0.64–1.95 GiB above)
match today's. **The `herbie_cache_ssd` volume needs no additional capacity for global.**

Converted composition (consistent across models): ~73–78% grid `.bin`, ~18–23%
display-prep/companion rasters ("other"), ~2–4% contour `.geojson`, sidecar JSON noise.

## 3. Extrapolation at current retention (derived arithmetic)

Global converted per run × observed retention. ECMWF weighted by the measured 1:1
cycle mix.

| Model | Retention | Global total | Today's NA total | Delta |
|---|---|---|---|---|
| GFS | 7 runs | **390.7 GiB** | 62G | +329 |
| AIFS | 7 runs | **381.9 GiB** | 61G | +321 |
| AIGFS | 7 runs | **58.8 GiB** | 9.5G | +49 |
| ECMWF | 6 runs (3 full + 3 short) | **580.3 GiB** | 108G | +472 |
| **Combined** | | **≈ 1 412 GiB** | ≈ 240.5G | **≈ +1 171 GiB** |

8-run ECMWF variant (4+4): ECMWF **773.7 GiB**, combined **≈ 1 605 GiB** (delta ≈ +1 365 GiB).

Global replaces NA (NA ⊂ global), so the delta is global-minus-current, all landing on the
volume hosting `/opt/cartosky/data` (vda4 today). At the 6-run mix, vda4 would go from
594G/2.0T (31%) to **≈ 1.77T/2.0T (~88%)** — beyond comfortable headroom on the current
volume alone.

## 4. Resolution scenarios for ECMWF/AIFS (derived arithmetic)

ECMWF/AIFS open data is disseminated at 0.25° (~28 km projected); the current 9 km NA
target is ~3× oversampling of the source, while GFS already publishes at ~native 25 km.
Publishing **global** ECMWF/AIFS at a coarser target than NA is a per-region config
(`grid_meters_by_region`) — NA could stay 9 km untouched. Scaling: raster components by
pixel ratio, contour GeoJSON by linear ratio.

| Scenario (ECMWF/AIFS global grid) | ECMWF (6-run) | AIFS (7-run) | Combined all four | Delta vs today | vda4 after (of 2.0T) |
|---|---|---|---|---|---|
| **9 km** (as measured) | 580.3 GiB | 381.9 GiB | **≈ 1 412 GiB** | +1 171 GiB | ~88% |
| **14 km** | ≈ 245 GiB | ≈ 160 GiB | **≈ 855 GiB** | +615 GiB | ~60% |
| **25 km** (GFS-parity, ~native source) | ≈ 80 GiB | ≈ 52 GiB | **≈ 582 GiB** | +341 GiB | ~46% |

Build wall-clock scales the same way (Section 6) — resolution is simultaneously the disk
lever and the freshness lever for the burst models.

## 5. SST candidates (Phase 3, measured 2026-07-22)

Both measured end-to-end on prod through the production binary writer
(`write_grid_frame_for_run_root`, runtime `(sst, sst)` packing: u16, 0.01 °C precision,
−5…650 °C range) at both model-grid tiers. Converted cost is grid-determined — identical
for both candidates.

| Candidate | Source / cadence | Raw `.nc`/day | Native grid | Converted/day @25 km | Converted/day @9 km | Access |
|---|---|---|---|---|---|---|
| **OISST v2.1** | NCEI HTTPS, daily 0.25° | **1.50 MiB** | 1440×720 | bin 4.91 + sidecars 2.72 = **7.62 MiB** | bin 37.84 + sidecars 13.97 = **51.81 MiB** | no auth; finals lag ~2 weeks, preliminary ~2 days |
| **Geo-Polar Blended** | NCEI GHRSST archive (OSPO L4), daily 5 km | **18.30 MiB** | 7200×3600 | **7.70 MiB** | **54.00 MiB** | no auth; resolved via Night-blend template, day-blend 404'd for probed dates |
| MUR (JPL 0.01°) | PO.DAAC | — | 36000×17999 | not converted | not converted | **misfit (measured):** HEAD → 303 redirect to Earthdata-gated signed URL; AWS mirror `s3://mur-sst` is zarr — doesn't fit the single-file netCDF flow |

Sanity: OISST −1.80…34.86 °C, Geo-Polar −2.00…34.51 °C (physically correct on prod —
no GDAL scale/offset double-application). Valid-pixel (ocean) fraction ~66–67%.

Retention scenarios (derived; totals incl. compression sidecars):

| Retention | @25 km | @9 km |
|---|---|---|
| 30 days | 0.23 GiB | 1.6 GiB |
| 90 days | 0.68 GiB | 4.7 GiB |
| 365 days | 2.7 GiB | 19.2 GiB |

**SST is a rounding error next to the model expansion** at any plausible retention.
Product choice and placement are explicitly open (Section 8).

## 6. Build-time / freshness findings

Wall-clock above is **handicapped** (lowest CPU/IO priority, strictly sequential
single process, fetch serialized with convert) — an upper bound, not a production
prediction. The load-bearing measured fact is per-frame conversion cost tracking pixel
count: **~2.3 s/frame at 1604²** (GFS) vs **~13–15 s/frame at 4454²** (AIFS/ECMWF),
a ~6× ratio for a 7.7× pixel ratio.

- **GFS / AIGFS (progressive upstream arrival):** builds interleave with dissemination;
  the ~5.5–6× per-frame CPU increase is a *capacity* question (does per-FH build still fit
  inside the inter-FH arrival cadence), not a user-visible latency cliff.
- **ECMWF / AIFS (open-data tier — the entire run drops at once):** burst-build wall-clock
  **is** user-facing latency, one-for-one. Measured handicapped sequential bursts: AIFS
  ~4h, ECMWF-full ~7.5h at 9 km global. At 25 km the same bursts scale to roughly ~37min /
  ~1.2h (derived via the measured per-frame ratio) before any parallelization or priority
  help. Mitigation space (open, Section 8): coarser global grid, intra-run parallel frame
  builds, tiered publish (core variables first), ECMWF's paid real-time feed (which
  restores progressive arrival), and more cores (the separate tier decision).

**Live-service interference (observed, not hypothesized):** during the 4454² global
builds, the measurement — despite `nice`/`ionice`/`IOWeight=50` — drove swap to 100% full
twice (page-cache/writeback pressure; process RSS stayed <2 GB) and visibly **delayed the
live MRMS scheduler** on the shared root volume. `ionice` has limited effect under modern
IO schedulers. Global-scale build load co-located with time-sensitive products wants
volume-level isolation, which block storage also provides (Section 7).

## 7. Memory findings vs. the 3G scheduler cap

Single-frame sequential peak RSS: **986–1 181 MB at 1604²**, **1 862–1 952 MB at 4454²** —
under the schedulers' `MemoryHigh=3G` with ~1G headroom at the large grid. Two caveats:
(a) at ~1.9 GB/frame, **two concurrent 4454² frame builds in one scheduler process would
exceed 3G** — production concurrency at 9 km global needs either serialization of
large-grid frames or a raised cap; (b) prod baseline memory is already tight (chronic
5–6 GiB swap; see EPS memory audit) — the swap-fill events in Section 6 happened at
system level with modest process RSS, so RSS headroom alone is not the whole picture.

## 8. Block-storage recommendation (framed per spec: "add N GB to which volume")

`/dev/vdb` (herbie cache): **add 0 GB** in every scenario — raw downloads are already
global-extent.

For the volume hosting `/opt/cartosky/data` (vda4 today), at Netcup Local Block Storage
~€0.012/GB/month:

| Scenario | Delta to absorb | Recommendation | ~Cost/mo |
|---|---|---|---|
| 9 km global ECMWF/AIFS | +1.17–1.37 TiB | **add 2 000 GB** block storage for the published tree (keeps vda4 ≤ ~50% and isolates build IO from MRMS/live serving) | ~€24 |
| 14 km global ECMWF/AIFS | +615 GiB | **add 1 000 GB** | ~€12 |
| 25 km global ECMWF/AIFS | +341 GiB | **add 0 GB required** (vda4 lands ~46%); **optional 500 GB** purely for the IO-isolation benefit observed in Section 6 | €0 / ~€6 |

Plus SST: +0.2–19 GiB depending on grid/retention — absorbed by any row above.
Mount/layout choice (whole published tree vs. global-only artifacts) is an open decision.

## 9. Incidental constraints & findings (surfaced by the spike)

- **AIGFS/NOMADS per-IP budget is near-saturated by normal NA operations at run tail** —
  observed 302 anti-abuse blocks affecting the live scheduler's final frames with no spike
  traffic in flight. Global adds no fetch load (subsets are already global), but there is
  zero headroom for backfills/reprocessing. The AWS EAGLE mirror
  (`noaa-nws-graphcastgfs-pds`) lags NOMADS ~4–6h (batch-copied) — backfill relief only.
  An aws-first source flip with NOMADS fallback is implemented and verified in the working
  tree (side task, uncommitted; scheduler+API restarts required on deploy).
- **Global anomaly content is wrong until ERA5 baselines exist at global extent.** All
  `*_anom` variables built (sizes valid for this spike) against NA-extent baselines via a
  runtime grid-table injection; the derive/contour path
  (`pipeline._build_contour_metadata_for_variable` hardcodes
  `derive_component_warp_cache=True`) hard-requires a `global` entry in
  `climatology._BASELINE_SOURCE_GRID_METERS`.
- **NA-tuned value-sanity ranges trip at global** (e.g. Antarctic dewpoints at −111 °F
  logged range warnings). Warn-only today; per-variable ranges need widening before a real
  global product.
- **The spike's `global` region exists only in-memory.** Productionizing requires real
  entries in `regions.py`, `cog_writer` bbox/grid tables, `grid_meters_by_region`,
  climatology tables, and model registry region lists — deliberately not committed here
  because schedulers build every registered region.
- Measurement-run reports (per-var detail, failures, probe logs) persist at
  `/opt/cartosky-dev/reports/` and `/opt/cartosky-dev/sst/reports/`.

## 10. Open decisions

| # | Decision | Inputs from this spike | Notes |
|---|---|---|---|
| 1 | ECMWF/AIFS global grid resolution (9/14/25 km) | Section 4 disk + Section 6 burst-latency scaling; source is 0.25° | Simultaneously the biggest disk and freshness lever; NA can stay 9 km regardless |
| 2 | SST product choice | Section 5: converted cost identical; differs in raw size, native detail (0.25° vs 5 km), finals lag, blend variant availability | MUR requires Earthdata auth or zarr tooling — a separate integration spike if wanted |
| 3 | SST placement (standalone layer vs overlay vs model-tied) & cadence/retention | Section 5 scenarios | Explicitly out of spike scope |
| 4 | Block-storage size + mount/layout (whole published tree vs global-only) | Section 8 | IO isolation benefit applies even in the 25 km scenario |
| 5 | ECMWF/AIFS burst-latency mitigation (parallelism, tiered publish, paid feed, cores) | Section 6 | Interacts with #1; hardware tier is a separate decision, not made here |
| 6 | Global ERA5 baseline generation | Section 9 | Prerequisite for any global anomaly variable being *correct* |
| 7 | Production `global` region registration + scheduler rollout shape | Section 9 | Includes per-region variable capability review and sanity-range widening |
| 8 | AIGFS EAGLE (AWS) source flip deploy | Section 9; implementation verified, uncommitted | Backfill relief only (mirror lags ~4–6h); scheduler+API restarts on deploy |
| 9 | Scheduler memory-cap posture for 4454² global builds | Section 7 | Only if a 9/14 km scenario is chosen |
