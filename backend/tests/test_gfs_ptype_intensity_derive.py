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
        "apcp_component": "apcp_step",
        "step_hours": "6",
        "rain_component": "crain",
        "snow_component": "csnow",
        "sleet_component": "cicep",
        "frzr_component": "cfrzr",
    }
    if component is not None:
        hints["ptype_component"] = component
    return SimpleNamespace(selectors=SimpleNamespace(hints=hints))


def _make_fake_step_intensity(apcp_step_data: np.ndarray):
    """Return a mock for ``_ptype_intensity_fetch_step_intensity`` that
    converts raw APCP step data (kg/m²) to inches, matching the real function's
    output contract."""
    inch_scale = np.float32(0.03937007874015748)

    def _fake(**kwargs):
        expected_shape = tuple(kwargs["expected_shape"])
        if tuple(apcp_step_data.shape) != expected_shape:
            return None
        values = np.asarray(apcp_step_data, dtype=np.float32)
        valid = np.isfinite(values) & (values >= 0.0)
        step_inches = (values * inch_scale).astype(np.float32, copy=False)
        return np.where(valid, step_inches, np.nan).astype(np.float32, copy=False)

    return _fake


def test_ptype_intensity_component_weights_preserve_winter_signal(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
    )

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

    expected_total_rate = np.float32(10.0 * 0.03937007874015748)
    assert snow_values[0, 0] > rain_values[0, 0]
    assert snow_values[0, 1] > rain_values[0, 1]
    assert snow_values[0, 2] > rain_values[0, 2]
    assert np.all((rain_values + ice_values) <= expected_total_rate)
    np.testing.assert_allclose(snow_values, expected_total_rate * 2.0, rtol=1e-5, atol=1e-5)
    assert np.count_nonzero(rain_values[0] > 0.0) + np.count_nonzero(snow_values[0] > 0.0) + np.count_nonzero(ice_values[0] > 0.0) == 3


def test_ptype_intensity_uses_warped_component_fetches_when_requested(monkeypatch) -> None:
    crs = CRS.from_epsg(3857)
    transform = Affine.translation(-1.0, 1.0)
    target_shape = (2, 2)
    component_data = {
        "prate": np.full(target_shape, 10.0, dtype=np.float32),
        "crain": np.ones(target_shape, dtype=np.float32),
        "csnow": np.zeros(target_shape, dtype=np.float32),
        "cicep": np.zeros(target_shape, dtype=np.float32),
        "cfrzr": np.zeros(target_shape, dtype=np.float32),
        "tmp2m": np.full(target_shape, 3.0, dtype=np.float32),
        "tmp850": np.full(target_shape, 4.0, dtype=np.float32),
    }
    seen: list[tuple[str, bool, str, str, str]] = []

    def _fake_fetch_step_component(**kwargs):
        var_key = str(kwargs["var_key"])
        seen.append(
            (
                var_key,
                bool(kwargs["use_warped"]),
                str(kwargs["target_region"]),
                str(kwargs["target_grid_id"]),
                str(kwargs["resampling"]),
            )
        )
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(np.full(target_shape, 2.0, dtype=np.float32)),
    )

    values, out_crs, out_transform = derive_module._derive_ptype_intensity_gfs(
        model_id="gfs",
        var_key="ptype_intensity",
        product="pgrb2.0p25",
        run_date=datetime(2026, 5, 19, 12),
        fh=0,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=SimpleNamespace(),
        ctx=derive_module.FetchContext(),
        derive_component_target_grid={"region": "na", "id": "climatology:era5:na:25000.0m"},
        derive_component_resampling="nearest",
    )

    assert values.shape == target_shape
    assert out_crs == crs
    assert out_transform == transform
    assert {item[0] for item in seen} == {"prate", "crain", "csnow", "cicep", "cfrzr", "tmp2m", "tmp850"}
    assert all(item[1:] == (True, "na", "climatology:era5:na:25000.0m", "nearest") for item in seen)


def test_ptype_intensity_visible_index_prefers_weighted_winter_family(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
    )

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

    assert 16.0 <= indexed[0, 0] <= 25.0   # csnow=1, crain=1 → snow wins (priority)
    assert 16.0 <= indexed[0, 1] <= 25.0   # csnow=0.9, crain=1 → snow wins (priority)
    assert 26.0 <= indexed[0, 2] <= 43.0   # cicep=1 → ice wins (highest priority)


def test_ptype_intensity_thermal_profile_can_promote_snow_without_csnow(monkeypatch) -> None:
    """When csnow=0 but crain=1, the model says rain — we trust it even if
    temps are cold.  Thermal fallback only fires when ALL masks are zero."""
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    expected_total_rate = np.float32(10.0 * 0.03937007874015748)
    # Model says rain (crain=1, csnow=0) — we trust it even though temps are cold
    assert rain_values[0, 0] > 0.0
    assert snow_values[0, 0] == 0.0
    assert 0.0 <= indexed[0, 0] <= 15.0  # rain palette range


def test_ptype_intensity_snow_component_uses_display_boost(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    expected_total_rate = np.float32(10.0 * 0.03937007874015748)
    np.testing.assert_allclose(snow_values[0, 0], expected_total_rate * 2.0, rtol=1e-5, atol=1e-5)


def test_ptype_intensity_preserves_prate_coverage_when_ptype_masks_are_empty(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0, 10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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
        "apcp_step": np.array([[10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    expected_total_rate = np.float32(10.0 * 0.03937007874015748)
    np.testing.assert_allclose(snow_values[0, 0], expected_total_rate * 2.0, rtol=1e-5, atol=1e-5)
    assert rain_values[0, 0] == 0.0
    assert 16.0 <= indexed[0, 0] <= 25.0


def test_ptype_intensity_midlevel_cold_can_override_rain_mask(monkeypatch) -> None:
    """When crain=1 and csnow=0, midlevel cold does NOT override — the model's
    categorical mask is authoritative.  This pixel is rain."""
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0]], dtype=np.float32),
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
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    # Model says rain (crain=1, csnow=0) — we trust it even with cold 850mb
    assert rain_values[0, 0] > snow_values[0, 0]


def test_ptype_intensity_prefers_shared_apcp_step_intensity_over_prate(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[20.0]], dtype=np.float32),
        "apcp_step": np.array([[5.0]], dtype=np.float32),
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

    def _fake_step_intensity(**kwargs):
        expected_shape = tuple(kwargs["expected_shape"])
        assert expected_shape == (1, 1)
        return np.array([[5.0 * 0.03937007874015748]], dtype=np.float32)

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(derive_module, "_ptype_intensity_fetch_step_intensity", _fake_step_intensity)

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

    expected_step_rate = np.float32(5.0 * 0.03937007874015748)
    np.testing.assert_allclose(snow_values[0, 0], expected_step_rate * 2.0, rtol=1e-5, atol=1e-5)
    assert 16.0 <= indexed[0, 0] <= 25.0


def test_ptype_intensity_moderate_cold_with_full_rain_mask_prefers_snow(monkeypatch) -> None:
    """Regression: crain=1.0 with moderate cold temps (surface near 0C, 850mb
    cold) must still classify as snow. Before the squared rain suppression fix,
    the linear damping left rain_score > snow_score in this scenario."""
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0]], dtype=np.float32),
        "crain": np.array([[1.0]], dtype=np.float32),
        "csnow": np.array([[0.5]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
        # Surface near freezing, 850mb solidly cold — typical CA Sierra scenario
        "tmp2m": np.array([[0.0]], dtype=np.float32),
        "tmp850": np.array([[-5.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    # Snow must win — indexed should fall in the snow palette range (16-25)
    assert 16.0 <= indexed[0, 0] <= 25.0, f"Expected snow index 16-25, got {indexed[0, 0]}"
    assert snow_values[0, 0] > 0.0
    assert rain_values[0, 0] == 0.0


def test_ptype_intensity_gfs_overlap_crain1_csnow1_selects_snow(monkeypatch) -> None:
    """GFS routinely sets both crain=1 and csnow=1 in snow areas.  With
    priority-based selection (ice > snow > rain), snow must always win."""
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[8.0, 15.0]], dtype=np.float32),
        "apcp_step": np.array([[8.0, 15.0]], dtype=np.float32),
        "crain": np.array([[1.0, 1.0]], dtype=np.float32),
        "csnow": np.array([[1.0, 1.0]], dtype=np.float32),
        "cicep": np.array([[0.0, 0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0, 0.0]], dtype=np.float32),
        "tmp2m": np.array([[1.0, -5.0]], dtype=np.float32),
        "tmp850": np.array([[-2.0, -8.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    # Both pixels: csnow=1 has priority over crain=1
    for col in (0, 1):
        assert 16.0 <= indexed[0, col] <= 25.0, f"Pixel {col}: expected snow index 16-25, got {indexed[0, col]}"
        assert snow_values[0, col] > 0.0, f"Pixel {col}: snow_rate should be > 0"
        assert rain_values[0, col] == 0.0, f"Pixel {col}: rain_rate should be 0"


def test_ptype_intensity_warm_rain_only_stays_rain(monkeypatch) -> None:
    """When crain=1 and csnow=0 with warm temps, rain must remain rain.
    Thermal profiles should not interfere."""
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[12.0]], dtype=np.float32),
        "apcp_step": np.array([[12.0]], dtype=np.float32),
        "crain": np.array([[1.0]], dtype=np.float32),
        "csnow": np.array([[0.0]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
        "tmp2m": np.array([[15.0]], dtype=np.float32),
        "tmp850": np.array([[5.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    # Rain palette range: 0-15
    assert 0.0 <= indexed[0, 0] <= 15.0, f"Expected rain index 0-15, got {indexed[0, 0]}"


def test_ptype_intensity_thermal_fallback_when_all_masks_zero(monkeypatch) -> None:
    """When all ptype masks are zero but precip exists, thermal fallback
    should assign based on temperature profiles."""
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "prate": np.array([[10.0, 10.0]], dtype=np.float32),
        "apcp_step": np.array([[10.0, 10.0]], dtype=np.float32),
        "crain": np.array([[0.0, 0.0]], dtype=np.float32),
        "csnow": np.array([[0.0, 0.0]], dtype=np.float32),
        "cicep": np.array([[0.0, 0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0, 0.0]], dtype=np.float32),
        # Pixel 0: cold → snow fallback; Pixel 1: warm → rain fallback
        "tmp2m": np.array([[-5.0, 10.0]], dtype=np.float32),
        "tmp850": np.array([[-8.0, 3.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(
        derive_module,
        "_ptype_intensity_fetch_step_intensity",
        _make_fake_step_intensity(component_data["apcp_step"]),
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

    # Pixel 0: cold temps → thermal fallback → snow
    assert snow_values[0, 0] > 0.0, "Cold pixel should get snow via thermal fallback"
    assert rain_values[0, 0] == 0.0, "Cold pixel should not get rain"
    # Pixel 1: warm temps → thermal fallback → rain
    assert rain_values[0, 1] > 0.0, "Warm pixel should get rain via thermal fallback"
    assert snow_values[0, 1] == 0.0, "Warm pixel should not get snow"
