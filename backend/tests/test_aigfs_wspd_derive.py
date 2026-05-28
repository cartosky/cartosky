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


def _wspd_var_spec() -> SimpleNamespace:
    return SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "u_component": "u850",
                "v_component": "v850",
            }
        )
    )


def test_aigfs_wspd_derive_uses_warped_components_when_target_grid_requested(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine(0.5, 0.0, -130.0, 0.0, -0.5, 55.0)
    u_data = np.full((657, 682), 3.0, dtype=np.float32)
    v_data = np.full((657, 682), 4.0, dtype=np.float32)

    def _unexpected_native_fetch(**kwargs):
        raise AssertionError("native fetch path should not be used")

    def _fake_fetch_component_warped(**kwargs):
        assert kwargs["target_region"] == "na"
        assert kwargs["target_grid_id"] == "climatology:era5:na:25000.0m"
        if kwargs["var_key"] == "u850":
            return u_data, crs, transform
        if kwargs["var_key"] == "v850":
            return v_data, crs, transform
        raise AssertionError(f"unexpected component {kwargs['var_key']}")

    monkeypatch.setattr(derive_module, "_fetch_component", _unexpected_native_fetch)
    monkeypatch.setattr(derive_module, "_fetch_component_warped", _fake_fetch_component_warped)
    monkeypatch.setattr(derive_module, "convert_units", lambda data, **kwargs: data)

    derived, out_crs, out_transform = derive_module._derive_wspd10m(
        model_id="aigfs",
        var_key="wspd850",
        product="pres",
        run_date=datetime(2026, 5, 28, 12, 0),
        fh=120,
        var_spec_model=_wspd_var_spec(),
        var_capability=None,
        model_plugin=object(),
        derive_component_target_grid={"region": "na", "id": "climatology:era5:na:25000.0m"},
        derive_component_resampling="bilinear",
    )

    assert derived.shape == (657, 682)
    assert out_crs == crs
    assert out_transform == transform
    np.testing.assert_allclose(derived, 5.0, rtol=0.0, atol=1e-6)