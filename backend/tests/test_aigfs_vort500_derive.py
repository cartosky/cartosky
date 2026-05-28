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


def test_aigfs_vort500_derive_uses_warped_components_when_target_grid_requested(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine(0.5, 0.0, -130.0, 0.0, -0.5, 55.0)
    zeros = np.zeros((657, 682), dtype=np.float32)

    def _unexpected_native_fetch(**kwargs):
        raise AssertionError("native fetch path should not be used")

    def _fake_fetch_component_warped(**kwargs):
        assert kwargs["target_region"] == "na"
        assert kwargs["target_grid_id"] == "climatology:era5:na:25000.0m"
        return zeros, crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _unexpected_native_fetch)
    monkeypatch.setattr(derive_module, "_fetch_component_warped", _fake_fetch_component_warped)
    monkeypatch.setattr(derive_module, "convert_units", lambda data, **kwargs: data)

    derived, out_crs, out_transform = derive_module._derive_vort500_from_uv(
        model_id="aigfs",
        var_key="vort500",
        product="pres",
        run_date=datetime(2026, 4, 16, 12, 0),
        fh=0,
        var_spec_model=_vort500_var_spec(),
        var_capability=None,
        model_plugin=object(),
        derive_component_target_grid={"region": "na", "id": "climatology:era5:na:25000.0m"},
        derive_component_resampling="bilinear",
    )

    assert derived.shape == (657, 682)
    assert out_crs == crs
    assert out_transform == transform
    np.testing.assert_allclose(derived, zeros, rtol=1e-5, atol=1e-5)


def test_aigfs_vort500_derive_reprojects_projected_crs_to_geographic(monkeypatch) -> None:
    crs = CRS.from_epsg(3857)
    transform = Affine(100000.0, 0.0, -200000.0, 0.0, -100000.0, 200000.0)
    zeros = np.zeros((3, 4), dtype=np.float32)

    def _fake_fetch_component(**kwargs):
        return zeros, crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(derive_module, "convert_units", lambda data, **kwargs: data)

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

    assert bool(getattr(out_crs, "is_geographic", False)) is True
    assert derived.shape[0] >= 2
    assert derived.shape[1] >= 2
    assert out_transform != transform
    np.testing.assert_allclose(derived, 0.0, rtol=1e-5, atol=1e-5)
