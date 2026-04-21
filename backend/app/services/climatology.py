from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS

from .builder.cog_writer import compute_transform_and_shape, get_grid_params

_configured_data_root: Path | None = None


def configure_data_root(data_root: Path) -> None:
    global _configured_data_root
    _configured_data_root = Path(data_root).resolve()


def _resolve_data_root() -> Path:
    if _configured_data_root is not None:
        return _configured_data_root
    raw = (
        os.environ.get("CARTOSKY_DATA_ROOT")
        or os.environ.get("CARTOSKY_V3_DATA_ROOT")
        or os.environ.get("TWF_V3_DATA_ROOT")
        or "./data"
    )
    return Path(raw).resolve()


def climatology_baseline_path(
    *,
    version: str,
    model_family: str,
    field: str,
    valid_time,
) -> Path:
    doy = int(valid_time.timetuple().tm_yday)
    hour = int(valid_time.hour)
    return (
        _resolve_data_root()
        / "climatology"
        / str(version).strip()
        / str(model_family).strip().lower()
        / "baseline"
        / str(field).strip().lower()
        / f"doy_{doy:03d}_h{hour:02d}.tif"
    )


def load_climatology_baseline(
    *,
    version: str,
    model_family: str,
    field: str,
    valid_time,
    region: str,
    reference_period: str,
) -> tuple[np.ndarray, CRS, rasterio.transform.Affine, dict[str, Any]]:
    path = climatology_baseline_path(
        version=version,
        model_family=model_family,
        field=field,
        valid_time=valid_time,
    )
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing climatology baseline asset: {path}"
        )

    expected_bbox, expected_grid_m = get_grid_params(str(model_family).strip().lower(), region)
    expected_transform, expected_height, expected_width = compute_transform_and_shape(
        expected_bbox,
        expected_grid_m,
    )

    with rasterio.open(path) as ds:
        data = ds.read(1).astype(np.float32, copy=False)
        crs = ds.crs
        transform = ds.transform
        width = int(ds.width)
        height = int(ds.height)

    if crs is None:
        raise ValueError(f"Climatology baseline asset missing CRS: {path}")
    if CRS.from_user_input(crs) != CRS.from_epsg(3857):
        raise ValueError(f"Climatology baseline asset must use EPSG:3857: {path}")
    if height != expected_height or width != expected_width:
        raise ValueError(
            "Climatology baseline asset grid shape mismatch: "
            f"expected={(expected_height, expected_width)} actual={(height, width)} path={path}"
        )
    if any(
        abs(float(actual) - float(expected)) > 1.0e-6
        for actual, expected in zip(transform[:6], expected_transform[:6])
    ):
        raise ValueError(
            "Climatology baseline asset transform mismatch: "
            f"expected={expected_transform} actual={transform} path={path}"
        )

    metadata = {
        "baseline_kind": "climatology",
        "baseline_version": str(version).strip(),
        "baseline_model_family": str(model_family).strip().lower(),
        "baseline_field": str(field).strip().lower(),
        "baseline_alignment": "valid_time",
        "reference_period": str(reference_period).strip(),
    }
    return data, CRS.from_epsg(3857), transform, metadata