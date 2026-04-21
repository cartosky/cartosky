from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio.transform

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import stage_era5_climatology_source as stage_script


def test_output_path_uses_stage_contract(tmp_path: Path) -> None:
    spec = stage_script.FIELD_SPECS["tmp2m"]
    path = stage_script._output_path(
        tmp_path,
        spec=spec,
        valid_time=datetime(1991, 1, 1, 6, tzinfo=timezone.utc),
    )
    expected = tmp_path / "era5" / "single-levels" / "tmp2m" / "1991" / "1991010106_tmp2m.tif"
    assert path == expected


def test_normalize_longitudes_wraps_and_sorts() -> None:
    values = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
    longitudes = np.array([0.0, 180.0, 270.0], dtype=np.float64)
    normalized_values, normalized_lons = stage_script._normalize_longitudes(values, longitudes)
    assert np.allclose(normalized_lons, np.array([-180.0, -90.0, 0.0]))
    assert np.allclose(normalized_values, np.array([[2.0, 3.0, 1.0]], dtype=np.float32))


def test_prepare_slice_converts_geopotential_to_height_meters() -> None:
    values = np.array([[9806.65]], dtype=np.float32)
    prepared = stage_script._prepare_slice(values, spec=stage_script.FIELD_SPECS["hgt500"])
    assert np.allclose(prepared, np.array([[1000.0]], dtype=np.float32), atol=1.0e-4)


def test_transform_from_latlon_produces_north_up_grid() -> None:
    transform = stage_script._transform_from_latlon(
        np.array([-100.0, -99.0], dtype=np.float64),
        np.array([41.0, 40.0], dtype=np.float64),
    )
    expected = rasterio.transform.from_origin(-100.5, 41.5, 1.0, 1.0)
    assert transform == expected