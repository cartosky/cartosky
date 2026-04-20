from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import xarray as xr
import rasterio.crs
import rasterio.transform

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder.fetch import fetch_variable
from app.services.builder.pipeline import build_frame
from app.services.builder.derive import FetchContext
from app.models.eps import EPS_MODEL


class _FakeHerbie:
    def __init__(self, *_args, **_kwargs) -> None:
        self.priority = _kwargs.get("priority")

    def xarray(self, _search_pattern: str):
        lat = np.array([46.0, 45.0], dtype=np.float64)
        lon = np.array([-101.0, -100.0], dtype=np.float64)
        members = xr.Dataset(
            {
                "t2m": (("number", "latitude", "longitude"), np.array(
                    [
                        [[1.0, 2.0], [3.0, 4.0]],
                        [[5.0, 6.0], [7.0, 8.0]],
                    ],
                    dtype=np.float32,
                )),
            },
            coords={
                "number": np.array([1, 2], dtype=np.int64),
                "latitude": lat,
                "longitude": lon,
            },
        )
        control = xr.Dataset(
            {
                "t2m": (("latitude", "longitude"), np.array([[99.0, 99.0], [99.0, 99.0]], dtype=np.float32)),
            },
            coords={
                "number": np.int64(0),
                "latitude": lat,
                "longitude": lon,
            },
        )
        return [members, control]


def test_fetch_variable_aggregates_ecmwf_eps_pf_members() -> None:
    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbie

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        data, crs, transform, meta = fetch_variable(
            model_id="ifs",
            product="enfo",
            search_pattern=":2t:",
            run_date=datetime(2026, 4, 19, 0, 0),
            fh=0,
            herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_pf_mean", "priority": ["azure"]},
            return_meta=True,
        )

    assert np.array_equal(data, np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32))
    assert crs.to_epsg() == 4326
    assert transform.c == -101.5
    assert transform.f == 46.5
    assert meta["inventory_line"] == "aggregate::2t::pf_mean"


def test_build_frame_uses_underlying_herbie_model_for_eps(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_fetch_variable(*, model_id: str, product: str, search_pattern: str, run_date: datetime, fh: int, herbie_kwargs=None, bundle_fetch_cache=None, return_meta=False):
        del search_pattern, run_date, fh, herbie_kwargs, bundle_fetch_cache
        captured["model_id"] = model_id
        captured["product"] = product
        data = np.array([[273.15, 273.15], [273.15, 273.15]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        if return_meta:
            return data, crs, transform, {"inventory_line": "fake"}
        return data, crs, transform

    monkeypatch.setattr("app.services.builder.pipeline.fetch_variable", _fake_fetch_variable)

    build_frame(
        model="eps",
        region="conus",
        var_id="tmp2m__mean",
        fh=0,
        run_date=datetime(2026, 4, 19, 0, 0),
        data_root=tmp_path,
        product="enfo",
        model_plugin=EPS_MODEL,
        ensemble_view="mean",
        fetch_ctx=FetchContext(coverage="conus"),
        log_fetch_cache_stats=False,
    )

    assert captured["model_id"] == "ifs"
    assert captured["product"] == "enfo"