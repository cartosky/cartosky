# Wave 2 — hourly ERA5 `total_precipitation` download + stage (2026-08-01)

Operator sequence for Phase 3A **Wave 2** (the four precip anomaly windows).
Instance-specific companion to `docs/GLOBAL_ANOMALY_WAVE1_RUNBOOK.md` (rationale)
and `docs/GLOBAL_ANOMALY_SIZING_3A_2026-07-30.md` (§4 volumes, §5 R2/R11), written
in the same style as `docs/WAVE1_VM_EXECUTION_2026-07-31.md`.

- **VM:** Hetzner (`root@<VM_IP>`), 286 GB free at `/`, 16 GB RAM, repo at
  `/root/cartosky`, both venvs built, CDS creds installed.
- **Mac archive:** `~/era5-archive/`.
- Checkpoints marked ⛔ are stop-points: do not continue past a failed one.

## ⛔⛔ STOP — this doc covers DOWNLOAD + STAGE ONLY

**FULLY LIFTED 2026-08-03.** The streaming rewrite shipped 2026-08-02
(streaming build ~1.5 GiB peak at global scale; NA byte-identity pinned) and
the CAPABILITY FLIP is now implemented for **all four** deterministic global
models — gfs, aigfs, aifs, ecmwf. Deploy steps: §3.4. Historical blockers and
the original checklist, for the record:

> [!IMPORTANT]
> **Capability-flip checklist — DONE 2026-08-03.** (1)
> `baseline_region_by_build_region: "global=global"` on the precip anomaly
> VarSpec hints, per model, opt-in via a keyword argument on the shared
> `_precip_anomaly_var_spec` so the ensembles (gefs, eps — no global domain)
> do not inherit it; (2) precip entries moved into each model's
> `*_GLOBAL_ANOMALY_VAR_KEYS` allowlist + both-direction test pins; (3) the
> accumulation missing-baseline pre-check was IMPLEMENTED, not deferred —
> `accumulation_baseline_assets_present`, applied in pipeline.py
> `_resolve_build_region_baseline`, so a missing precip baseline skips the
> frame (`skipped_missing_baseline`) instead of hard-failing it.

> [!NOTE]
> **Journal grep key.** The skip line
> (`climatology_baseline_missing … — frame skipped (status=skipped_missing_baseline)`)
> now names its alignment field honestly: instantaneous anomalies still log
> `valid_time=<frame valid time>`, but accumulation (precip-window) anomalies
> log `reference_date=<accumulation WINDOW-START date>`, which is a different
> quantity. The Wave-1 runbook greps
> (`docs/GLOBAL_ANOMALY_WAVE1_RUNBOOK.md`, `docs/WAVE1_VM_EXECUTION_2026-07-31.md`)
> match on `climatology_baseline_missing` and target the instantaneous vars,
> so they are unaffected.

> [!IMPORTANT]
> **The build command MUST pass `--windows 5 7 10 15 16`.** The script's
> default (`5 7 10 15`) omits the 16-day window — the default would produce a
> silently incomplete baseline set discovered only at the capability flip.
> All five windows are load-bearing because the four models split into two
> families (verified against the catalogs 2026-08-03):
>
> | Model | Windows | Long window |
> |---|---|---|
> | gfs   | 5 / 7 / 10 / 16 | 16 d |
> | aigfs | 5 / 7 / 10 / 16 | 16 d |
> | aifs  | 5 / 7 / 10 / 15 | 15 d |
> | ecmwf | 5 / 7 / 10 / 15 | 15 d |
>
> ECMWF has **no** `precip_16d_anom` — it was removed as a dead
> packed-but-uncataloged entry in 9a76a1c3 and must not be resurrected.

1. **R2 (memory).** `_daily_normals_by_doy` appends every warped daily raster to
   `buckets` before averaging — 10 958 × 3.961 MiB ≈ **42 GiB resident** at global.
   It will OOM instantly on a 16 GB VM. The streaming/incremental rewrite must land first.
2. **Hard-coded EPSG:3857.** `_write_baseline_asset` writes `crs="EPSG:3857"`
   unconditionally, and the script resolves its target grid through
   `get_baseline_grid_params`, which has no `global` entry. There is no `--region global`
   path in this script yet (unlike the instantaneous build, which got one in Wave 1).

Download and stage are safe to run now: the staged daily rasters are the build's
input and are unaffected by either change. Finish this doc, archive the staged
rasters, and **park** until the rewrite ships.

---

## 1. What the staging script actually expects (verified, not assumed)

Source of truth: `backend/scripts/stage_era5_precip_daily_source.py`.

| Question | Answer | Evidence |
|---|---|---|
| Variable | `tp`, or long name `total_precipitation` | `:122-131` |
| Raw units | **metres** (`--units-in meters` is the default) | `:93-102`, `:385` |
| Time coord | `time` **or** `valid_time` (new CDS naming is handled) | `:144-152` |
| Dims | must reduce to `(time, latitude, longitude)`; size-1 extra dims are dropped | `:133-141`, `:155-164` |
| File discovery | `rglob` over `--input-root` for `.nc` / `.nc4` / `.cdf`, sorted; **no path-based year filtering** — years are filtered on time values | `:116-119`, `:167-181` |
| Grid consistency | every file's lat/lon must be identical to the first file's, else hard raise | `:266-270` |
| Cadence needed | **all 24 hours per day**; `require_24_hours` (default on) raises if a day has ≠ 24 | `:334-342` |
| Output | `{stage_root}/era5/single-levels/precip_daily/{YYYY}/{YYYYMMDD}_precip_daily.tif`, float32, EPSG:4326, nodata NaN, deflate | `:105-113`, `:204-217` |
| Resumable | existing output paths are skipped unless `--overwrite` | `:299-303` |

### The day-boundary convention — the critical question

ERA5 `tp` at timestamp **T accumulates (T−1h, T]**. The staging script groups
hours purely by `valid_time.date()` (`_daily_groups` `:184-188`) and sums them
(`:343`). So the "day D" raster is:

> **sum of hours 00:00 … 23:00 of date D**, which physically covers
> **23:00 (D−1) → 23:00 (D)**.

That is one hour behind the true UTC calendar day (which would need 00:00 of D+1
instead of 00:00 of D). **This is deliberate and must not be changed:**

- It is the convention recorded in the raster tag itself —
  `source_semantics="hourly_accumulation_summed_to_utc_daily_total"` (`:225`).
- It is the convention the **already-shipped NA precip baselines** were built with.
  Changing it for global would make the global and NA precip anomalies disagree at
  their overlap and would invalidate the same NA-parity check Wave 1 used (§7.4).
- The product is a 30-year normal of 5/7/10/16-**day** rolling sums. A one-hour
  phase shift is far below the noise floor of that statistic.

### ⇒ Monthly files are correct. There is NO month-boundary problem.

Because a day's bucket is "hours 00–23 **of that same date**", every day is fully
contained in the month file that contains it. A monthly request covering all days ×
all 24 hours yields **exactly 24 hours for every day in the month**, self-contained.

- Do **not** request "monthly plus first hour of next month" — the extra hour would
  land in the *next* day's bucket in the next file anyway (it is hour 00 of day 1),
  and would trip `require_24_hours` with a 25-hour first day if it landed in the same file.
- 1991-01-01 needs no 1990 data.
- **Monthly granularity: 360 requests.** Daily granularity would be **10 958 requests**
  and is *not* needed. (If it had been, this would be a feasibility-changing blocker —
  it is not. Recording it here so nobody re-litigates it.)
- Monthly is also **required** in the other direction: sizing R11 — the script loads a
  whole file cube via `data_array.values` (`:322`). Monthly ≈ 2.9 GiB in RAM; **yearly
  would be ≈35 GiB and would OOM the 16 GB VM.** Never request yearly files.

**Verdict: no blocker.** Monthly NetCDF, all 24 hours, whole globe, metres — exactly
what the script below produces.

---

## 2. Disk math (verify before starting)

Per-slice global 0.25° = 1440 × 721 × 4 B = **3.961 MiB** (sizing §4).

| Quantity | Value | Derivation |
|---|---:|---|
| Hours per common year | 8 760 | 365 × 24 |
| **Raw decoded per year** | **≈ 33.9 GiB** | 8 760 × 3.961 MiB (leap: ≈ 34.0 GiB) |
| Raw decoded, 30 yr | ≈ 1 017 GiB | matches sizing §4 (≈1.0 TiB) ✓ |
| **Raw ON DISK per year (packed NetCDF)** | **≈ 9-13 GB; budget 15 GB** | Wave-1 measured 2.1 GB/yr for 1 460 slices = 1.44 MB/slice; × 8 760 = 12.6 GB. `tp` packs better (mostly exact zeros), so the low end is likely. |
| Raw on disk, all 30 yr at once | **≈ 380 GB** | **> 286 GB free — DOES NOT FIT** |
| Staged daily rasters | 10 958 files, ≤ 42 GiB | sizing §4 (raw f32; deflate makes it less) |
| **Peak disk with the year-chunked flow** | **≈ 60 GB** | full staged set (42 GiB) + one year of raw (15 GB) |

### ⇒ The Wave-1 shape does not work here

Wave 1 could download a whole field, archive it, and *then* stage year-by-year.
Wave 2 cannot: 380 GB of raw exceeds the disk. **Download and staging must be
interleaved per year** — download year Y, stage year Y, delete year Y's raw, next year.
The script in §5 therefore takes a year range.

RAM watch (16 GB): a monthly cube is 2.88 GiB float32, and
`_convert_precip_to_inches` makes a second array — ~5.8 GiB peak, more if xarray
unpacks to float64 (~8.6 GiB). It fits, but run staging with nothing else on the box,
and make sure swap exists.

---

## 3. Operator sequence

### 3.0 Install the updated downloader

Paste the §5 script over `/root/download_era5.py`. It is a **full-file replacement**
(the field tuples gain a 4th element for hours, and the CLI gains a year range), so
the Wave-1 entries are included verbatim-equivalent. Re-running Wave-1 fields is a
no-op — finished files are skipped. Sanity-check it first:

```bash
/root/.era5-prep-venv/bin/python /root/download_era5.py --list
```
⛔ EXPECT the four fields listed, `precip` showing `single-levels / total_precipitation / 24 hours`.

### 3.1 Download + stage, one year at a time (VM, in tmux)

```bash
tmux new -s era5p
```

Do 1991 by hand first so the grid assertion happens before you burn days of queue time.

**A. Download 1991:**
```bash
/root/.era5-prep-venv/bin/python /root/download_era5.py precip --start-year 1991 --end-year 1991
```
⛔ EXPECT 12 files under `/data/era5-raw/single-levels/precip/1991/`:
```bash
ls /data/era5-raw/single-levels/precip/1991/ | wc -l; du -sh /data/era5-raw/single-levels/precip/1991
```
⛔ EXPECT `12` and roughly 9-15 GB. Wildly smaller = truncated transfer; delete and re-run.

**B. Stage 1991:**
```bash
cd /root/cartosky && /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_precip_daily_source.py --input-root /data/era5-raw/single-levels/precip --stage-root /data/era5-stage --start-year 1991 --end-year 1991 --units-in meters
```
(No `--allow-incomplete-days`. The 24-hour requirement is the download's correctness check —
if it raises, a month came back short and must be re-downloaded, not waved through.)

**C. ⛔ Grid assertion on the first staged raster:**
```bash
/root/.era5-prep-venv/bin/python -c "
import rasterio
with rasterio.open('/data/era5-stage/era5/single-levels/precip_daily/1991/19910101_precip_daily.tif') as ds:
    print(ds.crs, ds.width, ds.height, ds.transform)
    print(ds.tags(1))
"
```
⛔ EXPECT exactly `EPSG:4326 1440 721` and transform
`| 0.25, 0.00,-180.12| 0.00,-0.25, 90.12|` — identical to Wave 1 — and band tags
containing `source_hour_count: 24` and `staged_units: inches`. Anything else: STOP.

⛔ Also expect `365` files for 1991:
```bash
find /data/era5-stage/era5/single-levels/precip_daily/1991 -name '*.tif' | wc -l
```

**D. The loop, 1992-2020** (download → stage → delete raw, `|| break` on any failure):
```bash
cd /root/cartosky && rm -rf /data/era5-raw/single-levels/precip/1991 && for Y in $(seq 1992 2020); do /root/.era5-prep-venv/bin/python /root/download_era5.py precip --start-year $Y --end-year $Y && /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_precip_daily_source.py --input-root /data/era5-raw/single-levels/precip --stage-root /data/era5-stage --start-year $Y --end-year $Y --units-in meters && rm -rf /data/era5-raw/single-levels/precip/$Y || break; done
```

Because the loop deletes raw as it goes, `df -h /` should hover near 60 GB used by
this workflow and never climb. Check it daily; if it climbs, a `rm -rf` is not firing
and the loop broke.

**E. ⛔ Staged count for 1991-2020:**
```bash
find /data/era5-stage/era5/single-levels/precip_daily -name '*_precip_daily.tif' | wc -l
```
⛔ EXPECT **`10958`** — calendar-exact: 30 × 365 + 8 leap days (1992, 1996, 2000,
2004, 2008, 2012, 2016, 2020). A count of 10 950 means the leap days are missing;
10 957 or similar means a month came back short — find the gap before archiving:
```bash
for Y in $(seq 1991 2020); do echo -n "$Y "; find /data/era5-stage/era5/single-levels/precip_daily/$Y -name '*.tif' | wc -l; done
```
(365 every year, 366 in the eight leap years.)

```bash
du -sh /data/era5-stage/era5/single-levels/precip_daily; df -h /
```

### 3.2 Archive to the Mac — **archive the STAGED rasters, not the raw**

This inverts the Wave-1 policy, deliberately. In Wave 1 raw was 63 GB/field and
staged was 509 GiB, so raw was the cheap archive. Here raw is ~380 GB and staged is
≤42 GiB — and recreating the staged set costs a **1 TiB re-download**, which is the
single most expensive thing in this phase. Staged is now the artifact worth keeping.

```bash
mkdir -p ~/era5-archive/stage/era5/single-levels
rsync -a --info=progress2 root@<VM_IP>:/data/era5-stage/era5/single-levels/precip_daily/ ~/era5-archive/stage/era5/single-levels/precip_daily/
```
⛔ Verify the Mac copy before deleting anything on the VM:
```bash
find ~/era5-archive/stage/era5/single-levels/precip_daily -name '*_precip_daily.tif' | wc -l
```
⛔ EXPECT `10958`.

Optional (only if the Mac has ~400 GB spare): archiving raw as well means adding a
per-year rsync *inside* the loop in step D, before the `rm -rf`. Not recommended —
raw's only value is re-deriving staged, and staged is now archived directly.

### 3.3 Build the global precip accumulation baselines (VM)

*Added 2026-08-02: the streaming rewrite is committed and the VM repo is
refreshed — the old ⛔ build block is lifted. The build streams (measured
~1.5 GiB peak at full global scale), so the 16 GB VM is ample.*

Prerequisites: staging loop complete (⛔ **10 958** staged rasters), Mac
archive of the staged rasters complete (§3.2 — they are the expensive
artifact; the build below is cheap to re-run, the staging is not).

> [!IMPORTANT]
> `--windows 5 7 10 15 16` is mandatory — the default omits 16, and GFS's
> `precip_16d_anom` requires it (§ STOP block).

```bash
cd /root/cartosky && PYTHONPATH=backend /root/cartosky/.venv/bin/python backend/scripts/build_precip_accumulation_climatology_assets.py --source-root /data/era5-stage/era5/single-levels/precip_daily --data-root /data/cartosky-global-baselines --version v1 --baseline-source era5 --region global --reference-period 1991-2020 --windows 5 7 10 15 16 --units-in inches --start-year 1991 --end-year 2020 --require-complete
```

⛔ The summary must report the global target grid (EPSG:4326, 0.25°,
721 × 1440) and five windows. `--require-complete` makes any missing source
day or empty day-of-year bucket a hard failure — a failure here means a
staging gap, not a build bug; re-check §3.1's counts.

**Verify counts — ⛔ EXPECT `366` five times** (one asset per day-of-year
per window; daily, no hourly dimension — unlike Wave 1's 1 464):

```bash
for W in 5 7 10 15 16; do echo -n "precip_${W}d: "; find /data/cartosky-global-baselines/climatology/v1/era5/baseline/precip_${W}d/global/1991-2020 -name 'doy_*.tif' | wc -l; done
```

Size sanity: ≈1.4 GiB per window before compression, ≈7 GiB total —
`du -sh /data/cartosky-global-baselines/climatology/v1/era5/baseline/precip_*`.

**⛔ Loader check** — the real prod loader validates CRS/shape/transform
(mirror of Wave 1 Phase D; run from the repo root):

```bash
cd /root/cartosky && PYTHONPATH=backend /root/cartosky/.venv/bin/python - <<'PY'
from datetime import date
from pathlib import Path
from app.services.climatology import configure_data_root, load_accumulation_climatology_baseline
configure_data_root(Path("/data/cartosky-global-baselines"))
for days in (5, 7, 10, 15, 16):
    arr, crs, transform, meta = load_accumulation_climatology_baseline(
        version="v1", baseline_source="era5", field=f"precip_{days}d",
        reference_date=date(2026, 1, 15), region="global",
        reference_period="1991-2020",
    )
    print(f"precip_{days}d", arr.shape, crs, transform, float(arr[360, 720]))
PY
```

⛔ EXPECT `(721, 1440) EPSG:4326 | 0.25, 0.00,-180.12| ...` for all five,
no exception; the sampled values are inches of accumulated precip at the
equator/0° — small positive numbers, increasing with window length.

**Pull to Mac** (incremental, same command as the staged-raster archive
root — the baselines land beside them):

```bash
rsync -a --info=progress2 root@<VM_IP>:/data/cartosky-global-baselines/climatology/ ~/era5-archive/baselines/climatology/
```

⛔ EXPECT `366` × 5 in the Mac copy before the VM is decommed.

### 3.4 Install on prod + the capability flip

Prod install mirrors Wave 1 (Mac → prod, `.incoming` → atomic rename, per
field; destinations `/opt/cartosky/data/climatology/v1/era5/baseline/precip_{5,7,10,15,16}d/global/`).

The capability-flip code shipped 2026-08-03 for **all four** deterministic
global models. Install the baselines first; **wrong order is survivable** —
the accumulation pre-check skips affected frames (`skipped_missing_baseline`)
and the run continues, so nothing crashes and the next run picks them up.

**Step 0 — precondition. Five window dirs × 366 files each:**

```bash
for W in 5 7 10 15 16; do
  printf 'precip_%-3sd: ' "$W"
  ls /opt/cartosky/data/climatology/v1/era5/baseline/precip_${W}d/global/1991-2020/doy_*.tif 2>/dev/null | wc -l
done
```

⛔ Expect `366` on **every** line (365 + leap day). 5/7/10 are shared by all
four models; 16 serves gfs + aigfs; 15 serves aifs + ecmwf.

**Step 1 — pull and restart. This is a descriptor change, so ALL FOUR model
schedulers AND the API must restart.** The scheduler decides what to build,
the API decides what to advertise — restarting only one gives a split brain
where the viewer offers a variable no scheduler is producing.

```bash
cd /opt/cartosky && sudo -u cartosky git pull
sudo systemctl restart \
  csky-gfs-scheduler csky-aigfs-scheduler csky-aifs-scheduler csky-ecmwf-scheduler \
  csky-api
systemctl is-active csky-gfs-scheduler csky-aigfs-scheduler csky-aifs-scheduler csky-ecmwf-scheduler csky-api
```

**Step 2 — capabilities advertise the flip.** Each model must list its own
window set; note gfs/aigfs are 16 d and aifs/ecmwf are 15 d.

```bash
for M in gfs aigfs aifs ecmwf; do
  echo "== $M"
  curl -s https://api.cartosky.com/api/v4/capabilities \
    | jq --arg m "$M" '
        .model_catalog[$m].variables
        | with_entries(select(.key | test("^precip_[0-9]+d_anom$")))
        | map_values(.supported_build_regions)'
done
```

⛔ Expect `["na","global"]` for every listed key, and exactly these keys:
`gfs`/`aigfs` → 5d, 7d, 10d, **16d**; `aifs`/`ecmwf` → 5d, 7d, 10d, **15d**.
A `precip_16d_anom` appearing under ecmwf is a regression (9a76a1c3).

**Step 3 — frames actually published on the global domain.**

```bash
for M in gfs aigfs aifs ecmwf; do
  RUN=$(curl -s "https://api.cartosky.com/api/v4/$M/runs?domain=global" | jq -r '.runs[-1].run // .runs[-1]')
  echo "== $M run=$RUN"
  curl -s "https://api.cartosky.com/api/v4/$M/$RUN/manifest?domain=global" \
    | jq '.variables | keys | map(select(test("^precip_[0-9]+d_anom$")))'
  for V in precip_5d_anom precip_7d_anom precip_10d_anom; do
    printf '   %s frames: ' "$V"
    curl -s "https://api.cartosky.com/api/v4/$M/$RUN/$V/frames?domain=global" | jq '.frames | length'
  done
done
```

Expected first fh is the window length (120 / 168 / 240). The long-window
variable is **static**: `precip_16d_anom` (gfs, aigfs) is one frame at fh384,
`precip_15d_anom` (aifs, ecmwf) is one frame at fh360.

> ECMWF cycle asymmetry: on the **short** 06/18z cycles (144 h horizon) only
> `precip_5d_anom` clears its `min_fh`. Zero frames for 7d/10d/15d on those
> runs is CORRECT, not a failure. Verify ECMWF against a 00z or 12z run.

**Step 4 — journal. The skip line must be absent.**

```bash
for U in csky-gfs-scheduler csky-aigfs-scheduler csky-aifs-scheduler csky-ecmwf-scheduler; do
  echo "== $U"
  sudo journalctl -u "$U" --since '6 hours ago' \
    | grep -E 'climatology_baseline_missing|skipped_missing_baseline' \
    | grep -E 'precip_[0-9]+d_anom'
done
```

⛔ Expect **empty**. Any hit names the exact missing asset in
`baseline_field=` / `baseline_region=`, and carries `reference_date=` — the
accumulation **window-start** date, not the frame's valid time (instantaneous
anomalies log `valid_time=` instead). A hit means the baseline install is
incomplete for that field: nothing is broken, those frames simply did not
publish. Fix the install and the next run picks them up.

### 3.5 Park / decom

Leave the staged set on the VM if the VM is staying up for the build; otherwise the
Mac copy is authoritative and the VM can be decommissioned and rebuilt later.

---

## 4. Time budget

360 monthly CDS requests, ~1 GB each. Wall clock is the CDS queue, not the VM — Wave 1
ran ~7 h per field for 360 much smaller requests, and `tp` requests are ~6× larger per
month. Budget **days, plausibly a couple of weeks**, and note the download and the
staging alternate serially in the loop (staging a year is minutes; it is not the
bottleneck). If throughput matters more than simplicity, the download for year Y+1
can be run in a second tmux window while year Y stages — but only if `df -h /` shows
headroom, since that holds two years of raw at once (~30 GB, still fine).

---

## 5. `/root/download_era5.py` — full replacement

```python
#!/usr/bin/env python3
"""ERA5 climatology downloader for CartoSky global baselines (Waves 1 + 2).

Monthly CDS requests, resumable, landing under
/data/era5-raw/{family}/{field}/{YYYY}/{field}_{YYYYMM}.nc

Wave 1 fields (tmp2m, tmp850, hgt500): 4 synoptic hours/day.
Wave 2 field  (precip):                all 24 hours/day - ERA5 total_precipitation
                                       is an accumulation field and the staging
                                       script requires exactly 24 hours per day.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cdsapi

RAW_ROOT = Path("/data/era5-raw")

MONTHS = [f"{m:02d}" for m in range(1, 13)]
DAYS = [f"{d:02d}" for d in range(1, 32)]  # CDS ignores nonexistent dates
SYNOPTIC_HOURS = ["00:00", "06:00", "12:00", "18:00"]
ALL_HOURS = [f"{h:02d}:00" for h in range(24)]

DEFAULT_START_YEAR = 1991
DEFAULT_END_YEAR = 2020

# field -> (dataset, extra request params, archive family, hours)
FIELDS: dict[str, tuple[str, dict, str, list[str]]] = {
    "tmp2m": (
        "reanalysis-era5-single-levels",
        {"variable": ["2m_temperature"]},
        "single-levels",
        SYNOPTIC_HOURS,
    ),
    "tmp850": (
        "reanalysis-era5-pressure-levels",
        {"variable": ["temperature"], "pressure_level": ["850"]},
        "pressure-levels",
        SYNOPTIC_HOURS,
    ),
    "hgt500": (
        "reanalysis-era5-pressure-levels",
        {"variable": ["geopotential"], "pressure_level": ["500"]},
        "pressure-levels",
        SYNOPTIC_HOURS,
    ),
    # --- Wave 2 -----------------------------------------------------------
    # Hourly total_precipitation. ALL 24 hours are mandatory:
    # stage_era5_precip_daily_source.py groups hours by valid_time.date() and
    # raises unless a day has exactly 24 of them. Monthly granularity is both
    # sufficient (a day's 00..23 hours never span a month file) and required
    # (sizing R11: the stager loads a whole file cube; yearly = ~35 GiB RAM).
    "precip": (
        "reanalysis-era5-single-levels",
        {"variable": ["total_precipitation"]},
        "single-levels",
        ALL_HOURS,
    ),
    # ----------------------------------------------------------------------
}

# Below this, a landed file is treated as a truncated transfer and re-fetched.
MIN_BYTES = 1_000_000


def target_path(field: str, year: int, month: str) -> Path:
    _dataset, _extra, family, _hours = FIELDS[field]
    return RAW_ROOT / family / field / str(year) / f"{field}_{year}{month}.nc"


def fetch_month(client: cdsapi.Client, field: str, year: int, month: str) -> None:
    dataset, extra, _family, hours = FIELDS[field]
    out = target_path(field, year, month)

    if out.exists() and out.stat().st_size >= MIN_BYTES:
        print(f"skip  {out}", flush=True)
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_suffix(out.suffix + ".part")
    if partial.exists():
        partial.unlink()

    request = {
        "product_type": ["reanalysis"],
        "year": [str(year)],
        "month": [month],
        "day": DAYS,
        "time": hours,
        "data_format": "netcdf",
        "download_format": "unarchived",  # never hand us a .zip
        # No "area" key: the whole global grid is the point.
        **extra,
    }

    print(f"fetch {out}  ({len(hours)} h/day)", flush=True)
    client.retrieve(dataset, request).download(str(partial))

    size = partial.stat().st_size
    if size < MIN_BYTES:
        partial.unlink()
        raise RuntimeError(f"suspiciously small download for {out} ({size} bytes)")
    partial.rename(out)
    print(f"done  {out}  {size / 1e9:.2f} GB", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download monthly ERA5 files for a baseline field.")
    parser.add_argument("field", nargs="?", choices=sorted(FIELDS), help="Field to download.")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--list", action="store_true", help="List configured fields and exit.")
    args = parser.parse_args()

    if args.list:
        for name, (dataset, extra, family, hours) in sorted(FIELDS.items()):
            print(f"{name:8s} {family:16s} {','.join(extra['variable']):20s} {len(hours)} hours  [{dataset}]")
        return 0

    if not args.field:
        parser.error("field is required (or pass --list)")
    if args.end_year < args.start_year:
        parser.error("--end-year must be >= --start-year")

    client = cdsapi.Client()
    for year in range(args.start_year, args.end_year + 1):
        for month in MONTHS:
            fetch_month(client, args.field, year, month)

    print(
        f"complete: field={args.field} years={args.start_year}-{args.end_year} "
        f"root={RAW_ROOT / FIELDS[args.field][2] / args.field}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Diff note for the operator:** the Wave-1 field tuples above were reconstructed from
the documented pattern (dataset, extra-params, family) plus a new 4th element for
hours. Before pasting, `diff` this against your existing `/root/download_era5.py` and
keep any Wave-1 detail that differs (e.g. a different filename convention). The only
things Wave 2 strictly needs are: the `precip` entry with `ALL_HOURS`, hours becoming
per-field instead of a module constant, and the `--start-year/--end-year` arguments
that make the year-chunked loop in §3.1-D possible. The staging script discovers input
by `rglob` for `*.nc`, so the exact filenames do not matter — only that raw for a year
lives under its own `{YYYY}/` directory, which is what the per-year `rm -rf` deletes.

---

## 6. Quick reference — the numbers to check against

| Check | Expected |
|---|---:|
| CDS requests, 1991-2020 monthly | 360 |
| Raw decoded per year | ≈ 33.9 GiB |
| Raw on disk per year (packed) | ≈ 9-15 GB |
| Staged rasters, 1991-2020 | **10 958** |
| Staged rasters, per non-leap year | 365 |
| Staged raster grid | EPSG:4326, 1440 × 721, `-180.125 / +90.125`, 0.25° |
| Staged units (band tag) | `inches` |
| Peak workflow disk on the VM | ≈ 60 GB |
