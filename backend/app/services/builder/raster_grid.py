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
from dataclasses import dataclass
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
    # NOTE: there is deliberately no ``global`` entry. The global domain is a
    # native-geographic grid (see ``GLOBAL_DOMAIN_4326_CONTRACT.md``); a
    # mercator global grid is no longer producible.
}

# WGS84 bounding boxes (for reference / coordinate transforms)
REGION_BBOX_4326: dict[str, tuple[float, float, float, float]] = {
    "conus": (-134.0, 24.0, -60.0, 55.0),
    "na": (-178.0, 5.0, -25.0, 82.0),
    "pnw": (-126.0, 41.5, -116.0, 49.5),
    # Cell-edge bbox of the native 0.25° global grid: point-registered at
    # 0.25° multiples, so the edges overhang the ±180/±90 centres by half a
    # cell. Data coverage is the full globe, poles included.
    "global": (-180.125, -90.125, 179.875, 90.125),
}

# ---------------------------------------------------------------------------
# Grid CRS
# ---------------------------------------------------------------------------

#: Legacy default: every (model, region) that does not declare a native
#: geographic grid resolution is an EPSG:3857 metre grid.
LEGACY_GRID_CRS = "EPSG:3857"

#: Grids declared through ``ModelCapabilities.grid_native_degrees_by_region``
#: are stored in geographic coordinates instead.
NATIVE_GRID_CRS = "EPSG:4326"

#: At or below this, a "metre" resolution is really a *degree* resolution handed
#: to the wrong helper (native-geographic grids are 0.25°–1°).
#: ``compute_transform_and_shape`` refuses those. The bound is deliberately
#: tight rather than "anything under a kilometre": several tests build honest
#: metre grids at synthetic resolutions as small as 10 m, and a real metre grid
#: of ≤ 1 m is not producible here.
_MIN_METRE_GRID_RESOLUTION = 1.0

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
    """Return (bbox, resolution) for a model/region pair.

    For legacy regions that is ``(bbox_3857, grid_meters)``. For regions the
    model declares through ``grid_native_degrees_by_region`` it is the
    EPSG:4326 cell-edge bbox and the resolution in *degrees* — pair it with
    :func:`get_grid_crs`, or better, call :func:`get_target_grid`, which
    resolves CRS/transform/shape together. Do NOT feed a native-geographic
    pair to :func:`compute_transform_and_shape`: that function implements the
    metre-grid snap-to-multiple rule and would produce the wrong shape.

    Raises KeyError if the combination is not defined.
    """
    model_key = str(model).strip().lower()
    region_key = str(region).strip().lower()

    native_degrees = get_native_grid_degrees(model_key, region_key)
    if native_degrees is not None:
        return native_geographic_bbox(native_degrees), native_degrees

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


def _capability_grid_map(model: str, attribute: str) -> dict[str, Any] | None:
    # Relative import: the deployed API imports this package as
    # ``backend.app`` (uvicorn backend.app.main:app), where a
    # ``from app.models...`` absolute import fails and the except below
    # silently disabled every capability grid map. The legacy metre path had
    # static fallbacks that masked this; the native-geographic map does not.
    try:
        from ...models.registry import MODEL_REGISTRY
    except Exception:
        return None
    plugin = MODEL_REGISTRY.get(model)
    if plugin is None:
        return None
    capabilities = getattr(plugin, "capabilities", None)
    if capabilities is None:
        return None
    grid_map = getattr(capabilities, attribute, None)
    if not isinstance(grid_map, dict):
        return None
    return grid_map


def _grid_meters_from_capabilities(model: str, region: str) -> float | None:
    grid_map = _capability_grid_map(model, "grid_meters_by_region")
    if grid_map is None:
        return None
    value = grid_map.get(region)
    if value is None:
        return None
    return float(value)


def get_native_grid_degrees(model: str, region: str) -> float | None:
    """Resolution in degrees when (model, region) declares a native 4326 grid.

    ``None`` means the pair uses the legacy EPSG:3857 metre grid. Presence of
    a value is the *only* signal that a grid follows the native-geographic
    contract (``docs/GLOBAL_DOMAIN_4326_CONTRACT.md`` §4).
    """
    grid_map = _capability_grid_map(str(model).strip().lower(), "grid_native_degrees_by_region")
    if grid_map is None:
        return None
    value = grid_map.get(str(region).strip().lower())
    if value is None:
        return None
    return float(value)


def get_grid_crs(model: str, region: str) -> str:
    """The CRS a model/region's artifacts are stored in."""
    if get_native_grid_degrees(model, region) is not None:
        return NATIVE_GRID_CRS
    return LEGACY_GRID_CRS


def native_geographic_bbox(degrees: float) -> tuple[float, float, float, float]:
    """Cell-edge bbox of the point-registered global grid at ``degrees``.

    Column centres run −180 … 180−d, row centres +90 … −90 (both poles are
    literal rows), so the edges overhang by half a cell.
    """
    res = float(degrees)
    half = res / 2.0
    width = int(round(360.0 / res))
    west = -180.0 - half
    north = 90.0 + half
    return (west, -90.0 - half, west + width * res, north)


def compute_native_transform_and_shape(
    bbox_4326: tuple[float, float, float, float],
    grid_degrees: float,
) -> tuple[rasterio.transform.Affine, int, int]:
    """Affine/shape for a native-geographic grid.

    Unlike :func:`compute_transform_and_shape` there is no snapping: the bbox
    is already the exact cell-edge extent of the point-registered grid.
    """
    xmin, ymin, xmax, ymax = bbox_4326
    res = float(grid_degrees)
    width = round((xmax - xmin) / res)
    height = round((ymax - ymin) / res)
    return from_origin(xmin, ymax, res, res), height, width


@dataclass(frozen=True)
class TargetGrid:
    """The full target-grid description for a (model, region) pair."""

    crs: str
    bbox: tuple[float, float, float, float]
    resolution: float
    transform: rasterio.transform.Affine
    height: int
    width: int

    @property
    def is_native_geographic(self) -> bool:
        return self.crs == NATIVE_GRID_CRS


def get_target_grid(model: str, region: str) -> TargetGrid:
    """Resolve CRS + bbox + transform + shape for a model/region pair.

    The single entry point that is correct for both legacy metre grids and
    native-geographic grids.
    """
    bbox, resolution = get_grid_params(model, region)
    crs = get_grid_crs(model, region)
    if crs == NATIVE_GRID_CRS:
        transform, height, width = compute_native_transform_and_shape(bbox, resolution)
    else:
        transform, height, width = compute_transform_and_shape(bbox, resolution)
    return TargetGrid(
        crs=crs,
        bbox=bbox,
        resolution=float(resolution),
        transform=transform,
        height=int(height),
        width=int(width),
    )


def compute_transform_and_shape(
    bbox_3857: tuple[float, float, float, float],
    grid_meters: float,
) -> tuple[rasterio.transform.Affine, int, int]:
    """Compute the affine transform and pixel dimensions for a target grid.

    Uses target-aligned pixels (equivalent to gdalwarp -tap): the grid origin
    is snapped to a multiple of grid_meters, guaranteeing that all artifacts
    for the same model/region are pixel-aligned.

    Returns (transform, height, width).

    Raises ValueError for a degree-scale resolution: that is a native-geographic
    grid pair (see :func:`get_grid_params`) being fed to the metre-grid snap
    rule, which would silently produce a wrong, half-cell-shifted grid.
    """
    xmin, ymin, xmax, ymax = bbox_3857
    res = float(grid_meters)
    if res <= _MIN_METRE_GRID_RESOLUTION:
        raise ValueError(
            f"compute_transform_and_shape() expects an EPSG:3857 metre resolution, "
            f"got {res!r} — that looks like a native-geographic (degrees) grid. "
            f"Use get_target_grid(model, region), which resolves CRS, transform "
            f"and shape correctly for both grid families."
        )

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


def _aligned_source_indices(
    *,
    src_origin: float,
    src_res: float,
    src_count: int,
    dst_origin: float,
    dst_res: float,
    dst_count: int,
    wrap_span: float | None = None,
) -> np.ndarray | None:
    """Source indices for each destination index, or ``None`` if not aligned.

    Both axes are described by (origin edge, signed resolution, count). The
    destination cell centres must coincide with source cell centres to within
    a millionth of a cell; ``wrap_span`` (e.g. 360°) allows the source to be
    addressed cyclically, which is what turns a 0–360 longitude axis into a
    −180…180 one by pure index arithmetic.
    """
    if src_count <= 0 or dst_count <= 0 or src_res == 0.0 or dst_res == 0.0:
        return None
    dst_centers = dst_origin + dst_res * (np.arange(dst_count, dtype=np.float64) + 0.5)
    src_float = (dst_centers - src_origin) / src_res - 0.5
    if wrap_span is not None:
        cycle = abs(wrap_span / src_res)
        if abs(cycle - src_count) > 1.0e-6:
            return None
        src_float = np.mod(src_float, float(src_count))
    src_index = np.rint(src_float)
    if not np.all(np.abs(src_float - src_index) <= 1.0e-6):
        return None
    src_index = src_index.astype(np.int64)
    if wrap_span is not None:
        src_index = np.mod(src_index, src_count)
    if src_index.min() < 0 or src_index.max() >= src_count:
        return None
    return src_index


def _index_roll_to_target_grid(
    data: np.ndarray,
    src_crs: Any,
    src_transform: rasterio.transform.Affine,
    grid: TargetGrid,
) -> np.ndarray | None:
    """Exact, interpolation-free resample by index arithmetic.

    Applies when the source is already on the target CRS at the target
    resolution and grid-aligned — the GFS 0.25° 0–360 → native global case,
    where the whole "warp" is a longitude roll (contract §5). Returns ``None``
    when the source does not align, so the caller can fall back to a real
    reprojection.
    """
    if not grid.is_native_geographic:
        return None
    try:
        normalized_src_crs = rasterio.crs.CRS.from_user_input(src_crs)
    except Exception:
        return None
    if normalized_src_crs != rasterio.crs.CRS.from_user_input(grid.crs):
        return None
    if abs(float(src_transform.b)) > 1.0e-12 or abs(float(src_transform.d)) > 1.0e-12:
        return None
    if abs(abs(float(src_transform.a)) - grid.resolution) > 1.0e-9:
        return None
    if abs(abs(float(src_transform.e)) - grid.resolution) > 1.0e-9:
        return None

    src_h, src_w = data.shape[-2:]
    rows = _aligned_source_indices(
        src_origin=float(src_transform.f),
        src_res=float(src_transform.e),
        src_count=int(src_h),
        dst_origin=float(grid.transform.f),
        dst_res=float(grid.transform.e),
        dst_count=grid.height,
    )
    if rows is None:
        return None
    cols = _aligned_source_indices(
        src_origin=float(src_transform.c),
        src_res=float(src_transform.a),
        src_count=int(src_w),
        dst_origin=float(grid.transform.c),
        dst_res=float(grid.transform.a),
        dst_count=grid.width,
        wrap_span=360.0,
    )
    if cols is None:
        return None

    if data.ndim == 2:
        return data[np.ix_(rows, cols)]
    return data[..., rows, :][..., :, cols]


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
    """Reproject a 2-D array to the target grid for a model/region.

    Equivalent to:
        gdalwarp -t_srs {grid CRS} -te ... -tr ... -tap -r {resampling}

    The target CRS is the model/region's declared grid CRS (EPSG:3857 for
    every legacy region; EPSG:4326 for regions with a native-geographic
    grid). When the source already sits on an aligned native grid the warp
    degenerates to index arithmetic and no resampling runs at all.

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
    grid = get_target_grid(model, region)
    dst_transform, dst_h, dst_w = grid.transform, grid.height, grid.width
    dst_crs = rasterio.crs.CRS.from_user_input(grid.crs)

    resamp = Resampling[resampling]
    resolved_dtype = np.dtype(working_dtype)
    if resolved_dtype.kind != "f":
        raise ValueError(f"working_dtype must be a floating dtype, got {resolved_dtype}")

    # Native-geographic grids whose source is already aligned are a pure index
    # roll: exact values, no interpolation (contract §5).
    rolled = _index_roll_to_target_grid(data, src_crs, src_transform, grid)
    if rolled is not None:
        rolled = rolled.astype(np.float32, copy=True)
        if src_nodata is not None:
            rolled[rolled == np.float32(src_nodata)] = np.float32(dst_nodata)
        return rolled, dst_transform

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
