from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import pandas as pd
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
        self.grib = "https://example.invalid/eps-enfo.grib2"
        self.idx = "https://example.invalid/eps-enfo.index"

    @property
    def index_as_dataframe(self):
        return pd.DataFrame(
            [
                {"search_this": ":2t:sfc:g:0001:od:cf:enfo", "type": "cf", "number": np.nan, "start_byte": 0, "end_byte": 9},
                {"search_this": ":2t:sfc:1:g:0001:od:pf:enfo", "type": "pf", "number": 1, "start_byte": 10, "end_byte": 19},
                {"search_this": ":2t:sfc:2:g:0001:od:pf:enfo", "type": "pf", "number": 2, "start_byte": 20, "end_byte": 29},
            ]
        )


def test_fetch_variable_aggregates_ecmwf_eps_pf_members() -> None:
    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbie

    payload_to_data = {
        b"member-1": np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        b"member-2": np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
    }

    def _fake_fetch_range_bytes(**kwargs):
        start_byte = int(kwargs["start_byte"])
        if start_byte == 10:
            return b"member-1"
        if start_byte == 20:
            return b"member-2"
        raise AssertionError(f"unexpected byte-range fetch start={start_byte}")

    def _fake_read_grib_raster(payload):
        data = payload_to_data[payload]
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch("app.services.builder.fetch._fetch_range_bytes", side_effect=_fake_fetch_range_bytes), patch(
            "app.services.builder.fetch._read_grib_raster", side_effect=_fake_read_grib_raster
        ):
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
    assert transform.c == -101.0
    assert transform.f == 46.0
    assert meta["inventory_line"] == ":2t:sfc:1:g:0001:od:pf:enfo"
    assert meta["member_count"] == 2


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