from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.builder.cog_writer import compute_transform_and_shape
from app.services.climatology import (
    climatology_baseline_root,
    get_baseline_grid_params,
    normalize_baseline_source,
)

TIMESTAMP_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_-]?(\d{2})(?!\d)")
SUPPORTED_HOURS = (0, 6, 12, 18)


@dataclass(frozen=True)
class SourceRaster:
    path: Path
    valid_time: datetime


def _parse_valid_time(path: Path) -> datetime | None:
    match = TIMESTAMP_RE.search(path.stem)
    if match is None:
        match = TIMESTAMP_RE.search(path.name)
    if match is None:
        return None
    year, month, day, hour = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, tzinfo=timezone.utc)
    except ValueError:
        return None


def _scan_source_rasters(
    source_root: Path,
    *,
    start_year: int | None,
    end_year: int | None,
) -> dict[tuple[int, int], list[SourceRaster]]:
    buckets: dict[tuple[int, int], list[SourceRaster]] = defaultdict(list)
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        valid_time = _parse_valid_time(path)
        if valid_time is None:
            continue
        if valid_time.hour not in SUPPORTED_HOURS:
            continue
        if start_year is not None and valid_time.year < int(start_year):
            continue
        if end_year is not None and valid_time.year > int(end_year):
            continue
        buckets[(valid_time.timetuple().tm_yday, valid_time.hour)].append(
            SourceRaster(path=path, valid_time=valid_time)
        )
    return dict(buckets)


def _convert_values(values: np.ndarray, *, field: str, units_in: str) -> np.ndarray:
    normalized_units = str(units_in).strip().lower()
    normalized_field = str(field).strip().lower()
    output = values.astype(np.float32, copy=False)

    if normalized_field in {"tmp2m", "tmp850"}:
        if normalized_units in {"f", "degf", "fahrenheit"}:
            return output
        if normalized_units in {"c", "degc", "celsius"}:
            return output * np.float32(9.0 / 5.0) + np.float32(32.0)
        if normalized_units in {"k", "kelvin"}:
            return (output - np.float32(273.15)) * np.float32(9.0 / 5.0) + np.float32(32.0)
        raise ValueError(f"Unsupported temperature units for {field}: {units_in}")

    if normalized_field == "hgt500":
        if normalized_units in {"dam", "decameter", "decameters"}:
            return output
        if normalized_units in {"m", "meter", "meters", "gpm", "geopotential_meter", "geopotential_meters"}:
            return output / np.float32(10.0)
        raise ValueError(f"Unsupported height units for {field}: {units_in}")

    raise ValueError(f"Unsupported field for climatology asset build: {field}")


def _load_and_warp_source(
    source: SourceRaster,
    *,
    baseline_source: str,
    region: str,
    field: str,
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

    converted = _convert_values(values, field=field, units_in=units_in)
    target_bbox, target_grid_m = get_baseline_grid_params(
        baseline_source=baseline_source,
        region=region,
    )
    dst_transform, dst_h, dst_w = compute_transform_and_shape(target_bbox, target_grid_m)
    warped = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
    reproject(
        source=converted.astype(np.float32, copy=False),
        destination=warped,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=rasterio.crs.CRS.from_epsg(3857),
        resampling=Resampling[resampling],
        src_nodata=src_nodata,
        dst_nodata=float("nan"),
    )
    return warped.astype(np.float32, copy=False)


def _bucket_mean(
    bucket_sources: list[SourceRaster],
    *,
    baseline_source: str,
    region: str,
    field: str,
    units_in: str,
    resampling: str,
) -> np.ndarray:
    sum_values: np.ndarray | None = None
    valid_counts: np.ndarray | None = None

    for source in bucket_sources:
        warped = _load_and_warp_source(
            source,
            baseline_source=baseline_source,
            region=region,
            field=field,
            units_in=units_in,
            resampling=resampling,
        )
        finite = np.isfinite(warped)
        if sum_values is None:
            sum_values = np.zeros_like(warped, dtype=np.float64)
            valid_counts = np.zeros_like(warped, dtype=np.uint16)
        assert valid_counts is not None
        sum_values[finite] += warped[finite]
        valid_counts[finite] += 1

    if sum_values is None or valid_counts is None:
        raise ValueError("Cannot compute bucket mean with no source rasters")

    mean = np.full(sum_values.shape, np.nan, dtype=np.float32)
    np.divide(
        sum_values,
        valid_counts,
        out=mean,
        where=valid_counts > 0,
        casting="unsafe",
    )
    return mean.astype(np.float32, copy=False)


def _smooth_doy_series(raw_means: list[np.ndarray | None], window_days: int) -> list[np.ndarray | None]:
    if window_days <= 1:
        return list(raw_means)
    if window_days % 2 == 0:
        raise ValueError("Smoothing window must be an odd number of days")

    series_length = len(raw_means)
    half_window = window_days // 2
    smoothed: list[np.ndarray | None] = [None] * series_length

    for index in range(series_length):
        neighbors: list[np.ndarray] = []
        for offset in range(-half_window, half_window + 1):
            candidate = raw_means[(index + offset) % series_length]
            if candidate is not None:
                neighbors.append(candidate)
        if not neighbors:
            continue
        stack = np.stack(neighbors, axis=0).astype(np.float32, copy=False)
        smoothed[index] = np.nanmean(stack, axis=0).astype(np.float32, copy=False)
    return smoothed


def _write_baseline_asset(
    *,
    path: Path,
    values: np.ndarray,
    transform,
    field: str,
    reference_period: str,
    sample_count: int,
    source_year_range: str,
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
        crs="EPSG:3857",
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
            sample_count=int(sample_count),
            source_year_range=source_year_range,
        )


def build_climatology_assets(
    *,
    source_root: Path,
    data_root: Path,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
    units_in: str,
    smoothing_window_days: int,
    resampling: str,
    start_year: int | None,
    end_year: int | None,
    require_complete: bool,
) -> tuple[int, int]:
    source_key = normalize_baseline_source(baseline_source)
    field_key = str(field).strip().lower()
    region_key = str(region).strip().lower()
    reference_period_key = str(reference_period).strip()
    buckets = _scan_source_rasters(source_root, start_year=start_year, end_year=end_year)
    if not buckets:
        raise ValueError(f"No source rasters found under {source_root}")

    bbox, grid_m = get_baseline_grid_params(
        baseline_source=source_key,
        region=region_key,
    )
    transform, height, width = compute_transform_and_shape(bbox, grid_m)
    expected_shape = (height, width)
    output_root = climatology_baseline_root(
        data_root=data_root,
        version=version,
        baseline_source=source_key,
        field=field_key,
        region=region_key,
        reference_period=reference_period_key,
    )

    files_written = 0
    missing_buckets: list[str] = []

    for hour in SUPPORTED_HOURS:
        raw_means: list[np.ndarray | None] = [None] * 366
        sample_counts: list[int] = [0] * 366
        for doy in range(1, 367):
            bucket_sources = buckets.get((doy, hour), [])
            if not bucket_sources:
                missing_buckets.append(f"doy_{doy:03d}_h{hour:02d}")
                continue
            bucket_mean = _bucket_mean(
                bucket_sources,
                baseline_source=source_key,
                region=region_key,
                field=field_key,
                units_in=units_in,
                resampling=resampling,
            )
            if bucket_mean.shape != expected_shape:
                raise ValueError(
                    f"Unexpected warped shape for doy={doy} hour={hour}: {bucket_mean.shape} expected={expected_shape}"
                )
            raw_means[doy - 1] = bucket_mean
            sample_counts[doy - 1] = len(bucket_sources)

        smoothed_means = _smooth_doy_series(raw_means, smoothing_window_days)
        for doy in range(1, 367):
            values = smoothed_means[doy - 1]
            if values is None:
                continue
            year_range = "all" if start_year is None and end_year is None else f"{start_year or 'min'}-{end_year or 'max'}"
            _write_baseline_asset(
                path=output_root / f"doy_{doy:03d}_h{hour:02d}.tif",
                values=values,
                transform=transform,
                field=field_key,
                reference_period=reference_period_key,
                sample_count=sample_counts[doy - 1],
                source_year_range=year_range,
            )
            files_written += 1

    if require_complete and missing_buckets:
        raise ValueError(
            "Missing climatology source coverage for required buckets: "
            + ", ".join(missing_buckets[:20])
            + (" ..." if len(missing_buckets) > 20 else "")
        )
    return files_written, len(missing_buckets)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build climatology baseline GeoTIFF assets from historical GeoTIFF source rasters.")
    parser.add_argument("--source-root", required=True, help="Root directory containing historical source GeoTIFFs.")
    parser.add_argument("--data-root", required=True, help="CartoSky data root where climatology assets will be written.")
    parser.add_argument("--version", required=True, help="Climatology asset version, for example v1.")
    parser.add_argument("--baseline-source", required=True, help="Shared baseline source key, for example era5.")
    parser.add_argument("--field", required=True, choices=["tmp2m", "tmp850", "hgt500"], help="Baseline field to build.")
    parser.add_argument("--region", default="conus", help="Target region key. Default: conus.")
    parser.add_argument("--reference-period", required=True, help="Reference period label, for example 1991-2020.")
    parser.add_argument("--units-in", required=True, help="Units of the source rasters, for example C, K, F, m, or dam.")
    parser.add_argument("--smoothing-window-days", type=int, default=15, help="Odd-number circular smoothing window in days. Default: 15.")
    parser.add_argument("--resampling", default="bilinear", help="Raster resampling mode for source warping. Default: bilinear.")
    parser.add_argument("--start-year", type=int, default=None, help="Optional inclusive lower bound for source years.")
    parser.add_argument("--end-year", type=int, default=None, help="Optional inclusive upper bound for source years.")
    parser.add_argument("--require-complete", action="store_true", help="Fail if any day-of-year/hour bucket is missing source coverage.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files_written, missing_buckets = build_climatology_assets(
        source_root=Path(args.source_root).resolve(),
        data_root=Path(args.data_root).resolve(),
        version=args.version,
        baseline_source=args.baseline_source,
        field=args.field,
        region=args.region,
        reference_period=args.reference_period,
        units_in=args.units_in,
        smoothing_window_days=int(args.smoothing_window_days),
        resampling=args.resampling,
        start_year=args.start_year,
        end_year=args.end_year,
        require_complete=bool(args.require_complete),
    )
    print(
        "Built climatology baseline assets:",
        {
            "files_written": files_written,
            "missing_buckets": missing_buckets,
            "field": args.field,
            "baseline_source": args.baseline_source,
            "version": args.version,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
