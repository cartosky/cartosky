"""Value COG writer: numpy arrays → Cloud Optimized GeoTIFF files.

Produces the legacy value artifact:
  - Value COG: 1-band float32, EPSG:3857, 512×512 tiles, internal overviews

Overviews are built with gdaladdo (subprocess), nearest resampling. The final
COG is produced with gdal_translate -of COG.

Target-grid geometry, reprojection, and GDAL CLI resolution live in
:mod:`app.services.builder.raster_grid` — this module only knows how to turn
an already-warped array into a COG.

Value COGs are written only for models opted onto the COG substrate via
``CARTOSKY_COG_SAMPLING_MODELS``; every model is binary-only by default, so
this path is dormant in production.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import rasterio
import rasterio.transform

from app.services.builder.raster_grid import (
    _gdal,
    compute_transform_and_shape,
    get_grid_params,
)

logger = logging.getLogger(__name__)

# Internal tile size for all COGs
COG_BLOCKSIZE = 512

# Compression for all COGs
COG_COMPRESS = "deflate"


def _overview_levels(height: int, width: int) -> list[int]:
    """Compute overview levels (powers of 2), including small grids.

    Contract requires internal overviews. For compact domains (e.g. GFS PNW),
    still emit at least a 2x overview when possible.
    """
    max_dim = max(height, width)
    if max_dim < 2:
        return []

    levels = []
    factor = 2
    while max_dim // factor >= 128:
        levels.append(factor)
        factor *= 2

    # Always have at least one overview level for any grid that can be downsampled.
    if not levels:
        levels.append(2)
    return levels


# ---------------------------------------------------------------------------
# Value COG writer
# ---------------------------------------------------------------------------


def write_value_cog(
    values: np.ndarray,
    output_path: Path | str,
    *,
    model: str,
    region: str,
    nodata: float = float("nan"),
    downsample_factor: int = 1,
) -> Path:
    """Write a single-band float32 array as a Cloud Optimized GeoTIFF.

    Parameters
    ----------
    values : np.ndarray
        Shape (H, W), dtype float32. NaN = nodata.
    output_path : Path or str
        Destination file path.
    model, region : str
        Used to look up grid parameters (bbox + resolution).
    nodata : float
        Nodata value. Defaults to NaN.
    downsample_factor : int
        Deprecated compatibility argument. Value COG base resolution now always
        matches the model/region target grid.

    Returns
    -------
    Path to the written COG file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if values.ndim != 2:
        raise ValueError(f"values must be shape (H, W), got {values.shape}")
    if downsample_factor < 1:
        raise ValueError(f"downsample_factor must be >= 1, got {downsample_factor}")

    values_f32 = values.astype(np.float32, copy=False)
    data_h, data_w = values_f32.shape

    bbox, grid_m = get_grid_params(model, region)
    base_transform, expected_h, expected_w = compute_transform_and_shape(bbox, grid_m)

    if data_h != expected_h or data_w != expected_w:
        raise ValueError(
            f"Value array shape ({data_h}, {data_w}) does not match expected "
            f"grid ({expected_h}, {expected_w}) for {model}/{region} at {grid_m}m"
        )

    if downsample_factor > 1:
        logger.warning(
            "write_value_cog downsample_factor=%d ignored; writing full-resolution base grid for %s/%s",
            downsample_factor,
            model,
            region,
        )

    transform = base_transform
    output_grid_m = grid_m

    # Expand to (1, H, W) for rasterio
    data_3d = values_f32[np.newaxis, :, :]
    levels = _overview_levels(data_h, data_w)

    with tempfile.TemporaryDirectory(dir=output_path.parent) as tmp_dir:
        tmp_gtiff = Path(tmp_dir) / "base.tif"

        # Step 1: Write base GTiff (tiled, no overviews)
        _write_base_gtiff(
            data=data_3d,
            path=tmp_gtiff,
            transform=transform,
            count=1,
            dtype="float32",
            nodata=nodata,
        )

        # Step 2: Build overviews (nearest for value grids)
        if levels:
            level_strs = [str(l) for l in levels]
            _run_gdal([
                _gdal("gdaladdo"), "-r", "nearest",
                "--config", "GDAL_TIFF_OVR_BLOCKSIZE", str(COG_BLOCKSIZE),
                str(tmp_gtiff), *level_strs,
            ])

        # Step 3: Convert to COG
        _gtiff_to_cog(tmp_gtiff, output_path)

    logger.info(
        "Wrote value COG: %s (%dx%d, %d overviews, grid=%.1fm)",
        output_path, data_w, data_h, len(levels), output_grid_m,
    )
    return output_path


# ---------------------------------------------------------------------------
# Internal helpers: GDAL subprocess calls
# ---------------------------------------------------------------------------


def _run_gdal(cmd: list[str]) -> None:
    """Run a GDAL CLI command, raising on failure."""
    logger.debug("GDAL: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"GDAL command failed (exit {result.returncode}):\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )


def _write_base_gtiff(
    data: np.ndarray,
    path: Path,
    *,
    transform: rasterio.transform.Affine,
    count: int,
    dtype: str,
    nodata: float | None,
) -> None:
    """Write a tiled GTiff with no overviews (base image only).

    This is step 1 of the COG pipeline. Overviews are added
    separately via gdaladdo for per-band resampling control.
    """
    _, height, width = data.shape
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": count,
        "dtype": dtype,
        "crs": "EPSG:3857",
        "transform": transform,
        "tiled": True,
        "blockxsize": COG_BLOCKSIZE,
        "blockysize": COG_BLOCKSIZE,
        "compress": COG_COMPRESS,
    }
    if nodata is not None:
        profile["nodata"] = nodata

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)


def _gtiff_to_cog(src_path: Path, dst_path: Path) -> None:
    """Convert a GTiff or VRT (with overviews already built) to a COG.

    Uses `gdal_translate -of COG` which reorders IFDs for
    cloud-optimized layout (overview IFDs before main image).

    Overviews are expected to already exist in the source (or
    in source files referenced by a VRT). The COG driver copies
    them via COPY_SRC_OVERVIEWS.
    """
    _run_gdal([
        _gdal("gdal_translate"),
        "-of", "COG",
        "-co", f"BLOCKSIZE={COG_BLOCKSIZE}",
        "-co", f"COMPRESS={COG_COMPRESS.upper()}",
        "-co", "COPY_SRC_OVERVIEWS=YES",
        str(src_path),
        str(dst_path),
    ])
