from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module


def test_kuchera_store_writes_six_value_transform(tmp_path: Path) -> None:
    ctx = derive_module.FetchContext(coverage="conus")
    setattr(ctx, "data_root", str(tmp_path))

    derive_module._kuchera_store_cumulative_cache(
        model_id="hrrr",
        run_date=datetime(2026, 3, 25, 12, 0),
        var_key="snowfall_kuchera_total",
        fh=34,
        data=np.ones((2, 2), dtype=np.float32),
        crs=CRS.from_epsg(4326),
        transform=Affine(1.0, 0.0, 3.0, 0.0, -1.0, 4.0),
        ctx=ctx,
        grid_cache_key="warped:hrrr:conus:3000.0m:bilinear",
    )

    cache_path = (
        tmp_path
        / "staging"
        / "hrrr"
        / "20260325_12z"
        / "snowfall_kuchera_total"
        / "fh034.cumulative-cache.npz"
    )
    with np.load(cache_path, allow_pickle=False) as cached:
        transform_values = np.asarray(cached["transform"], dtype=np.float64).reshape(-1)
    assert transform_values.size == 6
    np.testing.assert_allclose(transform_values, np.array([1.0, 0.0, 3.0, 0.0, -1.0, 4.0]))


def test_kuchera_load_prior_cumulative_accepts_legacy_nine_value_transform(tmp_path: Path) -> None:
    cache_path = (
        tmp_path
        / "staging"
        / "hrrr"
        / "20260325_12z"
        / "snowfall_kuchera_total"
        / "fh034.cumulative-cache.npz"
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache_path,
        data=np.full((2, 2), 7.0, dtype=np.float32),
        crs_wkt=CRS.from_epsg(4326).to_wkt(),
        transform=np.asarray((1.0, 0.0, 3.0, 0.0, -1.0, 4.0, 0.0, 0.0, 1.0), dtype=np.float64),
        grid_cache_key="warped:hrrr:conus:3000.0m:bilinear",
    )

    ctx = derive_module.FetchContext(coverage="conus")
    setattr(ctx, "data_root", str(tmp_path))
    loaded = derive_module._kuchera_load_prior_cumulative(
        model_id="hrrr",
        run_date=datetime(2026, 3, 25, 12, 0),
        var_key="snowfall_kuchera_total",
        fh=34,
        ctx=ctx,
        grid_cache_key="warped:hrrr:conus:3000.0m:bilinear",
    )

    assert loaded is not None
    data, crs, transform = loaded
    np.testing.assert_allclose(data, np.full((2, 2), 7.0, dtype=np.float32))
    assert crs == CRS.from_epsg(4326)
    assert transform == Affine(1.0, 0.0, 3.0, 0.0, -1.0, 4.0)
