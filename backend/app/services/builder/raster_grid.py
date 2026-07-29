"""Target-grid geometry, reprojection, and GDAL CLI tool resolution.

This module owns the raster primitives shared across CartoSky's raster
producers and consumers:

  - Region bounding boxes and per-model/region grid resolutions
  - ``get_grid_params`` / ``compute_transform_and_shape``: the target-aligned
    EPSG:3857 pixel grid that every artifact for a given model/region shares
  - ``warp_to_target_grid``: reprojection onto that grid (pure rasterio, no
    GDAL CLI dependency)
  - ``_gdal`` / ``_find_gdal_tool``: lazy GDAL CLI discovery

Consumers include the build pipeline (frame warping and contour generation),
the observed-product publishers (MRMS, NDFD, WPC, RTMA-RU, GOES), ensemble
member processing, derived-variable computation, and climatology asset
builds.

Grid constants are defined here — the rest of the builder imports them from
this module. Nothing here is tied to any particular output artifact format.
"""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import rasterio.crs
import rasterio.transform
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Region bounding boxes (EPSG:3857) — authoritative, from ROADMAP_V3
# Format: (west, south, east, north) = (xmin, ymin, xmax, ymax)
# ---------------------------------------------------------------------------

REGION_BBOX_3857: dict[str, tuple[float, float, float, float]] = {
    "conus": (-14916811.77, 2753408.11, -6679169.45, 7361866.11),
    "na": (-19814869.36, 557305.26, -2782987.27, 16967796.94),
    "pnw": (-14026255.80, 5096324.37, -12913060.93, 6378137.00),
    # Full Web-Mercator extent. The ±85.05113° pole clip is inherent to
    # EPSG:3857 (the projection is undefined at the poles), so the global
    # domain is the whole valid Mercator square.
    "global": (
        -20037508.342789244,
        -20037508.342789244,
        20037508.342789244,
        20037508.342789244,
    ),
}

# WGS84 bounding boxes (for reference / coordinate transforms)
REGION_BBOX_4326: dict[str, tuple[float, float, float, float]] = {
    "conus": (-134.0, 24.0, -60.0, 55.0),
    "na": (-178.0, 5.0, -25.0, 82.0),
    "pnw": (-126.0, 41.5, -116.0, 49.5),
    "global": (-180.0, -85.05112877980659, 180.0, 85.05112877980659),
}

# ---------------------------------------------------------------------------
# Target grid resolution (meters) per model/region
# All variables for a given model/region share an identical pixel grid.
# ---------------------------------------------------------------------------

# Legacy fallback only. Authoritative grid ownership is model capabilities
# (`ModelCapabilities.grid_meters_by_region`).
TARGET_GRID_METERS: dict[str, dict[str, float]] = {
    "hrrr": {
        "conus": 3_000.0,
        "pnw": 3_000.0,
    },
    "gefs": {
        "conus": 25_000.0,
        "na": 25_000.0,
    },
    "gfs": {
        "conus": 25_000.0,
        "na": 25_000.0,
        "pnw": 25_000.0,
    },
    "nam": {
        "conus": 5_000.0,
        "pnw": 5_000.0,
    },
    "nbm": {
        "conus": 13_000.0,
        "pnw": 13_000.0,
    },
    "aigfs": {
        "conus": 25_000.0,
        "na": 25_000.0,
    },
    "aifs": {
        "conus": 9_000.0,
        "na": 9_000.0,
    },
    "ecmwf": {
        "conus": 9_000.0,
        "na": 9_000.0,
    },
    "eps": {
        "conus": 18_000.0,
        "na": 18_000.0,
    },
}


# ---------------------------------------------------------------------------
# GDAL CLI discovery
# ---------------------------------------------------------------------------

def _find_gdal_tool(name: str) -> str:
    """Locate a GDAL CLI tool, returning its absolute path.

    Checks PATH first, then common Homebrew / system locations.
    Raises RuntimeError if not found.
    """
    path = shutil.which(name)
    if path:
        return path
    # Fallback: common install locations
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        candidate = f"{prefix}/{name}"
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        f"GDAL tool '{name}' not found. Install GDAL CLI tools "
        f"(e.g. `brew install gdal` on macOS, `apt install gdal-bin` on Linux)."
    )


# ---------------------------------------------------------------------------
# Lazy GDAL tool resolution — avoids crashing on import in environments
# that don't have GDAL CLI installed (CI, unit tests, minimal containers).
# Resolved on first use.
# ---------------------------------------------------------------------------

_gdal_tools: dict[str, str] = {}


def _gdal(name: str) -> str:
    """Return the absolute path for a GDAL CLI tool, resolving lazily."""
    if name not in _gdal_tools:
        _gdal_tools[name] = _find_gdal_tool(name)
        logger.info("Resolved GDAL tool: %s → %s", name, _gdal_tools[name])
    return _gdal_tools[name]


def get_grid_params(
    model: str,
    region: str,
) -> tuple[tuple[float, float, float, float], float]:
    """Return (bbox_3857, grid_meters) for a model/region pair.

    Raises KeyError if the combination is not defined.
    """
    model_key = str(model).strip().lower()
    region_key = str(region).strip().lower()

    bbox = REGION_BBOX_3857.get(region_key)
    if bbox is None:
        raise KeyError(f"Unknown region: {region!r}")
    grid_m = _grid_meters_from_capabilities(model_key, region_key)
    if grid_m is None:
        model_grids = TARGET_GRID_METERS.get(model_key)
        if model_grids is None:
            raise KeyError(f"Unknown model: {model!r}")
        grid_m = model_grids.get(region_key)
    if grid_m is None:
        raise KeyError(f"No grid resolution defined for {model!r}/{region!r}")
    return bbox, grid_m


def _grid_meters_from_capabilities(model: str, region: str) -> float | None:
    try:
        from app.models.registry import MODEL_REGISTRY
    except Exception:
        return None
    plugin = MODEL_REGISTRY.get(model)
    if plugin is None:
        return None
    capabilities = getattr(plugin, "capabilities", None)
    if capabilities is None:
        return None
    grid_map = getattr(capabilities, "grid_meters_by_region", None)
    if not isinstance(grid_map, dict):
        return None
    value = grid_map.get(region)
    if value is None:
        return None
    return float(value)


def compute_transform_and_shape(
    bbox_3857: tuple[float, float, float, float],
    grid_meters: float,
) -> tuple[rasterio.transform.Affine, int, int]:
    """Compute the affine transform and pixel dimensions for a target grid.

    Uses target-aligned pixels (equivalent to gdalwarp -tap): the grid origin
    is snapped to a multiple of grid_meters, guaranteeing that all artifacts
    for the same model/region are pixel-aligned.

    Returns (transform, height, width).
    """
    xmin, ymin, xmax, ymax = bbox_3857
    res = grid_meters

    # Snap to target-aligned pixels (equivalent to -tap)
    aligned_xmin = math.floor(xmin / res) * res
    aligned_ymax = math.ceil(ymax / res) * res
    aligned_xmax = math.ceil(xmax / res) * res
    aligned_ymin = math.floor(ymin / res) * res

    width = round((aligned_xmax - aligned_xmin) / res)
    height = round((aligned_ymax - aligned_ymin) / res)

    # from_origin expects (west, north, xres, yres)
    transform = from_origin(aligned_xmin, aligned_ymax, res, res)

    return transform, height, width


# ---------------------------------------------------------------------------
# Warp: reproject source raster data to the target model/region grid
# ---------------------------------------------------------------------------


def warp_to_target_grid(
    data: np.ndarray,
    src_crs: Any,
    src_transform: rasterio.transform.Affine,
    *,
    model: str,
    region: str,
    resampling: str = "bilinear",
    src_nodata: float | None = None,
    dst_nodata: float = float("nan"),
    working_dtype: Any = np.float64,
) -> tuple[np.ndarray, rasterio.transform.Affine]:
    """Reproject a 2-D array to the target EPSG:3857 grid for a model/region.

    Equivalent to:
        gdalwarp -t_srs EPSG:3857 -te ... -tr ... -tap -r {resampling}

    Parameters
    ----------
    data : np.ndarray
        2-D float array in the source CRS.
    src_crs : rasterio CRS or string
        CRS of the input data.
    src_transform : Affine
        Affine transform of the input data.
    model, region : str
        Target model/region for grid parameters.
    resampling : str
        Resampling method name (e.g. "bilinear", "nearest").
    src_nodata, dst_nodata : float or None
        Nodata values for source and destination.
    working_dtype : numpy dtype, optional
        Floating dtype used for the in-memory reprojection source and destination.
        Defaults to float64 for legacy callers; memory-sensitive observed products
        can opt into float32.

    Returns
    -------
    (warped_data, dst_transform) where warped_data has the target grid shape.
    """
    bbox, grid_m = get_grid_params(model, region)
    dst_transform, dst_h, dst_w = compute_transform_and_shape(bbox, grid_m)
    dst_crs = rasterio.crs.CRS.from_epsg(3857)

    resamp = Resampling[resampling]
    resolved_dtype = np.dtype(working_dtype)
    if resolved_dtype.kind != "f":
        raise ValueError(f"working_dtype must be a floating dtype, got {resolved_dtype}")

    # Expand to 3-D for reproject
    src_3d = data[np.newaxis, :, :] if data.ndim == 2 else data
    band_count = src_3d.shape[0]
    src_work = src_3d.astype(resolved_dtype, copy=False)
    dst_3d = np.full((band_count, dst_h, dst_w), dst_nodata, dtype=resolved_dtype)

    reproject(
        source=src_work,
        destination=dst_3d,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=resamp,
        src_nodata=src_nodata,
        dst_nodata=dst_nodata,
    )

    # Squeeze back to 2-D if input was 2-D
    if data.ndim == 2:
        dst_3d = dst_3d[0]

    return dst_3d.astype(np.float32, copy=False), dst_transform
