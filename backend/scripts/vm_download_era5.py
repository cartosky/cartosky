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
