from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module


def _vort500_var_spec() -> SimpleNamespace:
    return SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "u_component": "u500",
                "v_component": "v500",
            }
        )
    )


def test_aigfs_vort500_derive_zero_wind_returns_zero_relative_vorticity(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine(1.0, 0.0, -2.0, 0.0, -1.0, 2.0)
    zeros = np.zeros((3, 4), dtype=np.float32)

    def _fake_fetch_component(**kwargs):
        return zeros, crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    derived, out_crs, out_transform = derive_module._derive_vort500_from_uv(
        model_id="aigfs",
        var_key="vort500",
        product="pres",
        run_date=datetime(2026, 4, 16, 12, 0),
        fh=0,
        var_spec_model=_vort500_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )

    expected = np.zeros((3, 4), dtype=np.float32)

    assert out_crs == crs
    assert out_transform == transform
    np.testing.assert_allclose(derived, expected, rtol=1e-5, atol=1e-5)
