from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _resolve_requested_ensemble_view, _runtime_var_id_for_request, _serialize_model_capability
from app.models.eps import EPS_MODEL
from app.services.colormaps import get_color_map_spec
from app.services.render_resampling import variable_color_map_id
from app.services.scheduler import _resolve_vars_to_schedule


def test_eps_run_discovery_invariants() -> None:
    capabilities = EPS_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "stale_cycle_release_minutes_by_hour": {0: 180, 6: 120, 12: 180, 18: 120},
        "source_priority": ["azure", "aws", "ecmwf"],
        "probe_ensemble_view": "mean",
    }


def test_eps_target_fhs_invariants() -> None:
    synoptic = list(range(0, 361, 6))
    off_cycle = list(range(0, 145, 6))
    assert EPS_MODEL.target_fhs(0) == synoptic
    assert EPS_MODEL.target_fhs(12) == synoptic
    assert EPS_MODEL.target_fhs(6) == off_cycle
    assert EPS_MODEL.target_fhs(18) == off_cycle


def test_eps_alias_and_herbie_request_invariants() -> None:
    assert EPS_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert EPS_MODEL.normalize_var_id("rh700") == "rh700"
    assert EPS_MODEL.normalize_var_id("rh700__mean") == "rh700__mean"
    assert EPS_MODEL.normalize_var_id("700mb_rh") == "rh700"
    assert EPS_MODEL.normalize_var_id("700mb_relative_humidity") == "rh700"
    assert EPS_MODEL.normalize_var_id("tmp2m_anom") == "tmp2m_anom"
    assert EPS_MODEL.normalize_var_id("tmp2m_anom__mean") == "tmp2m_anom__mean"
    assert EPS_MODEL.normalize_var_id("surface_temp_anom") == "tmp2m_anom"
    assert EPS_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert EPS_MODEL.normalize_var_id("2t") == "tmp2m"
    assert EPS_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert EPS_MODEL.normalize_var_id("tmp850__mean") == "tmp850__mean"
    assert EPS_MODEL.normalize_var_id("t850") == "tmp850"
    assert EPS_MODEL.normalize_var_id("tmp850_anom") == "tmp850_anom"
    assert EPS_MODEL.normalize_var_id("tmp850_anom__mean") == "tmp850_anom__mean"
    assert EPS_MODEL.normalize_var_id("t850_anom") == "tmp850_anom"
    assert EPS_MODEL.normalize_var_id("850mb_temp_anom") == "tmp850_anom"
    assert EPS_MODEL.normalize_var_id("hgt500") == "hgt500__mean"
    assert EPS_MODEL.normalize_var_id("z500") == "hgt500__mean"
    assert EPS_MODEL.normalize_var_id("hgt500_anom") == "hgt500_anom"
    assert EPS_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert EPS_MODEL.normalize_var_id("wind10m") == "wspd10m"
    assert EPS_MODEL.normalize_var_id("10u") == "10u__mean"
    assert EPS_MODEL.normalize_var_id("10v") == "10v__mean"
    assert EPS_MODEL.default_ensemble_view("tmp2m") == "mean"
    assert EPS_MODEL.default_ensemble_view("rh700") == "mean"
    assert EPS_MODEL.default_ensemble_view("tmp2m_anom") == "mean"
    assert EPS_MODEL.default_ensemble_view("tmp850_anom") == "mean"
    assert EPS_MODEL.default_ensemble_view("precip_15d_anom") == "mean"
    assert EPS_MODEL.default_ensemble_view("wspd10m") == "mean"
    assert EPS_MODEL.default_ensemble_view("hgt500_anom") == "mean"
    assert EPS_MODEL.supported_ensemble_views("tmp2m") == ["mean"]
    assert EPS_MODEL.supported_ensemble_views("rh700") == ["mean"]
    assert EPS_MODEL.supported_ensemble_views("tmp2m_anom") == ["mean"]
    assert EPS_MODEL.supported_ensemble_views("tmp850_anom") == ["mean"]
    assert EPS_MODEL.supported_ensemble_views("precip_15d_anom") == ["mean"]
    assert EPS_MODEL.supported_ensemble_views("wspd10m") == ["mean"]
    assert EPS_MODEL.supported_ensemble_views("hgt500_anom") == ["mean"]
    assert EPS_MODEL.resolve_runtime_var_id("tmp2m", "mean") == "tmp2m__mean"
    assert EPS_MODEL.resolve_runtime_var_id("rh700", "mean") == "rh700__mean"
    assert EPS_MODEL.resolve_runtime_var_id("tmp2m_anom", "mean") == "tmp2m_anom__mean"
    assert EPS_MODEL.resolve_runtime_var_id("tmp850_anom", "mean") == "tmp850_anom__mean"
    assert EPS_MODEL.resolve_runtime_var_id("precip_15d_anom", "mean") == "precip_15d_anom__mean"
    assert EPS_MODEL.resolve_runtime_var_id("wspd10m", "mean") == "wspd10m__mean"
    assert EPS_MODEL.resolve_runtime_var_id("hgt500_anom", "mean") == "hgt500_anom__mean"

    request = EPS_MODEL.herbie_request(product="enfo", var_key="tmp2m", ensemble_view="mean")
    assert request.model == "ifs"
    assert request.product == "enfo"
    assert request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_pf_mean"

    hgt500_request = EPS_MODEL.herbie_request(product="enfo", var_key="hgt500", ensemble_view="mean")
    assert hgt500_request.model == "ifs"
    assert hgt500_request.product == "enfo"
    assert hgt500_request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_direct_mean_or_pf_mean"

    tmp850_anom_request = EPS_MODEL.herbie_request(product="enfo", var_key="tmp850_anom", ensemble_view="mean")
    assert tmp850_anom_request.model == "ifs"
    assert tmp850_anom_request.product == "enfo"
    assert tmp850_anom_request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_pf_mean"

    tmp850_mean_request = EPS_MODEL.herbie_request(product="enfo", var_key="tmp850__mean", ensemble_view="mean")
    assert tmp850_mean_request.model == "ifs"
    assert tmp850_mean_request.product == "enfo"
    assert tmp850_mean_request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_pf_mean"

    rh700_request = EPS_MODEL.herbie_request(product="enfo", var_key="rh700", ensemble_view="mean")
    assert rh700_request.model == "ifs"
    assert rh700_request.product == "enfo"
    assert rh700_request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_pf_mean"

    u10_request = EPS_MODEL.herbie_request(product="enfo", var_key="10u", ensemble_view="mean")
    assert u10_request.model == "ifs"
    assert u10_request.product == "enfo"
    assert u10_request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_pf_mean"


def test_eps_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = EPS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m", "rh700", "tmp2m_anom", "tmp850_anom", "precip_15d_anom", "hgt500_anom", "wspd10m"}
    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_ensemble_view"] == "mean"
    assert capabilities.canonical_region == "na"
    assert capabilities.grid_meters_by_region == {
        "conus": 18000.0,
        "na": 18000.0,
    }
    auto_schedule_vars = _resolve_vars_to_schedule(EPS_MODEL, [])
    assert "tmp850" not in auto_schedule_vars
    assert "tmp850_anom" in auto_schedule_vars

    from app.services.grid import _PACKING_BY_MODEL_VAR

    assert ("eps", "hgt500__mean") in _PACKING_BY_MODEL_VAR
    assert ("eps", "tmp2m_anom") in _PACKING_BY_MODEL_VAR
    assert ("eps", "tmp2m_anom__mean") in _PACKING_BY_MODEL_VAR
    assert ("eps", "rh700__mean") in _PACKING_BY_MODEL_VAR
    assert ("eps", "tmp850__mean") in _PACKING_BY_MODEL_VAR
    assert ("eps", "tmp850_anom") in _PACKING_BY_MODEL_VAR
    assert ("eps", "tmp850_anom__mean") in _PACKING_BY_MODEL_VAR
    assert ("eps", "precip_15d_anom") in _PACKING_BY_MODEL_VAR
    assert ("eps", "precip_15d_anom__mean") in _PACKING_BY_MODEL_VAR
    assert ("eps", "hgt500_anom") in _PACKING_BY_MODEL_VAR
    assert ("eps", "hgt500_anom__mean") in _PACKING_BY_MODEL_VAR


def test_eps_capabilities_schema_snapshot_invariants() -> None:
    capabilities = EPS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("eps", capabilities)

    assert payload["model_id"] == "eps"
    assert payload["name"] == "EPS"
    assert payload["product"] == "enfo"
    assert payload["ensemble"]["default_view"] == "mean"
    assert payload["ensemble"]["supported_views"] == ["mean"]
    assert "tmp2m__mean" not in payload["variables"]
    assert "rh700__mean" not in payload["variables"]
    assert "rh2m" not in payload["variables"]
    assert "tmp2m_anom__mean" not in payload["variables"]
    assert "tmp850__mean" not in payload["variables"]
    assert "tmp850_anom__mean" not in payload["variables"]
    assert "hgt500__mean" not in payload["variables"]
    assert "hgt500_anom__mean" not in payload["variables"]
    assert "wspd10m__mean" not in payload["variables"]

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["var_key"] == "tmp2m"
    assert tmp2m["display_name"] == "Surface Temp (Mean)"
    assert tmp2m["buildable"] is True
    assert tmp2m["color_map_id"] == "tmp2m"
    assert tmp2m["default_fh"] == 0
    assert tmp2m["group"] == "Temperature"
    assert tmp2m["ensemble"]["default_view"] == "mean"
    assert tmp2m["ensemble"]["supported_views"] == ["mean"]

    rh700 = payload["variables"]["rh700"]
    assert rh700["var_key"] == "rh700"
    assert rh700["display_name"] == "700mb Relative Humidity (Mean)"
    assert rh700["buildable"] is True
    assert rh700["derived"] is False
    assert rh700["kind"] == "continuous"
    assert rh700["units"] == "%"
    assert rh700["color_map_id"] == "rh"
    assert rh700["default_fh"] == 0
    assert rh700["group"] == "Moisture"
    assert rh700["ensemble"]["default_view"] == "mean"
    assert rh700["ensemble"]["supported_views"] == ["mean"]

    tmp2m_anom = payload["variables"]["tmp2m_anom"]
    assert tmp2m_anom["var_key"] == "tmp2m_anom"
    assert tmp2m_anom["display_name"] == "Surface Temperature Anomaly"
    assert tmp2m_anom["buildable"] is True
    assert tmp2m_anom["derived"] is True
    assert tmp2m_anom["derive_strategy_id"] == "anomaly_departure"
    assert tmp2m_anom["color_map_id"] == "tmp2m_anom"
    assert tmp2m_anom["default_fh"] == 0
    assert tmp2m_anom["group"] == "Temperature"
    assert tmp2m_anom["ensemble"]["default_view"] == "mean"
    assert tmp2m_anom["ensemble"]["supported_views"] == ["mean"]

    tmp850_anom = payload["variables"]["tmp850_anom"]
    assert tmp850_anom["var_key"] == "tmp850_anom"
    assert tmp850_anom["display_name"] == "850mb Temperature Anomaly"
    assert tmp850_anom["buildable"] is True
    assert tmp850_anom["derived"] is True
    assert tmp850_anom["derive_strategy_id"] == "anomaly_departure"
    assert tmp850_anom["color_map_id"] == "tmp850_anom"
    assert tmp850_anom["default_fh"] == 0
    assert tmp850_anom["group"] == "Temperature"
    assert tmp850_anom["ensemble"]["default_view"] == "mean"
    assert tmp850_anom["ensemble"]["supported_views"] == ["mean"]

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["var_key"] == "wspd10m"
    assert wspd10m["display_name"] == "10m Wind Speed (Mean)"
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["derive_strategy_id"] == "wspd10m"
    assert wspd10m["color_map_id"] == "wspd10m"
    assert wspd10m["default_fh"] == 0
    assert wspd10m["group"] == "Wind"
    assert wspd10m["ensemble"]["default_view"] == "mean"
    assert wspd10m["ensemble"]["supported_views"] == ["mean"]

    hgt500_anom = payload["variables"]["hgt500_anom"]
    assert hgt500_anom["var_key"] == "hgt500_anom"
    assert hgt500_anom["display_name"] == "500mb Height Anomaly"
    assert hgt500_anom["buildable"] is True
    assert hgt500_anom["derived"] is True
    assert hgt500_anom["derive_strategy_id"] == "anomaly_departure"
    assert hgt500_anom["units"] == "m"
    assert hgt500_anom["color_map_id"] == "hgt500_anom"
    assert hgt500_anom["default_fh"] == 0
    assert hgt500_anom["group"] == "Dynamics"
    assert hgt500_anom["ensemble"]["default_view"] == "mean"
    assert hgt500_anom["ensemble"]["supported_views"] == ["mean"]


def test_eps_runtime_resolution_helpers() -> None:
    assert _resolve_requested_ensemble_view("eps", "tmp2m", None) == "mean"
    assert _resolve_requested_ensemble_view("eps", "rh700", None) == "mean"
    assert _resolve_requested_ensemble_view("eps", "tmp2m_anom", None) == "mean"
    assert _resolve_requested_ensemble_view("eps", "tmp850_anom", None) == "mean"
    assert _resolve_requested_ensemble_view("eps", "precip_15d_anom", None) == "mean"
    assert _resolve_requested_ensemble_view("eps", "wspd10m", None) == "mean"
    assert _resolve_requested_ensemble_view("eps", "hgt500_anom", None) == "mean"
    assert _runtime_var_id_for_request("eps", "tmp2m", "mean") == "tmp2m__mean"
    assert _runtime_var_id_for_request("eps", "rh700", "mean") == "rh700__mean"
    assert _runtime_var_id_for_request("eps", "tmp2m_anom", "mean") == "tmp2m_anom__mean"
    assert _runtime_var_id_for_request("eps", "tmp850_anom", "mean") == "tmp850_anom__mean"
    assert _runtime_var_id_for_request("eps", "precip_15d_anom", "mean") == "precip_15d_anom__mean"
    assert _runtime_var_id_for_request("eps", "wspd10m", "mean") == "wspd10m__mean"
    assert _runtime_var_id_for_request("eps", "hgt500_anom", "mean") == "hgt500_anom__mean"
    try:
        _resolve_requested_ensemble_view("eps", "tmp2m", "spread")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Expected unsupported EPS ensemble view to raise HTTPException")


def test_eps_hgt500_anom_mean_uses_updated_shared_colormap() -> None:
    runtime_var = _runtime_var_id_for_request("eps", "hgt500_anom", "mean")

    assert runtime_var == "hgt500_anom__mean"
    assert variable_color_map_id("eps", runtime_var) == "hgt500_anom"

    spec = get_color_map_spec("hgt500_anom")
    assert len(spec["legend_stops"]) == 70
    assert spec["legend_stops"][0] == (-440.0, "#aaabab")
    assert spec["legend_stops"][-1] == (420.0, "#c5a5c2")


def test_eps_tmp850_anom_uses_mean_tmp850_component_and_era5_baseline() -> None:
    var_spec = EPS_MODEL.get_var("tmp850_anom")
    runtime_spec = EPS_MODEL.get_var("tmp850_anom__mean")
    component_spec = EPS_MODEL.get_var("tmp850__mean")
    public_component_spec = EPS_MODEL.get_var("tmp850")
    assert var_spec is not None
    assert runtime_spec is not None
    assert component_spec is not None
    assert public_component_spec is not None
    assert public_component_spec.primary is False
    assert public_component_spec.derived is False
    assert component_spec.primary is True
    assert component_spec.derived is False
    assert component_spec.selectors.search == [":t:850:pl:"]
    for spec in (var_spec, runtime_spec):
        assert spec.primary is True
        assert spec.derived is True
        assert spec.derive == "anomaly_departure"
        assert spec.kind == "continuous"
        assert spec.units == "F"
        assert spec.selectors.hints["base_component"] == "tmp850__mean"
        assert spec.selectors.hints["base_conversion"] == "c_to_f"
        assert spec.selectors.hints["baseline_field"] == "tmp850"
        assert spec.selectors.hints["baseline_source"] == "era5"
        assert spec.selectors.hints["legacy_baseline_model_family"] == "gefs"
        assert spec.selectors.hints["baseline_region"] == "na"
        assert spec.selectors.hints["baseline_version"] == "v1"
        assert spec.selectors.hints["reference_period"] == "1991-2020"


def test_eps_precip_15d_anom_uses_mean_precip_component_and_era5_baseline() -> None:
    var_spec = EPS_MODEL.get_var("precip_15d_anom")
    runtime_spec = EPS_MODEL.get_var("precip_15d_anom__mean")
    component_spec = EPS_MODEL.get_var("precip_total__mean")
    assert var_spec is not None
    assert runtime_spec is not None
    assert component_spec is not None
    assert component_spec.primary is True
    assert component_spec.derived is False
    for spec in (var_spec, runtime_spec):
        assert spec.primary is True
        assert spec.derived is True
        assert spec.derive == "precip_accum_anomaly_departure"
        assert spec.kind == "continuous"
        assert spec.units == "in"
        assert spec.selectors.hints["base_component"] == "precip_total__mean"
        assert spec.selectors.hints["baseline_field"] == "precip_15d"
        assert spec.selectors.hints["baseline_source"] == "era5"
        assert spec.selectors.hints["baseline_region"] == "na"
        assert spec.selectors.hints["baseline_version"] == "v1"
        assert spec.selectors.hints["reference_period"] == "1991-2020"
        assert spec.selectors.hints["target_fh"] == 360
        assert spec.selectors.hints["accumulation_window_hours"] == 360
