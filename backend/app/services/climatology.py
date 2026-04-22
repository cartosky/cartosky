from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS

from .builder.cog_writer import REGION_BBOX_3857, compute_transform_and_shape

_configured_data_root: Path | None = None
DEFAULT_BASELINE_SOURCE = "era5"
_BASELINE_SOURCE_ALIASES = {
    "shared": DEFAULT_BASELINE_SOURCE,
}
_BASELINE_SOURCE_GRID_METERS: dict[str, dict[str, float]] = {
    DEFAULT_BASELINE_SOURCE: {
        "conus": 25_000.0,
        "na": 25_000.0,
    },
}


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


def normalize_baseline_source(baseline_source: str | None) -> str:
    normalized = str(baseline_source or DEFAULT_BASELINE_SOURCE).strip().lower()
    if not normalized:
        return DEFAULT_BASELINE_SOURCE
    return _BASELINE_SOURCE_ALIASES.get(normalized, normalized)


def get_baseline_grid_params(
    *,
    baseline_source: str,
    region: str,
) -> tuple[tuple[float, float, float, float], float]:
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()

    bbox = REGION_BBOX_3857.get(region_key)
    if bbox is None:
        raise KeyError(f"Unknown climatology baseline region: {region!r}")

    source_grids = _BASELINE_SOURCE_GRID_METERS.get(source_key)
    if source_grids is None:
        raise KeyError(f"Unknown climatology baseline source: {baseline_source!r}")
    grid_m = source_grids.get(region_key)
    if grid_m is None:
        raise KeyError(
            f"No climatology baseline grid configured for source={source_key!r} region={region_key!r}"
        )
    return bbox, float(grid_m)


def get_baseline_target_grid(
    *,
    baseline_source: str,
    region: str,
) -> dict[str, str]:
    region_key = str(region).strip().lower()
    _, grid_m = get_baseline_grid_params(
        baseline_source=baseline_source,
        region=region_key,
    )
    source_key = normalize_baseline_source(baseline_source)
    return {
        "region": region_key,
        "id": f"climatology:{source_key}:{region_key}:{grid_m:.1f}m",
    }


def climatology_baseline_root(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
) -> Path:
    root = Path(data_root).resolve() if data_root is not None else _resolve_data_root()
    return (
        root
        / "climatology"
        / str(version).strip()
        / normalize_baseline_source(baseline_source)
        / "baseline"
        / str(field).strip().lower()
        / str(region).strip().lower()
        / str(reference_period).strip()
    )


def legacy_climatology_baseline_path(
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


def climatology_baseline_path(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
    valid_time,
) -> Path:
    doy = int(valid_time.timetuple().tm_yday)
    hour = int(valid_time.hour)
    root = climatology_baseline_root(
        data_root=data_root,
        version=version,
        baseline_source=baseline_source,
        field=field,
        region=region,
        reference_period=reference_period,
    )
    return root / f"doy_{doy:03d}_h{hour:02d}.tif"


def load_climatology_baseline(
    *,
    version: str,
    baseline_source: str,
    field: str,
    valid_time,
    region: str,
    reference_period: str,
    legacy_model_family_fallback: str | None = None,
) -> tuple[np.ndarray, CRS, rasterio.transform.Affine, dict[str, Any]]:
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()
    path = climatology_baseline_path(
        version=version,
        baseline_source=source_key,
        field=field,
        region=region_key,
        reference_period=reference_period,
        valid_time=valid_time,
    )
    used_legacy_fallback = False
    if not path.is_file():
        fallback_family = str(legacy_model_family_fallback or "").strip().lower()
        if fallback_family:
            fallback_path = legacy_climatology_baseline_path(
                version=version,
                model_family=fallback_family,
                field=field,
                valid_time=valid_time,
            )
            if fallback_path.is_file():
                path = fallback_path
                used_legacy_fallback = True
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing climatology baseline asset: {path}"
            )

    expected_bbox, expected_grid_m = get_baseline_grid_params(
        baseline_source=source_key,
        region=region_key,
    )
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
        "baseline_source": source_key,
        "baseline_field": str(field).strip().lower(),
        "baseline_region": region_key,
        "baseline_alignment": "valid_time",
        "reference_period": str(reference_period).strip(),
        "baseline_legacy_fallback": used_legacy_fallback,
    }
    return data, CRS.from_epsg(3857), transform, metadata
