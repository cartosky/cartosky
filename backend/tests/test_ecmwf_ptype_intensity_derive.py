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
        "precip_component": "precip_total",
        "snow_component": "sf",
        "surface_temp_component": "tmp2m",
        "low_temp_component": "tmp925",
        "mid_temp_component": "tmp850",
        "step_hours": "3",
        "step_transition_fh": "144",
        "step_hours_after_fh": "6",
        "contour_component": "msl",
    }
    if component is not None:
        hints["ptype_component"] = component
    return SimpleNamespace(selectors=SimpleNamespace(hints=hints))


def test_ecmwf_ptype_intensity_prefers_snow_from_sf_step(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    cumulative = {
        ("precip_total", 3): np.array([[0.03]], dtype=np.float32),
        ("precip_total", 6): np.array([[0.06]], dtype=np.float32),
        ("sf", 3): np.array([[0.02]], dtype=np.float32),
        ("sf", 6): np.array([[0.05]], dtype=np.float32),
    }
    thermal = {
        "tmp2m": np.array([[-2.0]], dtype=np.float32),
        "tmp925": np.array([[-3.0]], dtype=np.float32),
        "tmp850": np.array([[-5.0]], dtype=np.float32),
    }

    def _fake_fetch_step_component(**kwargs):
        # Thermal fields also flow through _fetch_step_component now; serve them too.
        var_key = str(kwargs["var_key"])
        key = (var_key, int(kwargs["step_fh"]))
        if key in cumulative:
            return cumulative[key], crs, transform
        return thermal[var_key], crs, transform

    def _fake_fetch_component(**kwargs):
        return thermal[str(kwargs["var_key"])], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, out_crs, out_transform = derive_module._derive_ptype_intensity_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )
    snow_values, _, _ = derive_module._derive_ptype_intensity_component_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity_snow",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
    )
    rain_values, _, _ = derive_module._derive_ptype_intensity_component_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity_rain",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert 16.0 <= indexed[0, 0] <= 25.0
    assert snow_values[0, 0] > 0.0
    assert rain_values[0, 0] == 0.0


def test_ecmwf_ptype_intensity_marks_freezing_rain_transition(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    cumulative = {
        ("precip_total", 3): np.array([[0.02]], dtype=np.float32),
        ("precip_total", 6): np.array([[0.06]], dtype=np.float32),
        ("sf", 3): np.array([[0.0005]], dtype=np.float32),
        ("sf", 6): np.array([[0.0010]], dtype=np.float32),
    }
    thermal = {
        "tmp2m": np.array([[-2.0]], dtype=np.float32),
        "tmp925": np.array([[2.5]], dtype=np.float32),
        "tmp850": np.array([[3.0]], dtype=np.float32),
    }

    def _fake_fetch_step_component(**kwargs):
        # Thermal fields also flow through _fetch_step_component now; serve them too.
        var_key = str(kwargs["var_key"])
        key = (var_key, int(kwargs["step_fh"]))
        if key in cumulative:
            return cumulative[key], crs, transform
        return thermal[var_key], crs, transform

    def _fake_fetch_component(**kwargs):
        return thermal[str(kwargs["var_key"])], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, _, _ = derive_module._derive_ptype_intensity_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )
    ice_values, _, _ = derive_module._derive_ptype_intensity_component_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity_ice",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("ice"),
        var_capability=None,
        model_plugin=object(),
    )

    assert 26.0 <= indexed[0, 0] <= 43.0
    assert ice_values[0, 0] > 0.0


def test_ecmwf_ice_total_accumulates_unscaled_ice_steps(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    cumulative = {
        ("precip_total", 6): np.array([[0.0060, 0.0060]], dtype=np.float32),
        ("precip_total", 12): np.array([[0.0180, 0.0180]], dtype=np.float32),
        ("sf", 6): np.array([[0.0003, 0.0050]], dtype=np.float32),
        ("sf", 12): np.array([[0.0006, 0.0160]], dtype=np.float32),
    }
    thermal = {
        "tmp2m": np.array([[-2.0, -5.0]], dtype=np.float32),
        "tmp925": np.array([[2.5, -4.0]], dtype=np.float32),
        "tmp850": np.array([[3.0, -7.0]], dtype=np.float32),
    }

    def _fake_fetch_step_component(**kwargs):
        # Thermal fields also flow through _fetch_step_component now; serve them too.
        var_key = str(kwargs["var_key"])
        key = (var_key, int(kwargs["step_fh"]))
        if key in cumulative:
            return cumulative[key], crs, transform
        return thermal[var_key], crs, transform

    def _fake_fetch_component(**kwargs):
        return thermal[str(kwargs["var_key"])], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    values, out_crs, out_transform = derive_module._derive_ptype_accumulation_ecmwf(
        model_id="ecmwf",
        var_key="ice_total",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=12,
        var_spec_model=SimpleNamespace(
            selectors=SimpleNamespace(
                hints={
                    "ptype_component": "ice",
                    "precip_component": "precip_total",
                    "snow_component": "sf",
                    "surface_temp_component": "tmp2m",
                    "low_temp_component": "tmp925",
                    "mid_temp_component": "tmp850",
                    "step_hours": "6",
                }
            )
        ),
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    expected = np.array([[0.0180 * 39.37007874015748, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(values, expected, rtol=1e-5, atol=1e-5)


def test_ecmwf_ptype_intensity_keeps_warm_precip_as_rain(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    cumulative = {
        ("precip_total", 3): np.array([[0.02]], dtype=np.float32),
        ("precip_total", 6): np.array([[0.05]], dtype=np.float32),
        ("sf", 3): np.array([[0.0]], dtype=np.float32),
        ("sf", 6): np.array([[0.0]], dtype=np.float32),
    }
    thermal = {
        "tmp2m": np.array([[6.0]], dtype=np.float32),
        "tmp925": np.array([[4.0]], dtype=np.float32),
        "tmp850": np.array([[3.0]], dtype=np.float32),
    }

    def _fake_fetch_step_component(**kwargs):
        # Thermal fields also flow through _fetch_step_component now; serve them too.
        var_key = str(kwargs["var_key"])
        key = (var_key, int(kwargs["step_fh"]))
        if key in cumulative:
            return cumulative[key], crs, transform
        return thermal[var_key], crs, transform

    def _fake_fetch_component(**kwargs):
        return thermal[str(kwargs["var_key"])], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, _, _ = derive_module._derive_ptype_intensity_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )
    rain_values, _, _ = derive_module._derive_ptype_intensity_component_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity_rain",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
    )

    assert 0.0 <= indexed[0, 0] <= 15.0
    assert rain_values[0, 0] > 0.0


def test_ecmwf_ptype_intensity_can_fallback_to_cold_snow_without_sf(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    cumulative = {
        ("precip_total", 3): np.array([[0.01]], dtype=np.float32),
        ("precip_total", 6): np.array([[0.04]], dtype=np.float32),
        ("sf", 3): np.array([[0.0]], dtype=np.float32),
        ("sf", 6): np.array([[0.0]], dtype=np.float32),
    }
    thermal = {
        "tmp2m": np.array([[-5.0]], dtype=np.float32),
        "tmp925": np.array([[-4.0]], dtype=np.float32),
        "tmp850": np.array([[-7.0]], dtype=np.float32),
    }

    def _fake_fetch_step_component(**kwargs):
        # Thermal fields also flow through _fetch_step_component now; serve them too.
        var_key = str(kwargs["var_key"])
        key = (var_key, int(kwargs["step_fh"]))
        if key in cumulative:
            return cumulative[key], crs, transform
        return thermal[var_key], crs, transform

    def _fake_fetch_component(**kwargs):
        return thermal[str(kwargs["var_key"])], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, _, _ = derive_module._derive_ptype_intensity_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )

    assert 16.0 <= indexed[0, 0] <= 25.0


def test_ecmwf_ptype_intensity_uses_warped_component_fetches_when_requested(monkeypatch) -> None:
    crs = CRS.from_epsg(3857)
    transform = Affine.translation(-1.0, 1.0)
    cumulative = {
        ("precip_total", 3): np.array([[0.01]], dtype=np.float32),
        ("precip_total", 6): np.array([[0.04]], dtype=np.float32),
        ("sf", 3): np.array([[0.0]], dtype=np.float32),
        ("sf", 6): np.array([[0.0]], dtype=np.float32),
    }
    thermal = {
        "tmp2m": np.array([[-5.0]], dtype=np.float32),
        "tmp925": np.array([[-4.0]], dtype=np.float32),
        "tmp850": np.array([[-7.0]], dtype=np.float32),
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
        key = (var_key, int(kwargs["step_fh"]))
        if key in cumulative:
            return cumulative[key], crs, transform
        return thermal[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)

    indexed, out_crs, out_transform = derive_module._derive_ptype_intensity_ecmwf(
        model_id="ecmwf",
        var_key="ptype_intensity",
        product="oper",
        run_date=datetime(2026, 4, 14, 0, 0),
        fh=6,
        var_spec_model=_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
        ctx=derive_module.FetchContext(),
        derive_component_target_grid={"region": "na", "id": "climatology:era5:na:25000.0m"},
        derive_component_resampling="nearest",
    )

    assert out_crs == crs
    assert out_transform == transform
    assert {item[0] for item in seen} == {"precip_total", "sf", "tmp2m", "tmp925", "tmp850"}
    assert all(item[1:] == (True, "na", "climatology:era5:na:25000.0m", "nearest") for item in seen)
    # Cold profile with sf=0 relies entirely on the thermal phase signals: before
    # the warp-params fix those fetches came back native-shape, were silently
    # skipped, and this pixel classified as rain instead of snow.
    assert 16.0 <= indexed[0, 0] <= 25.0