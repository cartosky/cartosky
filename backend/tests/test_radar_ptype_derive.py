from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine

from app.services.builder import derive as derive_module
from app.services.colormaps import RADAR_PTYPE_BREAKS, RADAR_PTYPE_LEVELS_BY_TYPE, RADAR_PTYPE_ORDER


def _expected_radar_ptype_index(ptype: str, reflectivity: float) -> float:
    breaks = RADAR_PTYPE_BREAKS[ptype]
    offset = int(breaks["offset"])
    count = int(breaks["count"])
    levels = RADAR_PTYPE_LEVELS_BY_TYPE[RADAR_PTYPE_ORDER[0]]
    refl_min = float(levels[0])
    refl_max = float(levels[-1])
    normalized = np.clip((float(reflectivity) - refl_min) / max(refl_max - refl_min, 1.0), 0.0, 1.0)
    return float(offset + int(np.clip(np.rint(normalized * (count - 1)), 0, count - 1)))


def _radar_ptype_var_spec(component: str | None = None) -> SimpleNamespace:
    hints = {
        "refl_component": "refc",
        "rain_component": "crain",
        "snow_component": "csnow",
        "sleet_component": "cicep",
        "frzr_component": "cfrzr",
        "min_visible_dbz": "10.0",
    }
    if component is not None:
        hints["ptype_component"] = component
    return SimpleNamespace(selectors=SimpleNamespace(hints=hints))


def test_nam_radar_ptype_preserves_single_light_pixel_without_extra_gating(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "refc": np.array([[8.0]], dtype=np.float32),
        "crain": np.array([[0.25]], dtype=np.float32),
        "csnow": np.array([[0.0]], dtype=np.float32),
        "cicep": np.array([[0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0]], dtype=np.float32),
    }

    def _fake_fetch_component(**kwargs):
        return component_data[str(kwargs["var_key"])], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    indexed, out_crs, out_transform = derive_module._derive_radar_ptype_combo(
        model_id="nam",
        var_key="radar_ptype",
        product="sfc",
        run_date=datetime(2026, 5, 26, 18),
        fh=1,
        var_spec_model=SimpleNamespace(
            selectors=SimpleNamespace(
                hints={
                    "refl_component": "refc",
                    "rain_component": "crain",
                    "snow_component": "csnow",
                    "sleet_component": "cicep",
                    "frzr_component": "cfrzr",
                    "min_visible_dbz": "5.0",
                }
            )
        ),
        var_capability=None,
        model_plugin=object(),
        ctx=derive_module.FetchContext(coverage="conus"),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert float(indexed[0, 0]) == _expected_radar_ptype_index("rain", 8.0)


def test_radar_ptype_components_preserve_classified_reflectivity(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    component_data = {
        "refc": np.array([[35.0, 28.0, 42.0, 55.0, 8.0]], dtype=np.float32),
        "crain": np.array([[1.0, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32),
        "csnow": np.array([[0.0, 1.0, 0.0, 0.0, 0.0]], dtype=np.float32),
        "cicep": np.array([[0.0, 0.0, 1.0, 0.0, 0.0]], dtype=np.float32),
        "cfrzr": np.array([[0.0, 0.0, 0.0, 1.0, 0.0]], dtype=np.float32),
    }
    fetch_calls: list[str] = []

    def _fake_fetch_component(**kwargs):
        var_key = str(kwargs["var_key"])
        fetch_calls.append(var_key)
        return component_data[var_key], crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    ctx = derive_module.FetchContext(coverage="conus")
    indexed, out_crs, out_transform = derive_module._derive_radar_ptype_combo(
        model_id="hrrr",
        var_key="radar_ptype",
        product="sfc",
        run_date=datetime(2026, 5, 26, 18),
        fh=1,
        var_spec_model=_radar_ptype_var_spec(),
        var_capability=None,
        model_plugin=object(),
        ctx=ctx,
    )
    rain_values, _, _ = derive_module._derive_radar_ptype_component(
        model_id="hrrr",
        var_key="radar_ptype_rain",
        product="sfc",
        run_date=datetime(2026, 5, 26, 18),
        fh=1,
        var_spec_model=_radar_ptype_var_spec("rain"),
        var_capability=None,
        model_plugin=object(),
        ctx=ctx,
    )
    snow_values, _, _ = derive_module._derive_radar_ptype_component(
        model_id="hrrr",
        var_key="radar_ptype_snow",
        product="sfc",
        run_date=datetime(2026, 5, 26, 18),
        fh=1,
        var_spec_model=_radar_ptype_var_spec("snow"),
        var_capability=None,
        model_plugin=object(),
        ctx=ctx,
    )
    sleet_values, _, _ = derive_module._derive_radar_ptype_component(
        model_id="hrrr",
        var_key="radar_ptype_sleet",
        product="sfc",
        run_date=datetime(2026, 5, 26, 18),
        fh=1,
        var_spec_model=_radar_ptype_var_spec("sleet"),
        var_capability=None,
        model_plugin=object(),
        ctx=ctx,
    )
    frzr_values, _, _ = derive_module._derive_radar_ptype_component(
        model_id="hrrr",
        var_key="radar_ptype_frzr",
        product="sfc",
        run_date=datetime(2026, 5, 26, 18),
        fh=1,
        var_spec_model=_radar_ptype_var_spec("frzr"),
        var_capability=None,
        model_plugin=object(),
        ctx=ctx,
    )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_calls == ["refc", "crain", "csnow", "cicep", "cfrzr"]
    assert np.isfinite(indexed[0, :4]).all()
    assert np.isnan(indexed[0, 4])
    assert float(indexed[0, 0]) == _expected_radar_ptype_index("rain", 35.0)
    assert float(indexed[0, 1]) == _expected_radar_ptype_index("snow", 28.0)
    assert float(indexed[0, 2]) == _expected_radar_ptype_index("sleet", 42.0)
    assert float(indexed[0, 3]) == _expected_radar_ptype_index("frzr", 55.0)
    assert tuple(RADAR_PTYPE_ORDER) == ("rain", "snow", "sleet", "frzr")
    np.testing.assert_array_equal(rain_values, np.array([[35.0, 0.0, 0.0, 0.0, 0.0]], dtype=np.float32))
    np.testing.assert_array_equal(snow_values, np.array([[0.0, 28.0, 0.0, 0.0, 0.0]], dtype=np.float32))
    np.testing.assert_array_equal(sleet_values, np.array([[0.0, 0.0, 42.0, 0.0, 0.0]], dtype=np.float32))
    np.testing.assert_array_equal(frzr_values, np.array([[0.0, 0.0, 0.0, 55.0, 0.0]], dtype=np.float32))
