from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import xarray as xr

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder.fetch import fetch_variable


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