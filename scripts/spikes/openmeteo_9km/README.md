# Open-Meteo S3 ingestion spike (Phase 1) — 2026-08-04

Prototype measurements against the Open-Meteo AWS Open Data bucket
(`s3://openmeteo`, anonymous HTTP at `https://openmeteo.s3.amazonaws.com`),
which relays ECMWF IFS HRES at native 9 km (O1280) per-timestep during
dissemination — ~2 h ahead of the free delayed 0.25° path we ingest via Herbie.

## Phase 1 verdict: GO

NA latitude band (5–82°N, all lons, 44.2% of globe), 10 winter surface
variables, one live 12z `.om` file (111 MB whole):

- **16.85 MB transferred**, 33 range requests, 3.8 s (`measure_ten.py`)
- ≈ 8.5 GB/day upper bound at 504 files/day; NA longitude subsetting would cut ~2.4×
- Metadata open cost: ~5 KB
- Value spot-check vs Open-Meteo point API (same run/valid time): Denver
  25.4/25.4, Toronto 22.0/22.1, Seattle 26.0/26.0 °C

AIFS-vs-IFS Kuchera profile diff (`aifs_diff.py`, exact vendor formula from
`derive.py`, profile source the only variable): max-column-T MAE ≈ 0.4 K,
SLR ratio MAE ≈ 0.4 (scale 5–30), |ΔSLR|>4 ≈ 0% of snow-plausible area
(NA + Andes SH-winter, one summer cycle — needs multi-cycle + warm-nose cases).
Context: `ecmwf_aifs025_single` (full pressure-level temps) lands at/before the
IFS 9 km surface stream, while `ecmwf_ifs025` is a batch relay 1–5 min *behind*
ECMWF's own delayed portal — so AIFS is the only free real-time profile source.

## Phase 2 verdict: PASSED (2026-08-04)

`phase2_regrid.py` — one precomputed reduced-Gaussian bilinear sampler (exact
Gaussian latitudes via Legendre roots) serves both build families:

- **NA 9 km EPSG:3857** (TAP-snapped, 1825×1893): sampler build 0.5 s once,
  apply 0.06 s/field. Flat-site oracle vs Open-Meteo point API within 0.4 °C
  (terrain sites differ more — the API does elevation downscaling; not a gate).
  The fetch band must be **4.5–82.5°N** (edge cell centers sit at 4.967/82.006°N,
  outside the naive 5–82 band).
- **Global 0.25° EPSG:4326** (point-registered, row 0 north): bilinear matches
  ECMWF's own `ifs025` product to **MAE 0.020 °C** (p95 0.05) — decode,
  latitudes, orientation, and antimeridian wrap all validated by one number.
  Bilinear beats box-mean (MAE 0.098, plus 63,648 empty polar cells): ifs025 is
  itself interpolated, not conservatively averaged. Full-globe read ≈ 4.8 MB/var.

## Phase 4 verdict: PASSED (2026-08-04, `phase4_reconcile.py`)

Fast path (O1280 native) vs legacy (ifs025 0.25° bilinear-upsampled), both on
the exact production NA 1825×1893 grid, same run/valid times:

| case | MAE | bias | corr | synoptic (1°) MAE |
|---|---|---|---|---|
| MSLP fh12 (alignment guard, hPa) | 0.050 | −0.0001 | 0.99986 | 0.020 |
| t2m fh12 | 0.176 °C | −0.004 | 0.9993 | 0.079 |
| gusts fh12 | 0.24 m/s | +0.000 | 0.9912 | 0.100 |
| precip 3 h fh93 | 0.046 mm | −0.000 | 0.9695 | 0.014 |

Signature is exactly right: near-zero bias + tight synoptic agreement + real
fine-scale differences (t2m p99 1.9 °C over terrain/coasts — that's the added
9 km information, the point of the exercise). Side-by-side PNGs:
`phase4_temperature_2m.png`, `phase4_precipitation.png` (early Phase 5
preview; formal gate still renders through the real builder).

**Units gotcha:** the SAME variable name carries different units per product —
9 km `pressure_msl` is Pa, ifs025 is hPa. A per-product units audit is
mandatory before wiring any variable into the builder.

## Phase 3 verdict: PASSED (2026-08-04, `phase3_semantics.py`)

The per-step convention holds for **all** accumulation vars and across both
cadence transitions:

- `precipitation` and `snowfall_water_equivalent`: hourly-regime fh12 single
  file is ~1/3 of ifs025's 3 h step (bias = −ref/1.5 exactly); summing hourly
  steps collapses to the 0.02/0.006 grid-noise floor. fh93 (first 3 h step) and
  fh150 (6 h regime) match ifs025 directly — bias ≤ 0.0012 mm.
- `showers` has no ifs025 counterpart; proven per-step by the cp ≤ tp
  constraint (max excess 0.1 mm = rounding; a run-cumulative field would dwarf
  per-step precip at fh150) and by mean scaling exactly with step length
  (0.06 → 0.22 → 0.44 mm across 1 h/3 h/6 h steps).

## Phase 3 headline finding (confirmed early, `phase3_accum_check.py`)

`.om` accumulation fields are **per-step de-accumulated**, NOT run-start
cumulative like raw ECMWF GRIB `tp` — and the step length is the product's
cadence at that forecast hour (9 km: hourly→FH90, 3 h→144, 6 h→360; ifs025:
3-hourly). Proof: single 9 km fh12 `precipitation` vs ifs025 fh12 shows a
uniform −0.23 mm bias; summing the 9 km fh10+11+12 hourly steps collapses it
to MAE 0.020 mm. The builder's cumulative derives (which difference GRIB
run-totals at fixed 3 h steps) must be adapted: accumulate per-step `.om`
values, with a per-source step ladder.

## Gotchas (hard-won, do not rediscover)

- **Grid orientation differs per product in the same bucket.** 9 km O1280
  files: north-first rows, lon 0→360°E. 0.25° `ifs025`/`aifs025` files: S→N
  rows. Detect per product (physical sanity check); never assume.
- **Naive `omfiles` reads issue one HTTP request per 1024-point chunk**
  (2,664 requests / 231 s for one variable). Compressed chunks for contiguous
  index ranges are contiguous in the file, so a block-caching fetch layer
  (`omblock.py`, 512 KB blocks) gets the same bytes in seconds. Mandatory.
- FH000 files lack accumulation/gust fields (`precipitation`, `snowfall_water_equivalent`, `wind_gusts_10m`, …).
- Values are °C, not K.
- O1280 octahedral geometry: row i (1-based from pole) has 16+4i points;
  2,560 rows; 6,599,680 points total (`o1280.py`).
- Object existence is truth; `latest.json` / `completed` flags lag reality.

## Design directive for the production source (Brian, 2026-08-04)

Build the ingestion source **model-agnostic**, not ECMWF-specific. The bucket
hosts many models unavailable via Herbie (ICON, GEM, UKMO, HRRR-15min, …) under
the same `data_spatial/{model}/` layout, manifest schema, and `.om` format.
Parameterize: model dir, variable list, grid decode (regular lat/lon vs reduced
Gaussian, orientation detection), step cadence/horizon per cycle. Keep the
GPLv2 `omfiles` reader in an isolated module so it is swappable.

## Files

- `omhttp.py` — CountingHTTPFS: duck-typed fsspec FS (needs only `cat_file`+`size`), exact byte/request accounting
- `omblock.py` — BlockCachedHTTPFS: 512 KB block-cached variant (production fetch pattern)
- `o1280.py` — octahedral reduced-Gaussian index geometry (row starts, lat band → 1D index range)
- `explore.py` — dump an `.om` file's variable tree / shapes / chunks / compression
- `measure_ten.py` — the Phase 1 measurement (10 vars, NA band, byte counts, city spot-check)
- `aifs_diff.py` — AIFS-vs-IFS profile temp → Kuchera ratio diff at a matched valid time

Run with a venv containing `omfiles numpy requests` (Python 3.11 used for the
spike). Dates/URLs are hardcoded to the 2026-08-04 12z run — bucket retention
is ~7 days, so update paths before rerunning.
