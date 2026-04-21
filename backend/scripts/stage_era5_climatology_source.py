from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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

STANDARD_GRAVITY = np.float32(9.80665)


@dataclass(frozen=True)
class Era5FieldSpec:
    field: str
    archive_family: str
    variable_name: str
    level_hpa: int | None
    staged_units: str


FIELD_SPECS: dict[str, Era5FieldSpec] = {
    "tmp2m": Era5FieldSpec(
        field="tmp2m",
        archive_family="single-levels",
        variable_name="t2m",
        level_hpa=None,
        staged_units="K",
    ),
    "tmp850": Era5FieldSpec(
        field="tmp850",
        archive_family="pressure-levels",
        variable_name="t",
        level_hpa=850,
        staged_units="K",
    ),
    "hgt500": Era5FieldSpec(
        field="hgt500",
        archive_family="pressure-levels",
        variable_name="z",
        level_hpa=500,
        staged_units="m",
    ),
}


def _import_xarray() -> Any:
    try:
        import xarray as xr
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "xarray is required for ERA5 staging. Install prep-time dependencies, for example: "
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


def _output_path(stage_root: Path, *, spec: Era5FieldSpec, valid_time: datetime) -> Path:
    timestamp = valid_time.strftime("%Y%m%d%H")
    return (
        stage_root
        / "era5"
        / spec.archive_family
        / spec.field
        / valid_time.strftime("%Y")
        / f"{timestamp}_{spec.field}.tif"
    )


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


def _prepare_slice(values: np.ndarray, *, spec: Era5FieldSpec) -> np.ndarray:
    prepared = np.asarray(values, dtype=np.float32)
    if spec.field == "hgt500":
        prepared = prepared / STANDARD_GRAVITY
    return prepared.astype(np.float32, copy=False)


def _write_stage_raster(path: Path, *, values: np.ndarray, longitudes: np.ndarray, latitudes: np.ndarray, spec: Era5FieldSpec) -> None:
    normalized_values, normalized_lons = _normalize_longitudes(values, longitudes)
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
            field=spec.field,
            staged_units=spec.staged_units,
        )


def _iter_input_files(input_root: Path) -> list[Path]:
    return sorted(
        path for path in input_root.rglob("*") if path.is_file() and path.suffix.lower() in {".nc", ".nc4", ".cdf"}
    )


def _select_data_array(ds: Any, *, spec: Era5FieldSpec) -> Any:
    if spec.variable_name not in ds:
        raise KeyError(f"ERA5 variable {spec.variable_name!r} not found in dataset")
    selected = ds[spec.variable_name]
    if spec.level_hpa is None:
        return selected
    for dim_name in ("pressure_level", "level", "isobaricInhPa"):
        if dim_name in selected.dims or dim_name in selected.coords:
            return selected.sel({dim_name: spec.level_hpa})
    raise KeyError(f"Could not resolve pressure level dimension for {spec.field}")


def stage_era5_source(
    *,
    input_root: Path,
    stage_root: Path,
    field: str,
    hours: tuple[int, ...],
    start_year: int | None,
    end_year: int | None,
    overwrite: bool,
) -> tuple[int, int]:
    xr = _import_xarray()
    spec = FIELD_SPECS[str(field).strip().lower()]
    written = 0
    skipped = 0

    input_files = _iter_input_files(input_root)
    if not input_files:
        raise ValueError(f"No ERA5 NetCDF files found under {input_root}")

    for input_path in input_files:
        with xr.open_dataset(input_path) as ds:
            data_array = _select_data_array(ds, spec=spec)
            longitudes = np.asarray(ds["longitude"].values, dtype=np.float64)
            latitudes = np.asarray(ds["latitude"].values, dtype=np.float64)

            if "time" not in data_array.coords:
                raise KeyError(f"Dataset missing time coordinate: {input_path}")

            for time_value in data_array["time"].values:
                valid_time = _coerce_valid_time(time_value)
                if valid_time.hour not in hours:
                    continue
                if start_year is not None and valid_time.year < int(start_year):
                    continue
                if end_year is not None and valid_time.year > int(end_year):
                    continue
                output_path = _output_path(stage_root, spec=spec, valid_time=valid_time)
                if output_path.exists() and not overwrite:
                    skipped += 1
                    continue
                time_slice = data_array.sel(time=time_value)
                values = _prepare_slice(np.asarray(time_slice.values), spec=spec)
                _write_stage_raster(
                    output_path,
                    values=values,
                    longitudes=longitudes,
                    latitudes=latitudes,
                    spec=spec,
                )
                written += 1
    return written, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage ERA5 NetCDF data into normalized GeoTIFF rasters for climatology generation.")
    parser.add_argument("--input-root", required=True, help="Root directory containing ERA5 NetCDF files.")
    parser.add_argument("--stage-root", required=True, help="Root directory for staged normalized rasters.")
    parser.add_argument("--field", required=True, choices=sorted(FIELD_SPECS.keys()), help="Field to stage.")
    parser.add_argument("--hours", nargs="+", type=int, default=[0, 6, 12, 18], help="UTC hours to stage. Default: 0 6 12 18.")
    parser.add_argument("--start-year", type=int, default=None, help="Optional inclusive lower year bound.")
    parser.add_argument("--end-year", type=int, default=None, help="Optional inclusive upper year bound.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing staged rasters.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    written, skipped = stage_era5_source(
        input_root=Path(args.input_root).resolve(),
        stage_root=Path(args.stage_root).resolve(),
        field=args.field,
        hours=tuple(int(hour) for hour in args.hours),
        start_year=args.start_year,
        end_year=args.end_year,
        overwrite=bool(args.overwrite),
    )
    print(
        "Staged ERA5 climatology source rasters:",
        {
            "field": args.field,
            "written": written,
            "skipped": skipped,
            "stage_root": str(Path(args.stage_root).resolve()),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())