from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import xarray as xr
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject

from app.services.builder.cog_writer import compute_transform_and_shape, get_grid_params


class GOESProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class GOESDecodedFrame:
    valid_time: datetime
    values: np.ndarray
    transform: Affine
    projection: str = "EPSG:3857"
    source_crs: str | None = None
    source_transform: Affine | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


def decode_goes_scan(
    scan_path: Path,
    *,
    model: str = "goes-east",
    region: str = "conus",
    resampling: str = "bilinear",
) -> GOESDecodedFrame:
    try:
        with xr.open_dataset(scan_path, engine="h5netcdf") as ds:
            _require_dataset_fields(ds)
            cmi = np.asarray(ds["CMI"].values, dtype=np.float32)
            dqf = np.asarray(ds["DQF"].values, dtype=np.float32)
            source_values = np.where(np.isfinite(cmi) & np.isfinite(dqf) & (dqf <= 1), cmi, np.nan).astype(np.float32)
            src_crs, src_transform, source_projection_meta = abi_source_geometry(ds)
            valid_time = _parse_dataset_time(ds["t"].values)
            time_coverage_start = _dataset_string_scalar(ds, "time_coverage_start")
            time_coverage_end = _dataset_string_scalar(ds, "time_coverage_end")
            band_id = _dataset_int_scalar(ds, "band_id")
            band_wavelength = _dataset_float_scalar(ds, "band_wavelength")
            dataset_name = _dataset_string_scalar(ds, "dataset_name")
            date_created = _dataset_string_scalar(ds, "date_created")
    except Exception as exc:
        raise GOESProcessingError(f"Unable to decode GOES scan {scan_path}") from exc

    bbox, grid_m = get_grid_params(model, region)
    dst_transform, dst_h, dst_w = compute_transform_and_shape(bbox, grid_m)
    dst = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
    reproject(
        source=source_values,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=CRS.from_epsg(3857),
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling[resampling],
    )
    source_metadata: dict[str, Any] = {
        "time_coverage_start": time_coverage_start,
        "time_coverage_end": time_coverage_end,
        "date_created": date_created,
        "band_id": band_id,
        "band_wavelength_um": band_wavelength,
        "dataset_name": dataset_name,
        "source_projection": source_projection_meta,
        "dqf_mask": "DQF <= 1",
    }
    return GOESDecodedFrame(
        valid_time=valid_time,
        values=dst.astype(np.float32, copy=False),
        transform=dst_transform,
        source_crs=src_crs.to_string(),
        source_transform=src_transform,
        source_metadata={key: value for key, value in source_metadata.items() if value is not None},
    )


def abi_source_geometry(ds: xr.Dataset) -> tuple[CRS, Affine, dict[str, Any]]:
    projection = ds["goes_imager_projection"]
    attrs = projection.attrs
    height = _required_float_attr(attrs, "perspective_point_height")
    semi_major_axis = _required_float_attr(attrs, "semi_major_axis")
    semi_minor_axis = _required_float_attr(attrs, "semi_minor_axis")
    lon_0 = _required_float_attr(attrs, "longitude_of_projection_origin")
    sweep = str(attrs.get("sweep_angle_axis") or "x").strip() or "x"
    x = np.asarray(ds["x"].values, dtype=np.float64) * height
    y = np.asarray(ds["y"].values, dtype=np.float64) * height
    if x.ndim != 1 or y.ndim != 1 or x.size < 2 or y.size < 2:
        raise GOESProcessingError("GOES x/y coordinates must be one-dimensional arrays with at least two points")
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    transform = Affine.translation(float(x[0] - dx / 2.0), float(y[0] - dy / 2.0)) * Affine.scale(dx, dy)
    crs = CRS.from_proj4(
        f"+proj=geos +h={height} +lon_0={lon_0} +sweep={sweep} "
        f"+a={semi_major_axis} +b={semi_minor_axis} +units=m +no_defs"
    )
    return crs, transform, {
        "grid_mapping_name": str(attrs.get("grid_mapping_name") or ""),
        "perspective_point_height": height,
        "semi_major_axis": semi_major_axis,
        "semi_minor_axis": semi_minor_axis,
        "longitude_of_projection_origin": lon_0,
        "sweep_angle_axis": sweep,
        "source_dx_m": dx,
        "source_dy_m": dy,
    }


def _require_dataset_fields(ds: xr.Dataset) -> None:
    for name in ("CMI", "DQF", "goes_imager_projection"):
        if name not in ds:
            raise GOESProcessingError(f"Missing required GOES data variable: {name}")
    for name in ("x", "y", "t"):
        if name not in ds.coords:
            raise GOESProcessingError(f"Missing required GOES coordinate: {name}")


def _required_float_attr(attrs: dict[str, Any], name: str) -> float:
    raw = attrs.get(name)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise GOESProcessingError(f"Missing or invalid GOES projection attribute: {name}") from exc
    if not np.isfinite(value):
        raise GOESProcessingError(f"Invalid GOES projection attribute: {name}")
    return value


def _parse_dataset_time(value: Any) -> datetime:
    scalar = np.asarray(value).astype("datetime64[ns]")
    ns = int(scalar.astype("int64"))
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)


def _string_scalar(value: Any) -> str | None:
    arr = np.asarray(value)
    if arr.shape != ():
        return None
    item = arr.item()
    if isinstance(item, bytes):
        return item.decode("utf-8", errors="replace")
    return str(item)


def _dataset_value(ds: xr.Dataset, name: str) -> Any:
    if name in ds.coords:
        return ds.coords[name].values
    if name in ds:
        return ds[name].values
    return ds.attrs.get(name)


def _dataset_string_scalar(ds: xr.Dataset, name: str) -> str | None:
    value = _dataset_value(ds, name)
    if value is None:
        return None
    return _string_scalar(value)


def _dataset_int_scalar(ds: xr.Dataset, name: str) -> int | None:
    value = _dataset_value(ds, name)
    if value is None:
        return None
    return _int_scalar(value)


def _dataset_float_scalar(ds: xr.Dataset, name: str) -> float | None:
    value = _dataset_value(ds, name)
    if value is None:
        return None
    return _float_scalar(value)


def _int_scalar(value: Any) -> int | None:
    arr = np.asarray(value)
    if arr.size < 1:
        return None
    try:
        return int(arr.reshape(-1)[0])
    except (TypeError, ValueError):
        return None


def _float_scalar(value: Any) -> float | None:
    arr = np.asarray(value)
    if arr.size < 1:
        return None
    try:
        result = float(arr.reshape(-1)[0])
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None
