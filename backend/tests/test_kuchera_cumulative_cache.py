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


def test_kuchera_load_prior_cumulative_rejects_pre_quality_schema_entry(tmp_path: Path) -> None:
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

    assert loaded is None


def test_kuchera_load_prior_cumulative_rejects_pre_quality_in_memory_entry(tmp_path: Path) -> None:
    run_date = datetime(2026, 7, 15, 0, 0)
    ctx = derive_module.FetchContext()
    setattr(ctx, "data_root", str(tmp_path))
    setattr(ctx, "kuchera_cumulative_cache", {})
    cache_key = ("gfs", "20260715_00z", "precip_total", 6, "native")
    ctx.kuchera_cumulative_cache[cache_key] = (
        np.array([[1.0]], dtype=np.float32),
        CRS.from_epsg(4326),
        Affine.identity(),
    )

    loaded = derive_module._kuchera_load_prior_cumulative(
        model_id="gfs",
        run_date=run_date,
        var_key="precip_total",
        fh=6,
        ctx=ctx,
        grid_cache_key="native",
    )

    assert loaded is None


def test_cumulative_cache_round_trips_quality_flags(tmp_path: Path) -> None:
    writer_ctx = derive_module.FetchContext(coverage="conus")
    setattr(writer_ctx, "data_root", str(tmp_path))
    details = {"accum_step_gap": {"affected_pixel_percentage": 25.0}}
    gap_mask = np.array([[True, False], [False, False]], dtype=bool)

    derive_module._kuchera_store_cumulative_cache(
        model_id="hrrr",
        run_date=datetime(2026, 3, 25, 12, 0),
        var_key="snowfall_kuchera_total",
        fh=34,
        data=np.ones((2, 2), dtype=np.float32),
        crs=CRS.from_epsg(4326),
        transform=Affine.identity(),
        ctx=writer_ctx,
        grid_cache_key="quality-schema-test",
        quality_flags=["accum_step_gap"],
        quality_flag_details=details,
        accum_step_gap_mask=gap_mask,
    )

    reader_ctx = derive_module.FetchContext(coverage="conus")
    setattr(reader_ctx, "data_root", str(tmp_path))
    loaded = derive_module._kuchera_load_prior_cumulative(
        model_id="hrrr",
        run_date=datetime(2026, 3, 25, 12, 0),
        var_key="snowfall_kuchera_total",
        fh=34,
        ctx=reader_ctx,
        grid_cache_key="quality-schema-test",
    )

    assert loaded is not None
    _data, _crs, _transform, metadata = loaded
    assert metadata["quality_flags"] == ["accum_step_gap"]
    assert metadata["quality_flag_details"] == details
    np.testing.assert_array_equal(metadata["accum_step_gap_mask"], gap_mask)
