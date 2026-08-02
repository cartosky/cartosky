from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import reproject

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.climatology import (
    BaselineGrid,
    climatology_baseline_root,
    get_baseline_grid,
    normalize_baseline_source,
)

#: Build regions this script knows how to target. ``na`` is the legacy
#: EPSG:3857 metre grid; ``global`` is the native EPSG:4326 0.25 degree
#: contract grid (``docs/GLOBAL_DOMAIN_4326_CONTRACT.md`` §1).
SUPPORTED_REGIONS = ("na", "global")

METERS_TO_INCHES = np.float32(39.37007874015748)
MM_TO_INCHES = np.float32(1.0 / 25.4)
DAILY_DATE_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
CANONICAL_LEAP_YEAR = 2000


@dataclass(frozen=True)
class DailyPrecipRaster:
    path: Path
    valid_date: date


def _parse_valid_date(path: Path) -> date | None:
    match = DAILY_DATE_RE.search(path.stem)
    if match is None:
        match = DAILY_DATE_RE.search(path.name)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_day_to_doy(month: int, day: int) -> int:
    return date(CANONICAL_LEAP_YEAR, month, day).timetuple().tm_yday


def _field_for_window(window_days: int) -> str:
    return f"precip_{int(window_days)}d"


def _convert_precip_to_inches(values: np.ndarray, *, units_in: str) -> np.ndarray:
    normalized_units = str(units_in).strip().lower()
    output = np.asarray(values, dtype=np.float32)
    if normalized_units in {"in", "inch", "inches"}:
        return output
    if normalized_units in {"m", "meter", "meters"}:
        return output * METERS_TO_INCHES
    if normalized_units in {"mm", "millimeter", "millimeters"}:
        return output * MM_TO_INCHES
    raise ValueError(f"Unsupported staged precip units: {units_in}")


def _scan_daily_precip_rasters(
    source_root: Path,
    *,
    start_year: int | None,
    end_year: int | None,
) -> dict[date, DailyPrecipRaster]:
    rasters: dict[date, DailyPrecipRaster] = {}
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        valid_date = _parse_valid_date(path)
        if valid_date is None:
            continue
        if start_year is not None and valid_date.year < int(start_year):
            continue
        if end_year is not None and valid_date.year > int(end_year):
            continue
        if valid_date in rasters:
            raise ValueError(f"Duplicate staged precip raster for {valid_date:%Y-%m-%d}: {rasters[valid_date].path} and {path}")
        rasters[valid_date] = DailyPrecipRaster(path=path, valid_date=valid_date)
    return rasters


def _expected_dates(start_year: int, end_year: int) -> list[date]:
    current = date(int(start_year), 1, 1)
    end = date(int(end_year), 12, 31)
    dates: list[date] = []
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _is_same_grid(
    *,
    src_crs,
    src_transform,
    src_shape: tuple[int, int],
    grid: BaselineGrid,
) -> bool:
    if CRS.from_user_input(src_crs) != CRS.from_user_input(grid.crs):
        return False
    if src_shape != (grid.height, grid.width):
        return False
    return all(
        abs(float(actual) - float(expected)) <= 1.0e-9
        for actual, expected in zip(src_transform[:6], grid.transform[:6])
    )


def _load_and_warp_daily(
    source: DailyPrecipRaster,
    *,
    grid: BaselineGrid,
    units_in: str,
    resampling: str,
) -> np.ndarray:
    with rasterio.open(source.path) as ds:
        band = ds.read(1, masked=True)
        values = np.asarray(band.filled(np.nan), dtype=np.float32)
        src_crs = ds.crs
        src_transform = ds.transform
        src_nodata = ds.nodata
    if src_crs is None:
        raise ValueError(f"Source raster missing CRS: {source.path}")

    converted = _convert_precip_to_inches(values, units_in=units_in)

    # Staged ERA5 daily precip rasters land on the exact global contract grid
    # (`stage_era5_precip_daily_source._transform_from_latlon` reproduces
    # `REGION_BBOX_4326["global"]` exactly), so the global build's "warp" is
    # the identity. Take it bit-exactly rather than letting a bilinear no-op
    # perturb float bits. Mirrors the instantaneous build's fast path; legacy
    # metre regions never reach it, so NA output is unchanged.
    if grid.is_native_geographic and _is_same_grid(
        src_crs=src_crs,
        src_transform=src_transform,
        src_shape=(converted.shape[0], converted.shape[1]),
        grid=grid,
    ):
        # No nodata remap here: the masked read above (`ds.read(1,
        # masked=True).filled(np.nan)`) is the nodata handler, and it runs
        # before unit conversion. Comparing converted values against the raw
        # `src_nodata` would be wrong for any `units_in` other than inches.
        return converted.astype(np.float32, copy=True)

    warped = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    reproject(
        source=converted.astype(np.float32, copy=False),
        destination=warped,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=grid.transform,
        dst_crs=CRS.from_user_input(grid.crs),
        resampling=Resampling[resampling],
        src_nodata=src_nodata,
        dst_nodata=float("nan"),
    )
    return warped.astype(np.float32, copy=False)


def _bucket_mean(
    bucket_sources: list[DailyPrecipRaster],
    *,
    grid: BaselineGrid,
    units_in: str,
    resampling: str,
) -> np.ndarray:
    """Streaming NaN-aware mean of one day-of-year bucket.

    Holds a single running sum plus a valid-sample count instead of a stack of
    warped rasters, so peak memory is O(one grid) rather than O(years).

    Two details are load-bearing for byte-identity with the pre-streaming
    ``np.nanmean(stack, axis=0)`` and must not be "cleaned up":

    * **float32 accumulator.** ``nanmean`` on a float32 stack sums in float32.
      The instantaneous build's ``_bucket_mean`` uses float64; copying that
      here would shift bits on every NA asset.
    * **Masked in-place add into a zero-initialised sum**, not
      ``sum += np.where(finite, x, 0)``. ``np.add.reduce`` starts from the
      ``+0.0`` identity, so an all-``-0.0`` cell reduces to ``+0.0``; the
      ``np.where`` fold would carry the sign through and yield ``-0.0``.
      Numerically irrelevant, bitwise not — and this form matches ``nanmean``
      universally, so it removes the whole counterexample class.
    """
    if not bucket_sources:
        raise ValueError("Cannot average an empty daily precip bucket")

    sum_values: np.ndarray | None = None
    valid_counts: np.ndarray | None = None

    for source in bucket_sources:
        warped = _load_and_warp_daily(
            source,
            grid=grid,
            units_in=units_in,
            resampling=resampling,
        )
        finite = np.isfinite(warped)
        if sum_values is None:
            sum_values = np.zeros(warped.shape, dtype=np.float32)
            valid_counts = np.zeros(warped.shape, dtype=np.int64)
        assert valid_counts is not None
        sum_values[finite] += warped[finite]
        valid_counts += finite

    assert sum_values is not None and valid_counts is not None
    mean = np.full(sum_values.shape, np.nan, dtype=np.float32)
    np.divide(
        sum_values,
        valid_counts,
        out=mean,
        where=valid_counts > 0,
        casting="unsafe",
    )
    return mean.astype(np.float32, copy=False)


def _daily_normals_by_doy(
    sources_by_date: dict[date, DailyPrecipRaster],
    *,
    grid: BaselineGrid,
    units_in: str,
    resampling: str,
) -> tuple[list[np.ndarray | None], list[int]]:
    """Per-day-of-year climatological daily normals, one bucket at a time.

    Only the paths are grouped up front; each bucket's rasters are read,
    warped and folded into a running sum inside :func:`_bucket_mean`, so at
    most one warped raster is resident at a time. Resident output is the 366
    normals themselves (1.42 GiB at global 721x1440 float32), not the ~11 000
    daily rasters the pre-streaming build stacked (~42 GiB, sizing doc R2).
    """
    paths_by_doy: dict[int, list[DailyPrecipRaster]] = defaultdict(list)
    for valid_date, source in sorted(sources_by_date.items()):
        paths_by_doy[_month_day_to_doy(valid_date.month, valid_date.day)].append(source)

    sample_counts = [0] * 366
    daily_normals: list[np.ndarray | None] = [None] * 366
    for doy in range(1, 367):
        bucket_sources = paths_by_doy.get(doy, [])
        sample_counts[doy - 1] = len(bucket_sources)
        if not bucket_sources:
            continue
        daily_normals[doy - 1] = _bucket_mean(
            bucket_sources,
            grid=grid,
            units_in=units_in,
            resampling=resampling,
        )
    return daily_normals, sample_counts


def _window_accumulation(
    daily_normals: list[np.ndarray | None],
    *,
    doy_index: int,
    window_days: int,
) -> np.ndarray | None:
    """Rolling forward sum of ``window_days`` daily normals from ``doy_index``.

    ``None`` when any day in the (circular) window has no normal. Computed one
    day-of-year at a time so the caller can write and drop each accumulation
    instead of materialising all 366 of them per window.

    Sequential float32 accumulation matches ``np.sum(stack, axis=0,
    dtype=np.float32)`` bit-for-bit. NaNs propagate, exactly as before: the
    window is a plain sum, never NaN-aware.

    The accumulator is zero-initialised rather than seeded with the first
    day's copy, for the same reason as :func:`_bucket_mean`: ``np.add.reduce``
    starts from the ``+0.0`` identity, so an all-``-0.0`` window must sum to
    ``+0.0``.
    """
    if int(window_days) <= 0:
        raise ValueError("Precip accumulation window must be positive")
    total: np.ndarray | None = None
    for offset in range(int(window_days)):
        values = daily_normals[(doy_index + offset) % len(daily_normals)]
        if values is None:
            return None
        if total is None:
            total = np.zeros(values.shape, dtype=np.float32)
        total += values
    return total


def _write_baseline_asset(
    *,
    path: Path,
    values: np.ndarray,
    transform,
    crs: str,
    field: str,
    reference_period: str,
    window_days: int,
    source_year_range: str,
    sample_count_min: int,
    sample_count_max: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=float("nan"),
        tiled=True,
        compress="deflate",
    ) as ds:
        ds.write(values.astype(np.float32), 1)
        ds.update_tags(
            1,
            field=str(field).strip().lower(),
            reference_period=str(reference_period).strip(),
            units="inches",
            accumulation_window_days=int(window_days),
            baseline_statistic="daily_normal_rolling_accumulation",
            source_year_range=source_year_range,
            sample_count_min=int(sample_count_min),
            sample_count_max=int(sample_count_max),
        )


def build_precip_accumulation_climatology_assets(
    *,
    source_root: Path,
    data_root: Path,
    version: str,
    baseline_source: str,
    region: str,
    reference_period: str,
    windows: tuple[int, ...],
    units_in: str,
    start_year: int | None,
    end_year: int | None,
    resampling: str,
    require_complete: bool,
) -> tuple[int, dict[int, int], list[str]]:
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()
    reference_period_key = str(reference_period).strip()
    if region_key not in SUPPORTED_REGIONS:
        raise ValueError(
            "Precip accumulation climatology supports region in "
            f"{', '.join(SUPPORTED_REGIONS)}; got {region_key!r}"
        )
    if source_key != "era5":
        raise ValueError("Precip accumulation climatology currently supports baseline_source=era5")
    if start_year is None or end_year is None:
        raise ValueError("start_year and end_year are required for precip accumulation climatology")

    sources_by_date = _scan_daily_precip_rasters(source_root, start_year=start_year, end_year=end_year)
    if not sources_by_date:
        raise ValueError(f"No staged daily precip rasters found under {source_root}")

    missing_dates = [f"{d:%Y-%m-%d}" for d in _expected_dates(start_year, end_year) if d not in sources_by_date]
    if require_complete and missing_dates:
        raise ValueError(
            "Missing staged daily precip source coverage: "
            + ", ".join(missing_dates[:20])
            + (" ..." if len(missing_dates) > 20 else "")
        )

    grid = get_baseline_grid(baseline_source=source_key, region=region_key)
    transform = grid.transform
    expected_shape = (grid.height, grid.width)

    daily_normals, daily_sample_counts = _daily_normals_by_doy(
        sources_by_date,
        grid=grid,
        units_in=units_in,
        resampling=resampling,
    )
    missing_doys = [f"doy_{doy:03d}" for doy, values in enumerate(daily_normals, start=1) if values is None]
    if require_complete and missing_doys:
        raise ValueError(
            "Missing climatological day-of-year precip buckets: "
            + ", ".join(missing_doys[:20])
            + (" ..." if len(missing_doys) > 20 else "")
        )

    files_by_window: dict[int, int] = {}
    files_written = 0
    year_range = f"{start_year}-{end_year}"
    for window_days in windows:
        window = int(window_days)
        field = _field_for_window(window)
        output_root = climatology_baseline_root(
            data_root=data_root,
            version=version,
            baseline_source=source_key,
            field=field,
            region=region_key,
            reference_period=reference_period_key,
        )
        window_written = 0
        for doy in range(1, 367):
            # One accumulation at a time: materialising all 366 per window
            # would add another full 366-grid set (1.42 GiB at global) on top
            # of the daily normals.
            values = _window_accumulation(
                daily_normals,
                doy_index=doy - 1,
                window_days=window,
            )
            if values is None:
                continue
            if values.shape != expected_shape:
                raise ValueError(
                    f"Unexpected accumulation shape for {field} doy={doy}: {values.shape} expected={expected_shape}"
                )
            sample_window = [daily_sample_counts[((doy - 1 + offset) % 366)] for offset in range(window)]
            _write_baseline_asset(
                path=output_root / f"doy_{doy:03d}.tif",
                values=values,
                transform=transform,
                crs=grid.crs,
                field=field,
                reference_period=reference_period_key,
                window_days=window,
                source_year_range=year_range,
                sample_count_min=min(sample_window),
                sample_count_max=max(sample_window),
            )
            window_written += 1
            files_written += 1
        files_by_window[window] = window_written

    return files_written, files_by_window, missing_dates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build leap-day-aware rolling precip accumulation climatology baseline GeoTIFF assets from staged ERA5 daily precip rasters."
    )
    parser.add_argument("--source-root", required=True, help="Root directory containing staged daily precip GeoTIFFs.")
    parser.add_argument("--data-root", required=True, help="CartoSky data root where climatology assets will be written.")
    parser.add_argument("--version", default="v1", help="Climatology asset version. Default: v1.")
    parser.add_argument("--baseline-source", default="era5", help="Shared baseline source key. Default: era5.")
    parser.add_argument(
        "--region",
        default="na",
        help=(
            "Target region key. Default: na. 'na' builds EPSG:3857 metre assets; "
            "'global' builds native EPSG:4326 0.25 degree assets on the global "
            "domain contract grid."
        ),
    )
    parser.add_argument("--reference-period", default="1991-2020", help="Reference period label. Default: 1991-2020.")
    parser.add_argument("--windows", nargs="+", type=int, default=[5, 7, 10, 15], help="Accumulation windows in days. Default: 5 7 10 15.")
    parser.add_argument("--units-in", default="inches", help="Units of staged daily rasters. Default: inches.")
    parser.add_argument("--start-year", type=int, required=True, help="Inclusive lower source year bound.")
    parser.add_argument("--end-year", type=int, required=True, help="Inclusive upper source year bound.")
    parser.add_argument("--resampling", default="bilinear", help="Raster resampling mode for source warping. Default: bilinear.")
    parser.add_argument("--require-complete", action="store_true", help="Fail if any source day or day-of-year bucket is missing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resolved_source = normalize_baseline_source(args.baseline_source)
    resolved_region = str(args.region).strip().lower()
    grid = get_baseline_grid(
        baseline_source=resolved_source,
        region=resolved_region,
    )
    files_written, files_by_window, missing_dates = build_precip_accumulation_climatology_assets(
        source_root=Path(args.source_root).resolve(),
        data_root=Path(args.data_root).resolve(),
        version=args.version,
        baseline_source=resolved_source,
        region=resolved_region,
        reference_period=args.reference_period,
        windows=tuple(int(window) for window in args.windows),
        units_in=args.units_in,
        start_year=args.start_year,
        end_year=args.end_year,
        resampling=args.resampling,
        require_complete=bool(args.require_complete),
    )
    print(
        "Built precip accumulation climatology baseline assets:",
        {
            "files_written": files_written,
            "files_by_window": files_by_window,
            "missing_dates": len(missing_dates),
            "baseline_source": resolved_source,
            "region": resolved_region,
            "version": args.version,
            "reference_period": args.reference_period,
            "units": "inches",
            "target_crs": grid.crs,
            "target_bbox": grid.bbox,
            "target_resolution": grid.resolution,
            "target_shape": [grid.height, grid.width],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
