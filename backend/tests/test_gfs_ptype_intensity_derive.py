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


def _ptype_var_spec(component: str | None = None) -> SimpleNamespace:
    hints = {
        "prate_component": "prate",
        "rain_component": "crain",
        "snow_component": "csnow",
        "sleet_component": "cicep",
        "frzr_component": "cfrzr",
    }
    if component is not None:
        hints["ptype_component"] = component
    return SimpleNamespace(selectors=SimpleNamespace(hints=hints))


def test_ptype_intensity_component_weights_preserve_winter_signal(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
        "crain": np.array([[1.0, 1.0, 0.2]], dtype=np.float32),
        "csnow": np.array([[1.0, 0.9, 1.0]], dtype=np.float32),
        "cicep": np.array([[0.0, 0.0, 0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0, 0.0, 0.0]], dtype=np.float32),
        "tmp2m": np.array([[-3.0, -2.0, -1.0]], dtype=np.float32),
        "tmp850": np.array([[-6.0, -5.0, -4.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    rain_values, out_crs, out_transform = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_rain",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )
    snow_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_snow",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )
    ice_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_ice",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("ice"),
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert rain_values.dtype == np.float32
    assert snow_values.dtype == np.float32
    assert ice_values.dtype == np.float32

    expected_total_rate = np.float32(10.0 * 3600.0 * 0.03937007874015748)
    assert snow_values[0, 0] > rain_values[0, 0]
    assert snow_values[0, 1] > rain_values[0, 1]
    assert snow_values[0, 2] > rain_values[0, 2]
    assert np.all((rain_values + ice_values) <= expected_total_rate)
    np.testing.assert_allclose(snow_values, expected_total_rate * 10.0, rtol=1e-5, atol=1e-4)
    assert np.count_nonzero(rain_values[0] > 0.0) + np.count_nonzero(snow_values[0] > 0.0) + np.count_nonzero(ice_values[0] > 0.0) == 3


def test_ptype_intensity_visible_index_prefers_weighted_winter_family(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
        "crain": np.array([[1.0, 1.0, 0.3]], dtype=np.float32),
        "csnow": np.array([[1.0, 0.9, 1.0]], dtype=np.float32),
        "cicep": np.array([[0.0, 0.0, 1.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0, 0.0, 0.0]], dtype=np.float32),
        "tmp2m": np.array([[-3.0, -2.0, -1.0]], dtype=np.float32),
        "tmp850": np.array([[-6.0, -5.0, -4.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, out_crs, out_transform = derive_module._derive_ptype_intensity_gfs(
        model_id="gfs",
        var_key="ptype_intensity",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert indexed.dtype == np.float32
    assert indexed.shape == (1, 3)

    assert 16.0 <= indexed[0, 0] <= 25.0
    assert 16.0 <= indexed[0, 1] <= 25.0
    assert 16.0 <= indexed[0, 2] <= 25.0


def test_ptype_intensity_thermal_profile_can_promote_snow_without_csnow(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "crain": np.array([[1.0]], dtype=np.float32),
        "csnow": np.array([[0.0]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
        "tmp2m": np.array([[-4.0]], dtype=np.float32),
        "tmp850": np.array([[-7.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    snow_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_snow",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )
    rain_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_rain",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )
    indexed, _, _ = derive_module._derive_ptype_intensity_gfs(
        model_id="gfs",
        var_key="ptype_intensity",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )

    expected_total_rate = np.float32(10.0 * 3600.0 * 0.03937007874015748)
    np.testing.assert_allclose(snow_values[0, 0], expected_total_rate * 10.0, rtol=1e-5, atol=1e-4)
    assert rain_values[0, 0] == 0.0
    assert 16.0 <= indexed[0, 0] <= 25.0


def test_ptype_intensity_snow_component_uses_display_boost(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "crain": np.array([[0.0]], dtype=np.float32),
        "csnow": np.array([[1.0]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
        "tmp2m": np.array([[-4.0]], dtype=np.float32),
        "tmp850": np.array([[-7.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    snow_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_snow",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )

    expected_total_rate = np.float32(10.0 * 3600.0 * 0.03937007874015748)
    np.testing.assert_allclose(snow_values[0, 0], expected_total_rate * 10.0, rtol=1e-5, atol=1e-4)


def test_ptype_intensity_preserves_prate_coverage_when_ptype_masks_are_empty(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0]], dtype=np.float32),
        "crain": np.array([[0.0, 0.0]], dtype=np.float32),
        "csnow": np.array([[0.0, 0.0]], dtype=np.float32),
        "cicep": np.array([[0.0, 0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0, 0.0]], dtype=np.float32),
        "tmp2m": np.array([[3.0, -3.0]], dtype=np.float32),
        "tmp850": np.array([[2.0, -6.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, _, _ = derive_module._derive_ptype_intensity_gfs(
        model_id="gfs",
        var_key="ptype_intensity",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )
    rain_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_rain",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )
    snow_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_snow",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )

    assert np.isfinite(indexed[0, 0])
    assert np.isfinite(indexed[0, 1])
    assert 0.0 <= indexed[0, 0] <= 15.0
    assert 16.0 <= indexed[0, 1] <= 25.0
    assert rain_values[0, 0] > 0.0
    assert snow_values[0, 1] > 0.0


def test_ptype_intensity_cold_precip_prefers_snow_with_weak_snow_mask(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "crain": np.array([[1.0]], dtype=np.float32),
        "csnow": np.array([[0.1]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
        "tmp2m": np.array([[-3.0]], dtype=np.float32),
        "tmp850": np.array([[-6.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, _, _ = derive_module._derive_ptype_intensity_gfs(
        model_id="gfs",
        var_key="ptype_intensity",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )
    snow_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_snow",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )
    rain_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_rain",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )

    expected_total_rate = np.float32(10.0 * 3600.0 * 0.03937007874015748)
    np.testing.assert_allclose(snow_values[0, 0], expected_total_rate * 10.0, rtol=1e-5, atol=1e-4)
    assert rain_values[0, 0] == 0.0
    assert 16.0 <= indexed[0, 0] <= 25.0


def test_ptype_intensity_midlevel_cold_can_override_rain_mask(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "crain": np.array([[1.0]], dtype=np.float32),
        "csnow": np.array([[0.0]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
        "tmp2m": np.array([[0.5]], dtype=np.float32),
        "tmp850": np.array([[-7.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, _, _ = derive_module._derive_ptype_intensity_gfs(
        model_id="gfs",
        var_key="ptype_intensity",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )
    snow_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_snow",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )
    rain_values, _, _ = derive_module._derive_ptype_intensity_component(
        model_id="gfs",
        var_key="ptype_intensity_rain",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 9, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )

    assert snow_values[0, 0] > rain_values[0, 0]
    assert 16.0 <= indexed[0, 0] <= 25.0
