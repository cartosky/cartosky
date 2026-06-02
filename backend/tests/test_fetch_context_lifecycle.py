from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module


def test_prune_fetch_context_after_frame_keeps_only_current_fh_entries() -> None:
    ctx = derive_module.FetchContext(coverage="conus")
    arr = np.ones((2, 2), dtype=np.float32)
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()

    ctx.fetch_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 3, "tmp2m", "sel", "conus", "conus")] = (arr, crs, transform)
    ctx.fetch_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 6, "tmp2m", "sel", "conus", "conus")] = (arr, crs, transform)
    ctx.fetch_meta_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 3, "tmp2m", "sel", "conus", "conus")] = {"fh": 3}
    ctx.fetch_meta_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 6, "tmp2m", "sel", "conus", "conus")] = {"fh": 6}
    ctx.warp_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 3, "tmp2m", "sel", "grid", "bilinear")] = (arr, crs, transform)
    ctx.warp_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 6, "tmp2m", "sel", "grid", "bilinear")] = (arr, crs, transform)
    ctx.warp_meta_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 3, "tmp2m", "sel", "grid", "bilinear")] = {"fh": 3}
    ctx.warp_meta_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 6, "tmp2m", "sel", "grid", "bilinear")] = {"fh": 6}
    ctx.resolved_apcp_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 3, "apcp_step", "sel", 0, "grid", "bilinear")] = (arr, crs, transform, {})
    ctx.resolved_apcp_cache[("ecmwf", "oper", "2026-04-14T00:00:00+00:00", 6, "apcp_step", "sel", 3, "grid", "bilinear")] = (arr, crs, transform, {})
    ctx.ptype_family_cache[("ecmwf", "oper", "2026041400", 3, "ptype_intensity", "[]", "")] = {"indexed": arr, "src_crs": crs, "src_transform": transform}
    ctx.ptype_family_cache[("ecmwf", "oper", "2026041400", 6, "ptype_intensity", "[]", "")] = {"indexed": arr, "src_crs": crs, "src_transform": transform}
    setattr(
        ctx,
        "kuchera_cumulative_cache",
        {
            ("ecmwf", "20260414_00z", "snowfall_kuchera_total", 3, "grid"): (arr, crs, transform, {}),
            ("ecmwf", "20260414_00z", "snowfall_kuchera_total", 6, "grid"): (arr, crs, transform, {}),
        },
    )

    removed = derive_module.prune_fetch_context_after_frame(
        ctx=ctx,
        var_spec_model=SimpleNamespace(derive="snowfall_kuchera_total_cumulative"),
        fh=6,
    )

    assert removed == {
        "fetch": 1,
        "fetch_meta": 1,
        "warp": 1,
        "warp_meta": 1,
        "resolved_apcp": 1,
        "ptype": 1,
        "kuchera": 1,
    }
    assert {int(key[3]) for key in ctx.fetch_cache} == {6}
    assert {int(key[3]) for key in ctx.warp_cache} == {6}
    assert {int(key[3]) for key in ctx.resolved_apcp_cache} == {6}
    assert {int(key[3]) for key in ctx.ptype_family_cache} == {6}
    assert {int(key[3]) for key in getattr(ctx, "kuchera_cumulative_cache")} == {6}


def test_destroy_fetch_context_clears_all_runtime_caches() -> None:
    ctx = derive_module.FetchContext(coverage="conus")
    ctx.fetch_cache[("x", "y", "z", 1, "a", "b", "c", "d")] = (np.ones((1, 1), dtype=np.float32), CRS.from_epsg(4326), Affine.identity())
    ctx.fetch_meta_cache[("x", "y", "z", 1, "a", "b", "c", "d")] = {"fh": 1}
    ctx.warp_cache[("x", "y", "z", 1, "a", "b", "grid", "bilinear")] = (np.ones((1, 1), dtype=np.float32), CRS.from_epsg(3857), Affine.identity())
    ctx.warp_meta_cache[("x", "y", "z", 1, "a", "b", "grid", "bilinear")] = {"fh": 1}
    ctx.resolved_apcp_cache[("x", "y", "z", 1, "apcp", "sel", 0, "grid", "bilinear")] = (np.ones((1, 1), dtype=np.float32), CRS.from_epsg(3857), Affine.identity(), {})
    ctx.ptype_family_cache[("x", "y", "z", 1, "ptype", "[]", "")] = {"indexed": np.ones((1, 1), dtype=np.float32)}
    ctx.derive_quality[("ptype", 1)] = {"quality": "full"}
    ctx.stats["hits"] = 3
    ctx.warp_stats["hits"] = 4
    setattr(ctx, "kuchera_cumulative_cache", {("x", "run", "var", 1, "grid"): (np.ones((1, 1), dtype=np.float32), CRS.from_epsg(4326), Affine.identity(), {})})

    derive_module.destroy_fetch_context(ctx)

    assert not ctx.fetch_cache
    assert not ctx.fetch_meta_cache
    assert not ctx.warp_cache
    assert not ctx.warp_meta_cache
    assert not ctx.resolved_apcp_cache
    assert not ctx.ptype_family_cache
    assert not ctx.derive_quality
    assert not ctx.stats
    assert not ctx.warp_stats
    assert not getattr(ctx, "kuchera_cumulative_cache")
    assert ctx.bundle_fetch_cache is None