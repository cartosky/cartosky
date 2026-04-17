from __future__ import annotations

import concurrent.futures
import brotli
import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.transform import Affine, array_bounds
from scipy.ndimage import zoom as ndimage_zoom

from ..config import grid_supported_pair
from .colormaps import get_color_map_spec
from .grid_display_prep import prepare_grid_display_values
from .render_resampling import resampling_name_for_kind, variable_color_map_id

logger = logging.getLogger(__name__)

GRID_MANIFEST_VERSION = 1
GRID_SUBTYPE = "grid"
GRID_PROJECTION = "EPSG:3857"
GRID_DTYPE_UINT8 = "uint8"
GRID_DTYPE_UINT16 = "uint16"
GRID_DTYPE = "uint16"
GRID_ENDIANNESS = "little"
GRID_LEVEL = 0
GRID_DIRNAME = "grid"
LEGACY_GRID_DIRNAME = "grid_v1"

_GRID_LOD_CONFIG_BY_MODEL_VAR: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {
    ("mrms", "reflectivity"): (
        {"level": 0, "scale_factor": 1, "min_zoom": 5.5},
        {"level": 1, "scale_factor": 2, "min_zoom": 4.0, "max_zoom": 5.5},
        {"level": 2, "scale_factor": 4, "max_zoom": 4.0},
    ),
    ("mrms", "mrms_radar_ptype"): (
        {"level": 0, "scale_factor": 1, "min_zoom": 5.5},
        {"level": 1, "scale_factor": 2, "min_zoom": 4.0, "max_zoom": 5.5},
        {"level": 2, "scale_factor": 4, "max_zoom": 4.0},
    ),
}

GRID_GZIP_SIDECARS_ENABLED = str(os.getenv("CARTOSKY_GRID_GZIP_SIDECARS_ENABLED", "1")).strip().lower() not in {
    "",
    "0",
    "false",
    "no",
    "off",
}
try:
    GRID_GZIP_COMPRESSLEVEL = max(1, min(9, int(os.getenv("CARTOSKY_GRID_GZIP_COMPRESSLEVEL", "5"))))
except ValueError:
    GRID_GZIP_COMPRESSLEVEL = 5
GRID_BROTLI_SIDECARS_ENABLED = str(os.getenv("CARTOSKY_GRID_BROTLI_SIDECARS_ENABLED", "1")).strip().lower() not in {
    "",
    "0",
    "false",
    "no",
    "off",
}
try:
    GRID_BROTLI_QUALITY = max(0, min(11, int(os.getenv("CARTOSKY_GRID_BROTLI_QUALITY", "5"))))
except ValueError:
    GRID_BROTLI_QUALITY = 5

_PACKING_BY_MODEL_VAR: dict[tuple[str, str], dict[str, Any]] = {
    ("hrrr", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("hrrr", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("hrrr", "mlcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("hrrr", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("hrrr", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("hrrr", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("hrrr", "radar_ptype"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("hrrr", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gfs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aigfs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "tmp2m__mean"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "sbcape__mean"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gefs", "wspd10m__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gefs", "snowfall_total__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gefs", "pwat__mean"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gefs", "precip_total__mean"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aigfs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("aigfs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aifs", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aigfs", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("ecmwf", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aifs", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aigfs", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("ecmwf", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("aigfs", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("ecmwf", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aigfs", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aifs", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "ptype_intensity"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "index",
    },
    ("ecmwf", "ptype_intensity_rain"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("ecmwf", "ptype_intensity_snow"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("ecmwf", "ptype_intensity_ice"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("ecmwf", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aifs", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aifs", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("aifs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("ecmwf", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("ecmwf", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gfs", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gfs", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("gfs", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "mlcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gfs", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gfs", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "ptype_intensity"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "index",
    },
    ("gfs", "ptype_intensity_rain"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("gfs", "ptype_intensity_snow"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("gfs", "ptype_intensity_ice"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("gfs", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("nam", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nam", "mlcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nam", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nam", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nam", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("nam", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("nam", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nam", "radar_ptype"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nbm", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nbm", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nbm", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nbm", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nbm", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("mrms", "reflectivity"): {
        "dtype": GRID_DTYPE_UINT8,
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 255,
        "units": "dBZ",
    },
    ("mrms", "mrms_radar_ptype"): {
        "dtype": GRID_DTYPE_UINT8,
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 255,
        "units": "index",
    },
}


def grid_code_supported(model_id: str, var_key: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_var = str(var_key or "").strip().lower()
    return _packing_config(normalized_model, normalized_var) is not None


def grid_supported(model_id: str, var_key: str) -> bool:
    return grid_supported_pair(model_id, var_key)


def grid_dir_for_run_root(run_root: Path, var: str) -> Path:
    return Path(run_root) / var / GRID_DIRNAME


def resolved_grid_dir_for_run_root(run_root: Path, var: str) -> Path:
    preferred = grid_dir_for_run_root(run_root, var)
    if preferred.is_dir():
        return preferred
    legacy = Path(run_root) / var / LEGACY_GRID_DIRNAME
    if legacy.is_dir():
        return legacy
    return preferred


def grid_dir(data_root: Path, model: str, run: str, var: str) -> Path:
    return resolved_grid_dir_for_run_root(data_root / "published" / model / run, var)


def grid_manifest_path(data_root: Path, model: str, run: str, var: str) -> Path:
    return grid_dir(data_root, model, run, var) / "manifest.json"


def grid_manifest_path_for_run_root(run_root: Path, var: str) -> Path:
    return grid_dir_for_run_root(run_root, var) / "manifest.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    tmp_path.replace(path)


def grid_dtype(dtype: str | None) -> str:
    normalized = str(dtype or GRID_DTYPE).strip().lower()
    return GRID_DTYPE_UINT8 if normalized == GRID_DTYPE_UINT8 else GRID_DTYPE_UINT16


def grid_bytes_per_sample(dtype: str | None) -> int:
    return 1 if grid_dtype(dtype) == GRID_DTYPE_UINT8 else 2


def grid_frame_dtype_token(dtype: str | None) -> str:
    return "u8" if grid_dtype(dtype) == GRID_DTYPE_UINT8 else "u16"


def grid_frame_filename(fh: int, *, level: int = GRID_LEVEL, dtype: str = GRID_DTYPE) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.{grid_frame_dtype_token(dtype)}.bin"


def grid_frame_path(
    data_root: Path,
    model: str,
    run: str,
    var: str,
    fh: int,
    *,
    level: int = GRID_LEVEL,
    dtype: str = GRID_DTYPE,
) -> Path:
    return grid_dir(data_root, model, run, var) / grid_frame_filename(fh, level=level, dtype=dtype)


def grid_frame_path_for_run_root(
    run_root: Path,
    var: str,
    fh: int,
    *,
    level: int = GRID_LEVEL,
    dtype: str = GRID_DTYPE,
) -> Path:
    return grid_dir_for_run_root(run_root, var) / grid_frame_filename(fh, level=level, dtype=dtype)


def grid_frame_meta_filename(fh: int, *, level: int = GRID_LEVEL) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.meta.json"


def grid_frame_meta_path(data_root: Path, model: str, run: str, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir(data_root, model, run, var) / grid_frame_meta_filename(fh, level=level)


def grid_frame_meta_path_for_run_root(run_root: Path, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir_for_run_root(run_root, var) / grid_frame_meta_filename(fh, level=level)


def resolved_grid_frame_path_for_run_root(
    run_root: Path,
    var: str,
    fh: int,
    *,
    level: int = GRID_LEVEL,
    dtype: str = GRID_DTYPE,
) -> Path:
    return resolved_grid_dir_for_run_root(run_root, var) / grid_frame_filename(fh, level=level, dtype=dtype)


def resolved_grid_frame_meta_path_for_run_root(run_root: Path, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return resolved_grid_dir_for_run_root(run_root, var) / grid_frame_meta_filename(fh, level=level)


def expected_grid_frame_size_bytes(*, width: int, height: int, dtype: str = GRID_DTYPE) -> int:
    return max(0, int(width) * int(height) * grid_bytes_per_sample(dtype))


def grid_lod_specs(model: str, var: str) -> tuple[dict[str, Any], ...]:
    configured = _GRID_LOD_CONFIG_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))
    if not configured:
        return ({"level": GRID_LEVEL, "scale_factor": 1},)

    normalized: list[dict[str, Any]] = []
    for raw_spec in configured:
        level = int(raw_spec.get("level", GRID_LEVEL))
        scale_factor = max(1, int(raw_spec.get("scale_factor", 1)))
        spec: dict[str, Any] = {"level": level, "scale_factor": scale_factor}
        min_zoom = raw_spec.get("min_zoom")
        max_zoom = raw_spec.get("max_zoom")
        if isinstance(min_zoom, (int, float)) and not isinstance(min_zoom, bool):
            spec["min_zoom"] = float(min_zoom)
        if isinstance(max_zoom, (int, float)) and not isinstance(max_zoom, bool):
            spec["max_zoom"] = float(max_zoom)
        normalized.append(spec)
    normalized.sort(key=lambda item: int(item["level"]))
    return tuple(normalized)


def _lod_target_shape(height: int, width: int, scale_factor: int) -> tuple[int, int]:
    return (
        max(1, int(np.ceil(height / max(1, scale_factor)))),
        max(1, int(np.ceil(width / max(1, scale_factor)))),
    )


def _resize_continuous_grid(values: np.ndarray, *, target_height: int, target_width: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if source.shape == (target_height, target_width):
        return source

    finite_mask = np.isfinite(source)
    if not finite_mask.any():
        return np.full((target_height, target_width), np.nan, dtype=np.float32)

    zoom_factors = (target_height / source.shape[0], target_width / source.shape[1])
    filled = np.where(finite_mask, source, 0.0).astype(np.float32, copy=False)
    weights = finite_mask.astype(np.float32, copy=False)
    resized_values = np.asarray(
        ndimage_zoom(filled, zoom_factors, order=1, mode="nearest", prefilter=False),
        dtype=np.float32,
    )
    resized_weights = np.asarray(
        ndimage_zoom(weights, zoom_factors, order=1, mode="nearest", prefilter=False),
        dtype=np.float32,
    )
    output = np.full((target_height, target_width), np.nan, dtype=np.float32)
    np.divide(resized_values, resized_weights, out=output, where=resized_weights > 1e-6)
    return output


def _resize_nearest_grid(values: np.ndarray, *, target_height: int, target_width: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if source.shape == (target_height, target_width):
        return source
    zoom_factors = (target_height / source.shape[0], target_width / source.shape[1])
    resized = ndimage_zoom(source, zoom_factors, order=0, mode="nearest", prefilter=False)
    return np.asarray(resized, dtype=np.float32)


def _values_for_lod(values: np.ndarray, *, model: str, var: str, scale_factor: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if scale_factor <= 1:
        return source

    target_height, target_width = _lod_target_shape(source.shape[0], source.shape[1], scale_factor)
    if resampling_name_for_kind(model_id=model, var_key=var) == "nearest":
        return _resize_nearest_grid(source, target_height=target_height, target_width=target_width)
    return _resize_continuous_grid(source, target_height=target_height, target_width=target_width)


def grid_gzip_sidecar_path(frame_path: Path) -> Path:
    return frame_path.with_name(f"{frame_path.name}.gz")


def grid_brotli_sidecar_path(frame_path: Path) -> Path:
    return frame_path.with_name(f"{frame_path.name}.br")


def write_grid_gzip_sidecar(frame_path: Path, payload: bytes, *, compresslevel: int = GRID_GZIP_COMPRESSLEVEL) -> Path:
    sidecar_path = grid_gzip_sidecar_path(frame_path)
    tmp_path = sidecar_path.with_suffix(f"{sidecar_path.suffix}.tmp")
    compressed = gzip.compress(payload, compresslevel=compresslevel, mtime=0)
    tmp_path.write_bytes(compressed)
    tmp_path.replace(sidecar_path)
    return sidecar_path


def write_grid_brotli_sidecar(frame_path: Path, payload: bytes, *, quality: int = GRID_BROTLI_QUALITY) -> Path:
    sidecar_path = grid_brotli_sidecar_path(frame_path)
    tmp_path = sidecar_path.with_suffix(f"{sidecar_path.suffix}.tmp")
    compressed = brotli.compress(payload, quality=quality)
    tmp_path.write_bytes(compressed)
    tmp_path.replace(sidecar_path)
    return sidecar_path


def ensure_grid_gzip_sidecar(
    frame_path: Path,
    *,
    compresslevel: int = GRID_GZIP_COMPRESSLEVEL,
    force: bool = False,
) -> Path | None:
    if not frame_path.is_file():
        raise FileNotFoundError(f"Missing grid frame artifact: {frame_path}")
    sidecar_path = grid_gzip_sidecar_path(frame_path)
    if sidecar_path.is_file() and not force:
        return sidecar_path
    payload = frame_path.read_bytes()
    return write_grid_gzip_sidecar(frame_path, payload, compresslevel=compresslevel)


def ensure_grid_brotli_sidecar(
    frame_path: Path,
    *,
    quality: int = GRID_BROTLI_QUALITY,
    force: bool = False,
) -> Path | None:
    if not frame_path.is_file():
        raise FileNotFoundError(f"Missing grid frame artifact: {frame_path}")
    sidecar_path = grid_brotli_sidecar_path(frame_path)
    if sidecar_path.is_file() and not force:
        return sidecar_path
    payload = frame_path.read_bytes()
    return write_grid_brotli_sidecar(frame_path, payload, quality=quality)


def _packing_config(model: str, var: str) -> dict[str, Any] | None:
    return _PACKING_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))


def _encode_values(values: np.ndarray, *, scale: float, offset: float, nodata: int, dtype: str) -> np.ndarray:
    resolved_dtype = grid_dtype(dtype)
    encoded_dtype: np.dtype[Any] = np.dtype(np.uint8 if resolved_dtype == GRID_DTYPE_UINT8 else np.uint16)
    encoded = np.full(values.shape, int(nodata), dtype=encoded_dtype)
    valid_mask = np.isfinite(values)
    if not np.any(valid_mask):
        return encoded

    scaled = np.rint((values[valid_mask] - float(offset)) / float(scale))
    clipped = np.clip(scaled, 0, int(nodata) - 1).astype(encoded_dtype, copy=False)
    encoded[valid_mask] = clipped
    return encoded


def write_grid_frame_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    values: np.ndarray,
    level: int = GRID_LEVEL,
    transform: Affine | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    projection: str = GRID_PROJECTION,
) -> dict[str, Any]:
    packing = _packing_config(model, var)
    if packing is None:
        raise ValueError(f"Unsupported grid pack target: {model}/{var}")
    packing_dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    values_array = np.asarray(values, dtype=np.float32)

    # Compute bbox from original (pre-display-prep) dimensions so that
    # upscaling in prepare_grid_display_values does not inflate the extent.
    if bbox is None:
        if transform is None:
            raise ValueError(f"Missing transform/bbox for grid frame: {model}/{var}/fh{int(fh):03d}")
        orig_h, orig_w = values_array.shape[:2]
        left, bottom, right, top = array_bounds(orig_h, orig_w, transform)
        bounds = [float(left), float(bottom), float(right), float(top)]
    else:
        bounds = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]

    display_values, prep_meta = prepare_grid_display_values(model=model, var=var, values=values_array)
    encoded = _encode_values(
        display_values,
        scale=float(packing["scale"]),
        offset=float(packing["offset"]),
        nodata=int(packing["nodata"]),
        dtype=packing_dtype,
    )
    height, width = encoded.shape
    crs_text = str(projection or GRID_PROJECTION)
    if packing_dtype == GRID_DTYPE_UINT8:
        encoded_bytes = encoded.astype(np.uint8, copy=False).tobytes(order="C")
    else:
        encoded_bytes = encoded.astype("<u2", copy=False).tobytes(order="C")

    out_path = grid_frame_path_for_run_root(run_root, var, fh, level=level, dtype=packing_dtype)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(encoded_bytes)
    tmp_path.replace(out_path)
    if GRID_GZIP_SIDECARS_ENABLED:
        write_grid_gzip_sidecar(out_path, encoded_bytes)
    if GRID_BROTLI_SIDECARS_ENABLED:
        write_grid_brotli_sidecar(out_path, encoded_bytes)

    frame_meta = {
        "fh": int(fh),
        "level": int(level),
        "file": out_path.name,
        "width": width,
        "height": height,
        "bbox": bounds,
        "projection": crs_text,
    }
    if prep_meta:
        frame_meta["display_prep"] = prep_meta
    write_json_atomic(grid_frame_meta_path_for_run_root(run_root, var, fh, level=level), frame_meta)
    return frame_meta


def write_grid_frames_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    values: np.ndarray,
    transform: Affine | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    projection: str = GRID_PROJECTION,
) -> list[dict[str, Any]]:
    values_array = np.asarray(values, dtype=np.float32)
    if bbox is None:
        if transform is None:
            raise ValueError(f"Missing transform/bbox for grid frame: {model}/{var}/fh{int(fh):03d}")
        source_height, source_width = values_array.shape[:2]
        left, bottom, right, top = array_bounds(source_height, source_width, transform)
        bounds = [float(left), float(bottom), float(right), float(top)]
    else:
        bounds = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]

    written: list[dict[str, Any]] = []
    for lod_spec in grid_lod_specs(model, var):
        level = int(lod_spec.get("level", GRID_LEVEL))
        scale_factor = max(1, int(lod_spec.get("scale_factor", 1)))
        lod_values = _values_for_lod(values_array, model=model, var=var, scale_factor=scale_factor)
        written.append(
            write_grid_frame_for_run_root(
                run_root=run_root,
                model=model,
                var=var,
                fh=fh,
                values=lod_values,
                level=level,
                bbox=bounds,
                projection=projection,
            )
        )
    return written


def write_grid_frame_from_value_cog_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    value_cog_path: Path,
) -> dict[str, Any]:
    if not value_cog_path.is_file():
        raise FileNotFoundError(f"Missing grid source value COG: {value_cog_path}")
    try:
        with rasterio.open(value_cog_path) as ds:
            frame_entries = write_grid_frames_for_run_root(
                run_root=run_root,
                model=model,
                var=var,
                fh=fh,
                values=ds.read(1).astype(np.float32, copy=False),
                transform=ds.transform,
                projection=ds.crs.to_string() if ds.crs is not None else GRID_PROJECTION,
            )
            return next((entry for entry in frame_entries if int(entry.get("level", GRID_LEVEL)) == GRID_LEVEL), frame_entries[0])
    except RasterioIOError as exc:
        raise FileNotFoundError(f"Unreadable grid source value COG: {value_cog_path}") from exc


def _build_palette_block(model: str, var: str) -> dict[str, Any]:
    color_map_id = variable_color_map_id(model, var)
    palette: dict[str, Any] = {"color_map_id": color_map_id}
    if color_map_id:
        try:
            spec = get_color_map_spec(color_map_id)
        except KeyError:
            spec = {}
        spec_type = str(spec.get("display_palette_kind") or spec.get("type") or "").strip()
        if spec_type:
            palette["kind"] = spec_type
        gamma = spec.get("power_norm_gamma")
        if gamma is not None:
            palette["power_norm_gamma"] = float(gamma)
        transparent_below_min = spec.get("transparent_below_min")
        if isinstance(transparent_below_min, (int, float)) and not isinstance(transparent_below_min, bool):
            palette["transparent_below_min"] = float(transparent_below_min)
        elif transparent_below_min is True and spec_type == "discrete":
            levels = spec.get("levels")
            if isinstance(levels, list) and levels:
                first_level = levels[0]
                if isinstance(first_level, (int, float)) and not isinstance(first_level, bool):
                    palette["transparent_below_min"] = float(first_level)
        transparent_zero = spec.get("transparent_zero")
        if isinstance(transparent_zero, bool):
            palette["transparent_zero"] = transparent_zero
    return palette


def _read_composite_sidecar_metadata(run_root: Path, var: str) -> dict[str, Any]:
    var_dir = Path(run_root) / var
    if not var_dir.is_dir():
        return {}
    for sidecar_path in sorted(var_dir.glob("fh*.json")):
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        composite_layers = sidecar.get("composite_layers")
        if not isinstance(composite_layers, list) or not composite_layers:
            continue
        payload: dict[str, Any] = {"composite_layers": composite_layers}
        composite_mode = sidecar.get("composite_mode")
        if isinstance(composite_mode, str) and composite_mode.strip():
            payload["composite_mode"] = composite_mode.strip()
        display_name = sidecar.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            payload["display_name"] = display_name.strip()
        return payload
    return {}


def _build_manifest_for_var_from_run_root(
    *,
    run_root: Path,
    model: str,
    run: str,
    var: str,
) -> bool:
    packing = _packing_config(model, var)
    if packing is None:
        return False
    packing_dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
    lod_specs_by_level = {
        int(spec.get("level", GRID_LEVEL)): spec
        for spec in grid_lod_specs(model, var)
    }

    var_dir = Path(run_root) / var
    if not var_dir.is_dir():
        return False

    grid_dir_path = resolved_grid_dir_for_run_root(run_root, var)
    if not grid_dir_path.is_dir():
        return False

    projection = GRID_PROJECTION
    units = str(packing.get("units") or "")
    display_prep: dict[str, Any] | None = None
    valid_time_by_fh: dict[int, str] = {}
    manifest_display_name: str | None = None
    manifest_legend: dict[str, Any] | None = None
    manifest_contours: dict[str, Any] | None = None
    lod_entries: dict[int, dict[str, Any]] = {}

    for sidecar_path in sorted(var_dir.glob("fh*.json")):
        fh_token = sidecar_path.stem
        if not fh_token.startswith("fh"):
            continue
        try:
            fh = int(fh_token.removeprefix("fh"))
        except ValueError:
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not units:
            units = str(sidecar.get("units") or units or "")
        if manifest_display_name is None:
            display_name = sidecar.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                manifest_display_name = display_name.strip()
        if manifest_legend is None:
            legend = sidecar.get("legend")
            if isinstance(legend, dict):
                manifest_legend = dict(legend)
        if manifest_contours is None:
            contours = sidecar.get("contours")
            if isinstance(contours, dict):
                manifest_contours = dict(contours)
        valid_time = sidecar.get("valid_time")
        if isinstance(valid_time, str) and valid_time.strip():
            valid_time_by_fh[fh] = valid_time.strip()

    for frame_meta_path in sorted(grid_dir_path.glob("fh*.l*.meta.json")):
        try:
            frame_meta = json.loads(frame_meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        raw_fh = frame_meta.get("fh")
        raw_level = frame_meta.get("level")
        fh = int(raw_fh) if raw_fh is not None else -1
        level = int(raw_level) if raw_level is not None else GRID_LEVEL
        filename = str(frame_meta.get("file") or "").strip()
        frame_width = int(frame_meta.get("width") or 0)
        frame_height = int(frame_meta.get("height") or 0)
        frame_bbox = frame_meta.get("bbox")
        frame_projection = str(frame_meta.get("projection") or GRID_PROJECTION)
        if fh < 0 or not filename or frame_width <= 0 or frame_height <= 0:
            continue

        frame_path = grid_dir_path / filename
        if not frame_path.is_file():
            continue

        expected_size_bytes = expected_grid_frame_size_bytes(
            width=frame_width,
            height=frame_height,
            dtype=packing_dtype,
        )
        actual_size_bytes = frame_path.stat().st_size
        if actual_size_bytes != expected_size_bytes:
            logger.warning(
                "Skipping invalid grid frame in manifest: model=%s run=%s var=%s fh=%s level=%s actual_bytes=%s expected_bytes=%s",
                model,
                run,
                var,
                fh,
                level,
                actual_size_bytes,
                expected_size_bytes,
            )
            continue

        next_level = lod_entries.setdefault(
            level,
            {
                "level": level,
                "width": frame_width,
                "height": frame_height,
                "bbox": [float(frame_bbox[0]), float(frame_bbox[1]), float(frame_bbox[2]), float(frame_bbox[3])]
                if isinstance(frame_bbox, list) and len(frame_bbox) == 4
                else None,
                "projection": frame_projection,
                "frames": [],
            },
        )
        next_level["frames"].append(
            {
                "fh": fh,
                "file": filename,
                **({"valid_time": valid_time_by_fh[fh]} if fh in valid_time_by_fh else {}),
            }
        )
        if display_prep is None and level == GRID_LEVEL and isinstance(frame_meta.get("display_prep"), dict):
            display_prep = dict(frame_meta["display_prep"])

    if not lod_entries:
        return False

    sorted_levels = sorted(lod_entries)
    base_level = GRID_LEVEL if GRID_LEVEL in lod_entries else sorted_levels[0]
    base_lod = lod_entries[base_level]
    base_bbox = base_lod.get("bbox")
    if not isinstance(base_bbox, list) or len(base_bbox) != 4:
        return False

    manifest_lods: list[dict[str, Any]] = []
    for level in sorted_levels:
        lod_entry = lod_entries[level]
        lod_frames = sorted(lod_entry["frames"], key=lambda item: int(item["fh"]))
        next_lod = {
            "level": int(level),
            "width": int(lod_entry["width"]),
            "height": int(lod_entry["height"]),
            "frames": lod_frames,
        }
        lod_spec = lod_specs_by_level.get(level, {})
        if isinstance(lod_spec.get("min_zoom"), (int, float)):
            next_lod["min_zoom"] = float(lod_spec["min_zoom"])
        if isinstance(lod_spec.get("max_zoom"), (int, float)):
            next_lod["max_zoom"] = float(lod_spec["max_zoom"])
        manifest_lods.append(next_lod)

    manifest = {
        "manifest_version": GRID_MANIFEST_VERSION,
        "subtype": GRID_SUBTYPE,
        "model": model,
        "run": run,
        "var": var,
        "projection": str(base_lod.get("projection") or GRID_PROJECTION),
        "bbox": base_bbox,
        "grid": {
            "width": int(base_lod["width"]),
            "height": int(base_lod["height"]),
            "dtype": packing_dtype,
            "endianness": GRID_ENDIANNESS,
            "scale": float(packing["scale"]),
            "offset": float(packing["offset"]),
            "nodata": int(packing["nodata"]),
            "units": units,
        },
        "palette": _build_palette_block(model, var),
        "lods": manifest_lods,
    }
    if manifest_display_name:
        manifest["display_name"] = manifest_display_name
    if manifest_legend:
        manifest["legend"] = manifest_legend
    if manifest_contours:
        manifest["contours"] = manifest_contours
    composite_meta = _read_composite_sidecar_metadata(run_root, var)
    if composite_meta:
        manifest.update(composite_meta)
    if display_prep:
        manifest["display_prep"] = display_prep
    write_json_atomic(grid_manifest_path_for_run_root(run_root, var), manifest)
    return True


def build_grid_manifests_for_run_root(
    *,
    run_root: Path,
    model: str,
    run: str,
    variables: tuple[str, ...] | None = None,
) -> int:
    run_root_path = Path(run_root)
    if not run_root_path.is_dir():
        return 0

    requested_vars = {str(item).strip().lower() for item in (variables or ()) if str(item).strip()}
    manifest_ok = 0
    for var_dir in sorted(path for path in run_root_path.iterdir() if path.is_dir()):
        var = var_dir.name.strip().lower()
        if requested_vars and var not in requested_vars:
            continue
        if not grid_supported(model, var):
            continue
        try:
            if _build_manifest_for_var_from_run_root(run_root=run_root_path, model=model, run=run, var=var):
                manifest_ok += 1
        except Exception:
            logger.exception("grid manifest build failed: model=%s run=%s var=%s", model, run, var)
    return manifest_ok


def build_grid_for_run(
    *,
    data_root: Path,
    model: str,
    run: str,
    workers: int,
    variables: tuple[str, ...] | None = None,
) -> tuple[int, int, int]:
    published_run = data_root / "published" / model / run
    if not published_run.is_dir():
        return 0, 0, 0

    requested_vars = {str(item).strip().lower() for item in (variables or ()) if str(item).strip()}
    jobs: list[tuple[str, int, Path]] = []
    manifest_vars: set[str] = set()

    for var_dir in sorted(path for path in published_run.iterdir() if path.is_dir()):
        var = var_dir.name.strip().lower()
        if requested_vars and var not in requested_vars:
            continue
        if not grid_supported(model, var):
            continue
        manifest_vars.add(var)
        for value_cog_path in sorted(var_dir.glob("fh*.val.cog.tif")):
            if not value_cog_path.is_file():
                logger.warning(
                    "Skipping missing grid source value COG: model=%s run=%s var=%s path=%s",
                    model,
                    run,
                    var,
                    value_cog_path,
                )
                continue
            fh_token = value_cog_path.name.split(".")[0]
            try:
                fh = int(fh_token.removeprefix("fh"))
            except ValueError:
                continue
            sidecar_path = var_dir / f"{fh_token}.json"
            if not sidecar_path.is_file():
                continue
            jobs.append((var, fh, value_cog_path))

    if not jobs:
        return 0, 0, 0

    ok = 0
    fail = 0
    max_workers = max(1, int(workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                write_grid_frame_from_value_cog_for_run_root,
                run_root=published_run,
                model=model,
                var=var,
                fh=fh,
                value_cog_path=value_cog_path,
            )
            for var, fh, value_cog_path in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("grid frame build failed for model=%s run=%s", model, run)
                fail += 1
                continue
            ok += 1

    manifest_ok = build_grid_manifests_for_run_root(
        run_root=published_run,
        model=model,
        run=run,
        variables=tuple(sorted(manifest_vars)),
    )

    return ok, fail, manifest_ok
