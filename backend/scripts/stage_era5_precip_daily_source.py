from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

METERS_TO_INCHES = np.float32(39.37007874015748)
MM_TO_INCHES = np.float32(1.0 / 25.4)


@dataclass(frozen=True)
class PrecipStageSpec:
    field: str = "precip_daily"
    archive_family: str = "single-levels"
    variable_name: str = "tp"
    long_variable_name: str = "total_precipitation"
    staged_units: str = "inches"


PRECIP_SPEC = PrecipStageSpec()


def _import_xarray() -> Any:
    try:
        import xarray as xr
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "xarray is required for ERA5 precip staging. Install prep-time dependencies, for example: "
            "python -m pip install xarray netcdf4 cdsapi"
        ) from exc
    return xr


def _normalize_longitudes(values: np.ndarray, longitudes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normalized = np.asarray(longitudes, dtype=np.float64)
    normalized = ((normalized + 180.0) % 360.0) - 180.0
    order = np.argsort(normalized)
    return values[..., order], normalized[order]


def _normalize_latitudes(values: np.ndarray, latitudes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    latitudes_array = np.asarray(latitudes, dtype=np.float64)
    if latitudes_array[0] >= latitudes_array[-1]:
        return values, latitudes_array
    return values[::-1, :], latitudes_array[::-1]


def _regular_resolution(coords: np.ndarray) -> float:
    diffs = np.diff(np.asarray(coords, dtype=np.float64))
    if diffs.size == 0:
        raise ValueError("Coordinate axis must contain at least two values")
    resolution = float(np.median(np.abs(diffs)))
    if resolution <= 0.0:
        raise ValueError("Coordinate axis resolution must be positive")
    return resolution


def _transform_from_latlon(longitudes: np.ndarray, latitudes: np.ndarray):
    xres = _regular_resolution(longitudes)
    yres = _regular_resolution(latitudes)
    west = float(longitudes[0]) - xres / 2.0
    north = float(latitudes[0]) + yres / 2.0
    return from_origin(west, north, xres, yres)


def _coerce_valid_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, np.datetime64):
        seconds = value.astype("datetime64[s]").astype(np.int64)
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _convert_precip_to_inches(values: np.ndarray, *, units_in: str) -> np.ndarray:
    normalized_units = str(units_in).strip().lower()
    output = np.asarray(values, dtype=np.float32)
    if normalized_units in {"in", "inch", "inches"}:
        return output
    if normalized_units in {"m", "meter", "meters"}:
        return output * METERS_TO_INCHES
    if normalized_units in {"mm", "millimeter", "millimeters"}:
        return output * MM_TO_INCHES
    raise ValueError(f"Unsupported ERA5 precip units: {units_in}")


def _output_path(stage_root: Path, *, valid_date: date) -> Path:
    return (
        stage_root
        / "era5"
        / PRECIP_SPEC.archive_family
        / PRECIP_SPEC.field
        / f"{valid_date:%Y}"
        / f"{valid_date:%Y%m%d}_{PRECIP_SPEC.field}.tif"
    )


def _iter_input_files(input_root: Path) -> list[Path]:
    return sorted(
        path for path in input_root.rglob("*") if path.is_file() and path.suffix.lower() in {".nc", ".nc4", ".cdf"}
    )


def _select_precip_data_array(ds: Any) -> Any:
    for variable_name in (PRECIP_SPEC.variable_name, PRECIP_SPEC.long_variable_name):
        if variable_name in ds:
            selected = ds[variable_name]
            break
    else:
        raise KeyError(
            f"ERA5 total precipitation variable not found. Expected one of "
            f"{PRECIP_SPEC.variable_name!r} or {PRECIP_SPEC.long_variable_name!r}"
        )

    drop_selectors: dict[str, Any] = {}
    for dim_name in selected.dims:
        if dim_name in {"time", "valid_time", "latitude", "longitude"}:
            continue
        if selected.sizes.get(dim_name, 1) == 1:
            drop_selectors[dim_name] = 0
    if drop_selectors:
        selected = selected.isel(drop_selectors)
    return selected


def _resolve_time_coord(data_array: Any, ds: Any) -> tuple[Any, str]:
    if "time" in data_array.coords:
        return data_array, "time"
    if "valid_time" in data_array.coords:
        return data_array.rename({"valid_time": "time"}), "time"
    if "valid_time" in ds.coords:
        renamed = ds.rename({"valid_time": "time"})
        return _select_precip_data_array(renamed), "time"
    raise KeyError("Dataset missing time or valid_time coordinate")


def _transpose_time_lat_lon(data_array: Any, *, time_coord: str) -> Any:
    dims = tuple(getattr(data_array, "dims", ()))
    if dims == (time_coord, "latitude", "longitude"):
        return data_array
    if time_coord not in dims or "latitude" not in dims or "longitude" not in dims:
        raise ValueError(
            "ERA5 precip array must have time, latitude, and longitude dimensions; "
            f"found dims={dims}"
        )
    return data_array.transpose(time_coord, "latitude", "longitude")


def _valid_times_for_month(
    time_values: np.ndarray,
    *,
    start_year: int | None,
    end_year: int | None,
) -> list[tuple[int, datetime]]:
    valid_times: list[tuple[int, datetime]] = []
    for index, time_value in enumerate(time_values):
        valid_time = _coerce_valid_time(time_value)
        if start_year is not None and valid_time.year < int(start_year):
            continue
        if end_year is not None and valid_time.year > int(end_year):
            continue
        valid_times.append((index, valid_time))
    return valid_times


def _daily_groups(valid_times: list[tuple[int, datetime]]) -> list[tuple[date, np.ndarray]]:
    by_date: dict[date, list[int]] = {}
    for source_index, valid_time in valid_times:
        by_date.setdefault(valid_time.date(), []).append(source_index)
    return [(valid_date, np.asarray(indices, dtype=np.int64)) for valid_date, indices in sorted(by_date.items())]


def _write_daily_raster(
    path: Path,
    *,
    values_inches: np.ndarray,
    longitudes: np.ndarray,
    latitudes: np.ndarray,
    source_hours: int,
) -> None:
    normalized_values, normalized_lons = _normalize_longitudes(values_inches, longitudes)
    normalized_values, normalized_lats = _normalize_latitudes(normalized_values, latitudes)
    transform = _transform_from_latlon(normalized_lons, normalized_lats)

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=normalized_values.shape[0],
        width=normalized_values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=float("nan"),
        tiled=True,
        compress="deflate",
    ) as ds:
        ds.write(normalized_values.astype(np.float32), 1)
        ds.update_tags(
            1,
            source_archive="ERA5",
            source_dataset="reanalysis-era5-single-levels",
            source_variable="total_precipitation",
            source_short_name="tp",
            source_semantics="hourly_accumulation_summed_to_utc_daily_total",
            staged_units=PRECIP_SPEC.staged_units,
            source_hour_count=int(source_hours),
        )


def stage_era5_precip_daily_source(
    *,
    input_root: Path,
    stage_root: Path,
    start_year: int | None,
    end_year: int | None,
    units_in: str,
    overwrite: bool,
    require_24_hours: bool,
) -> tuple[int, int]:
    xr = _import_xarray()
    input_files = _iter_input_files(input_root)
    if not input_files:
        raise ValueError(f"No ERA5 NetCDF files found under {input_root}")

    reference_longitudes: np.ndarray | None = None
    reference_latitudes: np.ndarray | None = None
    total_written = 0
    total_skipped = 0

    for file_index, input_path in enumerate(input_files, start=1):
        file_start = time.monotonic()
        file_written = 0
        file_skipped = 0
        file_days = 0
        print(
            "Processing ERA5 precip file:",
            {"index": file_index, "total": len(input_files), "path": str(input_path)},
            flush=True,
        )
        with xr.open_dataset(input_path) as ds:
            data_array, time_coord = _resolve_time_coord(_select_precip_data_array(ds), ds)
            data_array = _transpose_time_lat_lon(data_array, time_coord=time_coord)
            file_longitudes = np.asarray(ds["longitude"].values, dtype=np.float64)
            file_latitudes = np.asarray(ds["latitude"].values, dtype=np.float64)
            if reference_longitudes is None:
                reference_longitudes = file_longitudes
                reference_latitudes = file_latitudes
            elif not np.array_equal(reference_longitudes, file_longitudes) or not np.array_equal(reference_latitudes, file_latitudes):
                raise ValueError(f"ERA5 precip input grid changed within archive: {input_path}")

            valid_times = _valid_times_for_month(
                np.asarray(data_array[time_coord].values),
                start_year=start_year,
                end_year=end_year,
            )
            daily_groups = _daily_groups(valid_times)
            file_days = len(daily_groups)
            if not daily_groups:
                print(
                    "Finished ERA5 precip file:",
                    {
                        "index": file_index,
                        "total": len(input_files),
                        "path": str(input_path),
                        "written": 0,
                        "skipped": 0,
                        "processed_days": 0,
                        "elapsed_seconds": round(time.monotonic() - file_start, 3),
                    },
                    flush=True,
                )
                continue

            output_by_date = {
                valid_date: _output_path(stage_root, valid_date=valid_date)
                for valid_date, _indices in daily_groups
            }
            skip_dates = {
                valid_date
                for valid_date, output_path in output_by_date.items()
                if output_path.exists() and not overwrite
            }
            file_skipped = len(skip_dates)
            total_skipped += file_skipped
            if len(skip_dates) == len(daily_groups):
                print(
                    "Finished ERA5 precip file:",
                    {
                        "index": file_index,
                        "total": len(input_files),
                        "path": str(input_path),
                        "written": 0,
                        "skipped": file_skipped,
                        "processed_days": file_days,
                        "elapsed_seconds": round(time.monotonic() - file_start, 3),
                    },
                    flush=True,
                )
                continue

            monthly_values_inches = _convert_precip_to_inches(np.asarray(data_array.values), units_in=units_in)
            if monthly_values_inches.ndim != 3:
                raise ValueError(
                    "ERA5 precip array must load as a 3-D time/latitude/longitude cube; "
                    f"found shape={monthly_values_inches.shape}"
                )
            if monthly_values_inches.shape[0] < max(int(indices.max()) for _date, indices in daily_groups) + 1:
                raise ValueError(
                    "ERA5 precip time dimension does not cover grouped daily indices: "
                    f"values={monthly_values_inches.shape[0]}"
                )

            for valid_date, hour_indices in daily_groups:
                if valid_date in skip_dates:
                    continue
                source_hours = int(hour_indices.size)
                if require_24_hours and source_hours != 24:
                    raise ValueError(
                        f"Incomplete hourly coverage for {valid_date:%Y-%m-%d}: "
                        f"expected 24, found {source_hours}"
                    )
                daily_total_inches = np.sum(monthly_values_inches[hour_indices, :, :], axis=0, dtype=np.float32)
                _write_daily_raster(
                    output_by_date[valid_date],
                    values_inches=daily_total_inches,
                    longitudes=file_longitudes,
                    latitudes=file_latitudes,
                    source_hours=source_hours,
                )
                file_written += 1
                total_written += 1

        print(
            "Finished ERA5 precip file:",
            {
                "index": file_index,
                "total": len(input_files),
                "path": str(input_path),
                "written": file_written,
                "skipped": file_skipped,
                "processed_days": file_days,
                "elapsed_seconds": round(time.monotonic() - file_start, 3),
            },
            flush=True,
        )

    if reference_longitudes is None or reference_latitudes is None:
        raise ValueError("No ERA5 precip time slices matched the requested year range")

    return total_written, total_skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage hourly ERA5 total_precipitation into normalized UTC daily accumulated precip GeoTIFFs. "
            "ERA5 total_precipitation is handled as an accumulation field, not as an instantaneous field."
        )
    )
    parser.add_argument("--input-root", required=True, help="Root directory containing ERA5 total_precipitation NetCDF files.")
    parser.add_argument("--stage-root", required=True, help="Root directory for staged normalized daily precip rasters.")
    parser.add_argument("--start-year", type=int, default=None, help="Optional inclusive lower year bound.")
    parser.add_argument("--end-year", type=int, default=None, help="Optional inclusive upper year bound.")
    parser.add_argument("--units-in", default="meters", help="Units of raw ERA5 total_precipitation values. Default: meters.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing staged rasters.")
    parser.add_argument(
        "--allow-incomplete-days",
        action="store_true",
        help="Allow writing days with fewer than 24 hourly values. Default is to require 24 hours per day.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    written, skipped = stage_era5_precip_daily_source(
        input_root=Path(args.input_root).resolve(),
        stage_root=Path(args.stage_root).resolve(),
        start_year=args.start_year,
        end_year=args.end_year,
        units_in=args.units_in,
        overwrite=bool(args.overwrite),
        require_24_hours=not bool(args.allow_incomplete_days),
    )
    print(
        "Staged ERA5 daily precip source rasters:",
        {
            "written": written,
            "skipped": skipped,
            "stage_root": str(Path(args.stage_root).resolve()),
            "staged_units": PRECIP_SPEC.staged_units,
            "source_semantics": "hourly total_precipitation accumulations summed into UTC daily totals",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
