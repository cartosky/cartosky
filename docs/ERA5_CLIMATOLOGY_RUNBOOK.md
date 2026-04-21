# ERA5 Climatology Runbook

This runbook describes the off-prod workflow for generating the first anomaly climatology assets.

Initial pilot scope:

1. Source archive: ERA5
2. Field: `tmp2m`
3. Target consumer: `GEFS tmp2m_anom`
4. Valid times: `00/06/12/18Z`
5. Reference period: `1991-2020`

## Pipeline

```text
ERA5 raw archive
  -> stage_era5_climatology_source.py
  -> normalized staged rasters
  -> build_climatology_baseline_assets.py
  -> data/climatology/v1/gefs/baseline/tmp2m/*.tif
```

The heavy work should happen off-prod. Production should receive only staged normalized rasters if prod-side baseline generation is required, or preferably the final baseline assets.

## Archive Choices

Use these ERA5 sources:

1. Single levels for `tmp2m`
2. Pressure levels for `tmp850`
3. Pressure levels for `hgt500`

Recommended field mapping:

1. `tmp2m` -> ERA5 variable `t2m`
2. `tmp850` -> ERA5 variable `t` at `850 hPa`
3. `hgt500` -> ERA5 variable `z` at `500 hPa`, converted from geopotential to geopotential meters by dividing by standard gravity

## Off-Prod Prep Environment

The staging script is not part of the runtime API path. It is acceptable to run it in a separate prep environment.

Suggested prep-time packages:

```bash
python -m venv .era5-prep-venv
source .era5-prep-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install xarray netcdf4 cdsapi rasterio numpy
```

If you already have a data-processing environment, use that instead.

## Raw Archive Layout

One workable raw layout is:

```text
/data/era5-raw/
  single-levels/
    tmp2m/
      1991/
        era5_single_levels_tmp2m_1991_01.nc
        ...
  pressure-levels/
    tmp850/
      1991/
        era5_pressure_levels_tmp850_1991_01.nc
        ...
    hgt500/
      1991/
        era5_pressure_levels_hgt500_1991_01.nc
        ...
```

The exact raw archive naming is flexible. The prep script reads NetCDF content, not path semantics.

## Staged Raster Layout

The normalized staged raster layout is:

```text
/data/era5-stage/
  era5/
    single-levels/
      tmp2m/
        1991/
          1991010100_tmp2m.tif
          1991010106_tmp2m.tif
          ...
```

For v1 pilot:

1. Stage `tmp2m` only
2. Stage `00/06/12/18Z` only
3. Keep units in Kelvin in the staged rasters

## Stage ERA5 `tmp2m`

From the repo root:

```bash
source .era5-prep-venv/bin/activate
python backend/scripts/stage_era5_climatology_source.py \
  --input-root /data/era5-raw/single-levels/tmp2m \
  --stage-root /data/era5-stage \
  --field tmp2m \
  --start-year 1991 \
  --end-year 2020 \
  --hours 0 6 12 18
```

Expected output root:

```text
/data/era5-stage/era5/single-levels/tmp2m/
```

## Build GEFS `tmp2m` Baselines

After staging succeeds, build the repo-owned baseline assets:

```bash
source /path/to/cartosky/.venv/bin/activate
python backend/scripts/build_climatology_baseline_assets.py \
  --source-root /data/era5-stage/era5/single-levels/tmp2m \
  --data-root /data/cartosky \
  --version v1 \
  --model-family gefs \
  --field tmp2m \
  --region conus \
  --reference-period 1991-2020 \
  --units-in K \
  --smoothing-window-days 15 \
  --start-year 1991 \
  --end-year 2020 \
  --require-complete
```

Expected output root:

```text
/data/cartosky/climatology/v1/gefs/baseline/tmp2m/
```

## Validate The Pilot

Count the produced baseline files:

```bash
find /data/cartosky/climatology/v1/gefs/baseline/tmp2m -name 'doy_*.tif' | wc -l
```

Expected count: `1464`

That count is intentional because the current baseline asset contract is leap-day-aware:

1. `366` day-of-year buckets
2. `4` synoptic hours per day
3. `366 x 4 = 1464` assets per field

If the project ever switches to a normalized `365`-day climatology, that would be a new contract and should ship under a new climatology asset version instead of silently changing `v1`.

Then run a loader-level sanity check from the CartoSky repo root:

```bash
PYTHONPATH=backend python - <<'PY'
from datetime import datetime, timezone
from app.services.climatology import load_climatology_baseline

arr, crs, transform, meta = load_climatology_baseline(
    version="v1",
    model_family="gefs",
    field="tmp2m",
    valid_time=datetime(2026, 1, 1, 0, tzinfo=timezone.utc),
    region="conus",
    reference_period="1991-2020",
)
print("shape", arr.shape)
print("crs", crs)
print("meta", meta)
print("min", float(arr.min()))
print("max", float(arr.max()))
PY
```

## Prod Deploy Strategy

Preferred path:

1. Acquire ERA5 and stage rasters off-prod
2. Build climatology baseline assets off-prod
3. Copy only `/data/cartosky/climatology/v1/...` to prod

Fallback path:

1. Acquire ERA5 and stage rasters off-prod
2. Copy only staged rasters to a non-runtime area
3. Run `build_climatology_baseline_assets.py` on prod
4. Keep the raw ERA5 archive off-prod