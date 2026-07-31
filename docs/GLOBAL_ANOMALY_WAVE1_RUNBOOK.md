# Global anomaly baselines — Phase 3A Wave 1 operator runbook

Status: **written 2026-07-30, not yet executed.**
Implements `docs/GLOBAL_ANOMALY_SIZING_3A_2026-07-30.md` §6 Wave 1 under the
locked operator decisions **D1** (reference period stays 1991-2020) and **D2**
(Wave 1 = the three *instantaneous* anomaly fields only).

Grid authority: `docs/GLOBAL_DOMAIN_4326_CONTRACT.md`.
Companion runbook for the existing NA/CONUS baselines:
`docs/ERA5_CLIMATOLOGY_RUNBOOK.md` — this document does not repeat CDS account
setup, only the deltas for the global target.

**Scope of this runbook**

| In scope | Out of scope |
|---|---|
| `tmp2m`, `tmp850`, `hgt500` global baselines | `precip_5d/7d/10d/16d` (Wave 2) |
| ≈509 GiB ERA5 instantaneous download | ≈1.0 TiB hourly `tp` download |
| ≈17 GiB of baseline assets, 4392 files | The `_daily_normals_by_doy` memory rewrite |

---

## 0. The one rule

> **Install the baselines on prod BEFORE deploying/restarting the scheduler
> with the Wave-1 code.**

The code degrades safely in the wrong order (§6), but baselines-first is the
only sequence that produces a complete first global run.

---

## 1. Generation VM

Generation runs on a **separate VM. Never on prod.** The build is I/O-bound and
would evict prod's GDAL cache and page cache for hours.

| Resource | Requirement | Why |
|---|---|---|
| Scratch disk | **1 TB** | ≈509 GiB raw ERA5 + ≈509 GiB staged GeoTIFFs is more than 1 TB if both are kept; delete raw NetCDF per field after staging (§3). |
| RAM | 8 GB | The instantaneous build streams: `_bucket_mean` accumulates a running sum, never a stack of rasters. (The 42 GiB resident problem is precip-only — sizing doc R2 — and precip is out of scope here.) |
| CPU | 2–4 cores | The build is single-threaded per field; run the three fields in parallel if the disk keeps up. |
| Python | The repo `.venv` plus the ERA5 prep venv (`xarray`, `netcdf4`, `cdsapi`) | Same as `ERA5_CLIMATOLOGY_RUNBOOK.md`. |
| Wall clock | Dominated by the **CDS download queue** (historically multi-day), not compute. Budget hours per field for staging + build. |

Checkout the repo on the VM at the commit that contains this runbook — the
build script's `--region global` support ships with it.

---

## 2. Download (operator-run; do NOT let an agent run this)

Three fields, 4 synoptic hours/day, 1991-2020 inclusive, 0.25°, **global**
(no area subset — the whole point is the full grid).

| Field | CDS dataset | Variable | Level |
|---|---|---|---|
| `tmp2m` | `reanalysis-era5-single-levels` | `2m_temperature` | — |
| `tmp850` | `reanalysis-era5-pressure-levels` | `temperature` | 850 hPa |
| `hgt500` | `reanalysis-era5-pressure-levels` | `geopotential` | 500 hPa |

- Hours: `00, 06, 12, 18` only. `SUPPORTED_HOURS` in
  `backend/scripts/build_climatology_baseline_assets.py:38` ignores anything else;
  off-synoptic forecast hours are served at read time by the round-down bucket
  (`backend/app/services/climatology.py:_synoptic_bucket_valid_time`).
- Years: **1991-2020**, all 30. Do not shorten — D1. A different period makes
  global and NA anomalies non-comparable at their overlap and invalidates the
  parity check in §7.
- **Request monthly files, not yearly.** Sizing doc R11: the staging script
  loads a file's whole cube; monthly is ~100 MB-scale, yearly is not.
- Volume: ≈509 GiB uncompressed after decode across the three fields; wire
  volume roughly half that.

Land each field under its own directory, e.g.:

```text
/data/era5-raw/single-levels/tmp2m/
/data/era5-raw/pressure-levels/tmp850/
/data/era5-raw/pressure-levels/hgt500/
```

---

## 3. Stage

**The staging script needs no global flag.** It is already region-agnostic and
already produces the contract grid: it normalizes longitudes to −180 … +179.75
by index roll (no interpolation), flips to north-up, and derives
`west = lon[0] − 0.125 = −180.125`, `north = lat[0] + 0.125 = +90.125` — which
is byte-identical to `REGION_BBOX_4326["global"]`
(`backend/app/services/builder/raster_grid.py:63`). Both poles are literal rows.

Run once per field (repeat for `tmp850`, `hgt500`):

```bash
source .era5-prep-venv/bin/activate

python backend/scripts/stage_era5_climatology_source.py \
  --input-root /data/era5-raw/single-levels/tmp2m \
  --stage-root /data/era5-stage \
  --field tmp2m \
  --start-year 1991 --end-year 2020 \
  --hours 0 6 12 18
```

Staged output roots:

```text
/data/era5-stage/era5/single-levels/tmp2m/{YYYY}/{YYYYMMDDHH}_tmp2m.tif
/data/era5-stage/era5/pressure-levels/tmp850/{YYYY}/{YYYYMMDDHH}_tmp850.tif
/data/era5-stage/era5/pressure-levels/hgt500/{YYYY}/{YYYYMMDDHH}_hgt500.tif
```

Expected per field: **43 830** rasters (30 yr × 365.25 d × 4 h), ≈170 GiB.

Sanity-check the grid on one staged raster before spending days on the rest:

```bash
python -c "
import rasterio
with rasterio.open('/data/era5-stage/era5/single-levels/tmp2m/1991/1991010100_tmp2m.tif') as ds:
    print(ds.crs, ds.width, ds.height, ds.transform)
"
# EXPECT: EPSG:4326 1440 721 | 0.25, 0.00,-180.12| ... | 0.00,-0.25, 90.12| ...
```

If that does not match exactly, **stop** — the build's identity fast path
(§4) will silently fall back to a bilinear resample.

Delete the raw NetCDF for a field once its staging is verified; that is what
keeps the VM under 1 TB.

`hgt500` note: the stage script already divides geopotential by *g*, so the
staged units are **metres**, and the build below is invoked with `--units-in m`.

---

## 4. Build the global baselines

The only new argument is **`--region global`**. It selects the native
EPSG:4326 branch (`resolve_target_grid` in
`backend/scripts/build_climatology_baseline_assets.py`). `--region na` /
`--region conus` are untouched and still produce byte-identical EPSG:3857
assets.

Because the staged rasters already sit on the contract grid, the global build
takes a **bit-exact identity path** — no resampling at all.

```bash
source /path/to/cartosky/.venv/bin/activate

# tmp2m — baseline stored in °F (same convention as NA; the °C ladder for
# tmp850 is produced at derive time by f_to_c_delta, NOT here).
python backend/scripts/build_climatology_baseline_assets.py \
  --source-root /data/era5-stage/era5/single-levels/tmp2m \
  --data-root   /data/cartosky-global-baselines \
  --version v1 \
  --baseline-source era5 \
  --field tmp2m \
  --region global \
  --reference-period 1991-2020 \
  --units-in K \
  --smoothing-window-days 15 \
  --start-year 1991 --end-year 2020 \
  --require-complete

# tmp850 — ALSO stored in °F. Do not "fix" this to °C.
python backend/scripts/build_climatology_baseline_assets.py \
  --source-root /data/era5-stage/era5/pressure-levels/tmp850 \
  --data-root   /data/cartosky-global-baselines \
  --version v1 --baseline-source era5 --field tmp850 --region global \
  --reference-period 1991-2020 --units-in K \
  --smoothing-window-days 15 --start-year 1991 --end-year 2020 --require-complete

# hgt500 — stored in decametres; the script divides the staged metres by 10.
python backend/scripts/build_climatology_baseline_assets.py \
  --source-root /data/era5-stage/era5/pressure-levels/hgt500 \
  --data-root   /data/cartosky-global-baselines \
  --version v1 --baseline-source era5 --field hgt500 --region global \
  --reference-period 1991-2020 --units-in m \
  --smoothing-window-days 15 --start-year 1991 --end-year 2020 --require-complete
```

Each invocation prints a summary; confirm it reads:

```text
'target_crs': 'EPSG:4326', 'target_resolution': 0.25, 'target_shape': [721, 1440]
```

### Expected output inventory

```text
/data/cartosky-global-baselines/climatology/v1/era5/baseline/{field}/global/1991-2020/doy_{001..366}_h{00,06,12,18}.tif
```

| Field | Files | Raw float32 |
|---|---:|---:|
| `tmp2m` | 1464 | 5.66 GiB |
| `tmp850` | 1464 | 5.66 GiB |
| `hgt500` | 1464 | 5.66 GiB |
| **Total** | **4392** | **≈17.0 GiB** |

1464 = 366 day-of-year buckets (leap-day-aware) × 4 synoptic hours. Deflate
without a predictor buys some headroom; budget the raw number.

Verify before shipping anything to prod:

```bash
ROOT=/data/cartosky-global-baselines/climatology/v1/era5/baseline
for f in tmp2m tmp850 hgt500; do
  echo -n "$f: "; find "$ROOT/$f/global/1991-2020" -name 'doy_*_h*.tif' | wc -l
done
# EXPECT 1464 each

python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
from app.services.climatology import configure_data_root, load_climatology_baseline
configure_data_root(Path("/data/cartosky-global-baselines"))
for field in ("tmp2m", "tmp850", "hgt500"):
    arr, crs, transform, meta = load_climatology_baseline(
        version="v1", baseline_source="era5", field=field,
        valid_time=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
        region="global", reference_period="1991-2020",
    )
    print(field, arr.shape, crs, transform, float(arr[360, 720]))
PY
# EXPECT (721, 1440) EPSG:4326 | 0.25, 0.00,-180.12| ... for all three.
# The loader validates CRS, shape AND transform; anything off raises here
# rather than in prod.
```

Run that with `PYTHONPATH=backend` from the repo root.

---

## 5. Install on prod

Prod data root is `/opt/cartosky/data` (`deployment/systemd/*.env.example`).
The destination path comes from
`backend/app/services/climatology.py:climatology_baseline_root` —
`{data_root}/climatology/{version}/{source}/baseline/{field}/{region}/{period}`:

```text
/opt/cartosky/data/climatology/v1/era5/baseline/tmp2m/global/1991-2020/
/opt/cartosky/data/climatology/v1/era5/baseline/tmp850/global/1991-2020/
/opt/cartosky/data/climatology/v1/era5/baseline/hgt500/global/1991-2020/
```

Note there is **no model segment** in that path: the same 17 GiB serves GFS
and any future global model (AIGFS/AIFS/ECMWF) — it is paid once.

Copy field by field, and copy into a temporary sibling then rename, so a
half-transferred field is never visible under its real name:

```bash
for f in tmp2m tmp850 hgt500; do
  DEST=/opt/cartosky/data/climatology/v1/era5/baseline/$f/global
  ssh prod "mkdir -p $DEST"
  rsync -a --info=progress2 \
    /data/cartosky-global-baselines/climatology/v1/era5/baseline/$f/global/1991-2020/ \
    prod:$DEST/.1991-2020.incoming/
  ssh prod "mv $DEST/.1991-2020.incoming $DEST/1991-2020"
done
```

Post-copy checks on prod:

```bash
for f in tmp2m tmp850 hgt500; do
  echo -n "$f: "
  find /opt/cartosky/data/climatology/v1/era5/baseline/$f/global/1991-2020 \
       -name 'doy_*_h*.tif' | wc -l
done
# EXPECT 1464 each

du -sh /opt/cartosky/data/climatology/v1/era5/baseline/*/global
# EXPECT ~5.7G each, ~17G total

df -h /opt/cartosky/data
# EXPECT the volume to stay well under 50%; the phase budget is ~41.6%.
```

Ownership/permissions must match the existing NA baselines
(`ls -l .../tmp2m/na/1991-2020` for reference) or the scheduler will read-fail.

---

## 6. Deploy order (and why the wrong order survives)

**Required order:**

1. §5 — baselines installed and counted on prod.
2. Deploy the Wave-1 code.
3. Restart the **GFS scheduler** *and* the **API** (capability descriptors are
   read at process start — the flipped `supported_build_regions` will not be
   served by a stale API).
4. Confirm `CARTOSKY_GLOBAL_DOMAIN_MODELS` includes `gfs` (it already must, or
   nothing global builds at all).

**If the code lands first (assets missing or a field only half-copied):** the
build does not crash and does not publish a broken frame. Every affected frame
is *skipped* with status `skipped_missing_baseline`. Expect **three** WARNING
lines per skipped frame, not one — two from the builder and one from the
scheduler:

```text
# 1. builder — the diagnostic one; names the exact missing asset
climatology_baseline_missing model=gfs region=global var=hgt500_anom fh=012
  baseline_source=era5 baseline_field=hgt500 baseline_region=global
  reference_period=1991-2020 valid_time=2026-07-30T12:00:00+00:00
  — frame skipped (status=skipped_missing_baseline)

# 2. builder — the frame-level outcome
Skipping frame without a climatology baseline: gfs/global/hgt500_anom/fh012
  reason=missing_baseline_assets

# 3. scheduler — generic, shared with ordinary build failures
Build skipped/failed: {run} global/hgt500_anom fh012
```

Only line 1 is unique to this condition; line 3 is indistinguishable from a
real failure, so **count line 1, not line 3**, when assessing severity. The
§7.1 grep is written against lines 1 and 2 and works as-is.

The scheduler treats that status as a blocked target, not a failure: the rest
of the global run completes normally and canonical `na` anomalies are entirely
unaffected (the skip path only engages when the baseline region was resolved
*away* from the declared canonical one). Finish the copy and the next cycle
picks the variables up — no backfill command is needed, though a
`rebuild_existing` pass will fill the current run if you want it sooner.

**If the assets land first and the code later:** nothing reads them. Inert.

---

## 7. Post-install verification

### 7.1 Logs, first GFS cycle after the restart

```bash
journalctl -u cartosky-scheduler-gfs -f | grep -E \
  'climatology_baseline_missing|skipped_missing_baseline|global/(tmp2m|tmp850|hgt500)_anom'
```

- **Zero** `climatology_baseline_missing` lines. One means an incomplete copy —
  the line names the exact missing `baseline_field` and `valid_time`.
- `Build success: {run} global/hgt500_anom fh###` lines should appear.

### 7.2 API declares the three variables on the global domain

```bash
curl -s 'https://api.cartosky.com/api/v4/capabilities' \
  | jq '.model_catalog.gfs.variables
        | with_entries(select(.key|endswith("_anom")))
        | map_values(.supported_build_regions)'
# EXPECT: tmp2m_anom/tmp850_anom/hgt500_anom -> ["na","global"]
#         precip_*_anom                      -> []   (Wave 2)
```

An empty `[]` on all seven means the API process is still running the old
descriptors — restart it (§6 step 3).

### 7.3 Frames actually published for the global domain

```bash
RUN=$(curl -s 'https://api.cartosky.com/api/v4/gfs/runs?domain=global' | jq -r '.[0]')
curl -s "https://api.cartosky.com/api/v4/gfs/$RUN/vars?domain=global" \
  | jq '[.[].id] | map(select(endswith("_anom")))'
# EXPECT ["tmp850_anom","hgt500_anom","tmp2m_anom"] (order follows the catalog)

curl -s "https://api.cartosky.com/api/v4/gfs/$RUN/hgt500_anom/frames?domain=global" \
  | jq 'length'
# EXPECT 105 for a complete cycle (GFS_INITIAL_FHS)
```

Also spot-check the grid manifest declares the right projection — a 3857 here
would mean the derive path warped when it should have rolled:

```bash
curl -s "https://api.cartosky.com/api/v4/gfs/$RUN/hgt500_anom/grid-manifest?domain=global" \
  | jq '{projection, width, height}'
# EXPECT {"projection":"EPSG:4326","width":1440,"height":721}
```

### 7.4 NA-overlap parity spot check (required by the plan)

The global and NA baselines share the same reference period (D1) and the same
source archive, so at a point inside the NA domain the two anomaly values must
agree to within resample tolerance. They are **not** expected to be bit-equal:
the canonical value is bilinearly warped to a 25 km mercator cell while the
global value is the source 0.25° cell, so the difference is a resampling
difference plus uint16 packing quantization.

Pick a run and forecast hour that both domains published, then sample the same
lat/lon from each:

```bash
RUN=$(curl -s 'https://api.cartosky.com/api/v4/gfs/runs?domain=global' | jq -r '.[0]')
FH=24

for P in "39.74 -104.98 Denver" "40.71 -74.01 NewYork" "47.61 -122.33 Seattle" \
         "25.76 -80.19 Miami" "61.22 -149.90 Anchorage"; do
  set -- $P
  LAT=$1; LON=$2; NAME=$3
  for VAR in hgt500_anom tmp2m_anom tmp850_anom; do
    NA=$(curl -s "https://api.cartosky.com/api/v4/sample?model=gfs&run=$RUN&var=$VAR&fh=$FH&lat=$LAT&lon=$LON" | jq -r '.value')
    GL=$(curl -s "https://api.cartosky.com/api/v4/sample?model=gfs&run=$RUN&var=$VAR&fh=$FH&lat=$LAT&lon=$LON&domain=global" | jq -r '.value')
    echo "$NAME $VAR na=$NA global=$GL"
  done
done
```

Acceptance, per variable (interior points only — see below):

| Variable | Units | Expected \|global − na\| |
|---|---|---|
| `hgt500_anom` | m | ≤ 10 m, typically < 4 m |
| `tmp2m_anom` | °F | ≤ 2 °F over flat terrain; **larger over complex terrain is expected**, not a bug (25 km mercator vs 0.25° cells resolve different elevations) |
| `tmp850_anom` | °C | ≤ 1 °C |

Rules for reading the result:

- **Sign and magnitude must match.** A sign flip, or a difference of the order
  of the field's own range, is a real failure — most likely a units or
  longitude-roll error, not resampling.
- **Sample interior points.** Anywhere within ~50 km of the NA bbox edge
  (`REGION_BBOX_3857["na"]`) mixes in edge effects; Anchorage above is close to
  the northern edge and is included as a wrap/high-latitude probe, not as a
  tight-tolerance point.
- A `tmp2m_anom` disagreement over mountains that shrinks at nearby flat points
  is terrain aliasing, and is expected.
- If `hgt500_anom` disagrees by hundreds of metres, check the `hgt500`
  baseline's units: it must be stored in **decametres** (build invoked with
  `--units-in m` against staged metres, which the script divides by 10).

Record the observed table in the phase notes; it is the evidence that the
global baselines and the NA baselines describe the same climatology.

---

## 8. Rollback

Removing the three `global/1991-2020` directories reverts prod to the graceful
skip path (§6) within one cycle: the global domain simply stops publishing
anomaly variables. No code rollback is needed for that. Canonical `na`
anomalies are never affected — they read a different directory and never take
the skip path.
