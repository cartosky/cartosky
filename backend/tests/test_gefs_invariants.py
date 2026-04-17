from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _resolve_requested_ensemble_view, _runtime_var_id_for_request, _serialize_model_capability
from app.models.gefs import GEFS_MODEL


def test_gefs_target_fhs_invariants() -> None:
    expected = list(range(0, 361, 6))
    assert GEFS_MODEL.target_fhs(0) == expected
    assert GEFS_MODEL.target_fhs(12) == expected


def test_gefs_alias_and_herbie_request_invariants() -> None:
    assert GEFS_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert GEFS_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert GEFS_MODEL.normalize_var_id("2t") == "tmp2m"
    assert GEFS_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert GEFS_MODEL.normalize_var_id("t850") == "tmp850"
    assert GEFS_MODEL.normalize_var_id("wspd850") == "wspd850"
    assert GEFS_MODEL.normalize_var_id("wind850") == "wspd850"
    assert GEFS_MODEL.normalize_var_id("850mb_heights_winds") == "wspd850"
    assert GEFS_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert GEFS_MODEL.normalize_var_id("wind300") == "wspd300"
    assert GEFS_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"
    assert GEFS_MODEL.normalize_var_id("sbcape") == "sbcape"
    assert GEFS_MODEL.normalize_var_id("snow10") == "snowfall_total"
    assert GEFS_MODEL.normalize_var_id("asnow") == "snowfall_total"
    assert GEFS_MODEL.normalize_var_id("csnow") == "csnow__mean"
    assert GEFS_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert GEFS_MODEL.normalize_var_id("wind10m") == "wspd10m"
    assert GEFS_MODEL.normalize_var_id("10u") == "10u__mean"
    assert GEFS_MODEL.normalize_var_id("10v") == "10v__mean"
    assert GEFS_MODEL.normalize_var_id("pwat") == "pwat"
    assert GEFS_MODEL.normalize_var_id("precipitable_water") == "pwat"
    assert GEFS_MODEL.normalize_var_id("apcp") == "precip_total"
    assert GEFS_MODEL.default_ensemble_view("tmp2m") == "mean"
    assert GEFS_MODEL.default_ensemble_view("tmp850") == "mean"
    assert GEFS_MODEL.default_ensemble_view("wspd850") == "mean"
    assert GEFS_MODEL.default_ensemble_view("wspd300") == "mean"
    assert GEFS_MODEL.default_ensemble_view("sbcape") == "mean"
    assert GEFS_MODEL.default_ensemble_view("snowfall_total") == "mean"
    assert GEFS_MODEL.default_ensemble_view("wspd10m") == "mean"
    assert GEFS_MODEL.default_ensemble_view("pwat") == "mean"
    assert GEFS_MODEL.default_ensemble_view("precip_total") == "mean"
    assert GEFS_MODEL.supported_ensemble_views("tmp2m") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("tmp850") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("wspd850") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("wspd300") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("sbcape") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("snowfall_total") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("wspd10m") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("pwat") == ["mean"]
    assert GEFS_MODEL.supported_ensemble_views("precip_total") == ["mean"]
    assert GEFS_MODEL.resolve_runtime_var_id("tmp2m", "mean") == "tmp2m__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("tmp850", "mean") == "tmp850__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("wspd850", "mean") == "wspd850__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("wspd300", "mean") == "wspd300__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("sbcape", "mean") == "sbcape__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("snowfall_total", "mean") == "snowfall_total__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("wspd10m", "mean") == "wspd10m__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("pwat", "mean") == "pwat__mean"
    assert GEFS_MODEL.resolve_runtime_var_id("precip_total", "mean") == "precip_total__mean"

    request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="tmp2m", ensemble_view="mean")
    assert request.model == "gefs"
    assert request.product == "atmos.5"
    assert request.herbie_kwargs["member"] == "mean"

    tmp850_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="tmp850", ensemble_view="mean")
    assert tmp850_request.model == "gefs"
    assert tmp850_request.product == "atmos.5"
    assert tmp850_request.herbie_kwargs["member"] == "mean"

    wspd850_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="wspd850", ensemble_view="mean")
    assert wspd850_request.model == "gefs"
    assert wspd850_request.product == "atmos.5"
    assert wspd850_request.herbie_kwargs["member"] == "mean"

    wspd300_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="wspd300", ensemble_view="mean")
    assert wspd300_request.model == "gefs"
    assert wspd300_request.product == "atmos.5"
    assert wspd300_request.herbie_kwargs["member"] == "mean"

    sbcape_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="sbcape", ensemble_view="mean")
    assert sbcape_request.model == "gefs"
    assert sbcape_request.product == "atmos.5"
    assert sbcape_request.herbie_kwargs["member"] == "mean"

    snowfall_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="snowfall_total", ensemble_view="mean")
    assert snowfall_request.model == "gefs"
    assert snowfall_request.product == "atmos.5"
    assert snowfall_request.herbie_kwargs["member"] == "mean"

    wspd_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="wspd10m", ensemble_view="mean")
    assert wspd_request.model == "gefs"
    assert wspd_request.product == "atmos.5"
    assert wspd_request.herbie_kwargs["member"] == "mean"

    pwat_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="pwat", ensemble_view="mean")
    assert pwat_request.model == "gefs"
    assert pwat_request.product == "atmos.5"
    assert pwat_request.herbie_kwargs["member"] == "mean"

    precip_request = GEFS_MODEL.herbie_request(product="atmos.5", var_key="precip_total", ensemble_view="mean")
    assert precip_request.model == "gefs"
    assert precip_request.product == "atmos.5"
    assert precip_request.herbie_kwargs["member"] == "mean"


def test_gefs_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = GEFS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"precip_total", "pwat", "sbcape", "snowfall_total", "tmp2m", "tmp850", "wspd10m", "wspd300", "wspd850"}
    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_ensemble_view"] == "mean"
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {"conus": 25000.0}


def test_gefs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = GEFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("gefs", capabilities)

    assert payload["model_id"] == "gefs"
    assert payload["name"] == "GEFS"
    assert payload["product"] == "atmos.5"
    assert payload["ensemble"]["default_view"] == "mean"
    assert payload["ensemble"]["supported_views"] == ["mean"]
    assert "pwat__mean" not in payload["variables"]
    assert "tmp2m__mean" not in payload["variables"]
    assert "tmp850__mean" not in payload["variables"]
    assert "wspd850__mean" not in payload["variables"]
    assert "wspd300__mean" not in payload["variables"]
    assert "sbcape__mean" not in payload["variables"]
    assert "snowfall_total__mean" not in payload["variables"]
    assert "wspd10m__mean" not in payload["variables"]
    assert "precip_total__mean" not in payload["variables"]

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["var_key"] == "tmp2m"
    assert tmp2m["display_name"] == "Surface Temp (Mean)"
    assert tmp2m["buildable"] is True
    assert tmp2m["color_map_id"] == "tmp2m"
    assert tmp2m["ensemble"]["default_view"] == "mean"
    assert tmp2m["ensemble"]["supported_views"] == ["mean"]

    tmp850 = payload["variables"]["tmp850"]
    assert tmp850["var_key"] == "tmp850"
    assert tmp850["display_name"] == "850mb Temp (Mean)"
    assert tmp850["buildable"] is True
    assert tmp850["derived"] is False
    assert tmp850["color_map_id"] == "tmp850"
    assert tmp850["default_fh"] == 0
    assert tmp850["group"] == "Temperature"
    assert tmp850["ensemble"]["default_view"] == "mean"
    assert tmp850["ensemble"]["supported_views"] == ["mean"]

    wspd850 = payload["variables"]["wspd850"]
    assert wspd850["var_key"] == "wspd850"
    assert wspd850["display_name"] == "850mb Heights + Winds (Mean)"
    assert wspd850["buildable"] is True
    assert wspd850["derived"] is True
    assert wspd850["color_map_id"] == "wspd850"
    assert wspd850["derive_strategy_id"] == "wspd10m"
    assert wspd850["default_fh"] == 0
    assert wspd850["group"] == "Wind"
    assert wspd850["ensemble"]["default_view"] == "mean"
    assert wspd850["ensemble"]["supported_views"] == ["mean"]

    wspd300 = payload["variables"]["wspd300"]
    assert wspd300["var_key"] == "wspd300"
    assert wspd300["display_name"] == "300mb Heights + Winds (Mean)"
    assert wspd300["buildable"] is True
    assert wspd300["derived"] is True
    assert wspd300["color_map_id"] == "wspd300"
    assert wspd300["derive_strategy_id"] == "wspd10m"
    assert wspd300["default_fh"] == 0
    assert wspd300["group"] == "Wind"
    assert wspd300["ensemble"]["default_view"] == "mean"
    assert wspd300["ensemble"]["supported_views"] == ["mean"]

    sbcape = payload["variables"]["sbcape"]
    assert sbcape["var_key"] == "sbcape"
    assert sbcape["display_name"] == "Surface-Based CAPE (Mean)"
    assert sbcape["buildable"] is True
    assert sbcape["derived"] is False
    assert sbcape["color_map_id"] == "mlcape"
    assert sbcape["default_fh"] == 0
    assert sbcape["group"] == "Instability"
    assert sbcape["ensemble"]["default_view"] == "mean"
    assert sbcape["ensemble"]["supported_views"] == ["mean"]

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["var_key"] == "snowfall_total"
    assert snowfall_total["display_name"] == "Total Snowfall (10:1) (Mean)"
    assert snowfall_total["buildable"] is True
    assert snowfall_total["derived"] is True
    assert snowfall_total["color_map_id"] == "snowfall_total"
    assert snowfall_total["derive_strategy_id"] == "snowfall_total_10to1_cumulative"
    assert snowfall_total["default_fh"] == 6
    assert snowfall_total["constraints"]["min_fh"] == 6
    assert snowfall_total["group"] == "Precipitation"
    assert snowfall_total["ensemble"]["default_view"] == "mean"
    assert snowfall_total["ensemble"]["supported_views"] == ["mean"]

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["var_key"] == "wspd10m"
    assert wspd10m["display_name"] == "10m Wind Speed (Mean)"
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["color_map_id"] == "wspd10m"
    assert wspd10m["derive_strategy_id"] == "wspd10m"
    assert wspd10m["default_fh"] == 0
    assert wspd10m["group"] == "Wind"
    assert wspd10m["ensemble"]["default_view"] == "mean"
    assert wspd10m["ensemble"]["supported_views"] == ["mean"]

    pwat = payload["variables"]["pwat"]
    assert pwat["var_key"] == "pwat"
    assert pwat["display_name"] == "Precipitable Water (Mean)"
    assert pwat["buildable"] is True
    assert pwat["derived"] is False
    assert pwat["color_map_id"] == "pwat"
    assert pwat["default_fh"] == 0
    assert pwat["group"] == "Moisture"
    assert pwat["ensemble"]["default_view"] == "mean"
    assert pwat["ensemble"]["supported_views"] == ["mean"]

    precip_total = payload["variables"]["precip_total"]
    assert precip_total["var_key"] == "precip_total"
    assert precip_total["display_name"] == "Total Precip (Mean)"
    assert precip_total["buildable"] is True
    assert precip_total["color_map_id"] == "precip_total"
    assert precip_total["derive_strategy_id"] == "precip_total_cumulative"
    assert precip_total["default_fh"] == 6
    assert precip_total["constraints"]["min_fh"] == 6
    assert precip_total["ensemble"]["default_view"] == "mean"
    assert precip_total["ensemble"]["supported_views"] == ["mean"]


def test_gefs_runtime_resolution_helpers() -> None:
    assert _resolve_requested_ensemble_view("gefs", "tmp2m", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "tmp850", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "wspd850", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "wspd300", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "sbcape", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "snowfall_total", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "wspd10m", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "pwat", None) == "mean"
    assert _resolve_requested_ensemble_view("gefs", "precip_total", None) == "mean"
    assert _runtime_var_id_for_request("gefs", "tmp2m", "mean") == "tmp2m__mean"
    assert _runtime_var_id_for_request("gefs", "tmp850", "mean") == "tmp850__mean"
    assert _runtime_var_id_for_request("gefs", "wspd850", "mean") == "wspd850__mean"
    assert _runtime_var_id_for_request("gefs", "wspd300", "mean") == "wspd300__mean"
    assert _runtime_var_id_for_request("gefs", "sbcape", "mean") == "sbcape__mean"
    assert _runtime_var_id_for_request("gefs", "snowfall_total", "mean") == "snowfall_total__mean"
    assert _runtime_var_id_for_request("gefs", "wspd10m", "mean") == "wspd10m__mean"
    assert _runtime_var_id_for_request("gefs", "pwat", "mean") == "pwat__mean"
    assert _runtime_var_id_for_request("gefs", "precip_total", "mean") == "precip_total__mean"
    try:
        _resolve_requested_ensemble_view("gefs", "tmp2m", "spread")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Expected unsupported GEFS ensemble view to raise HTTPException")


def test_gefs_precip_apcp_selector_matches_live_inventory_shape() -> None:
    pattern = GEFS_MODEL.get_var("apcp_step__mean").selectors.search[0]
    assert re.search(pattern, ":APCP:surface:0-6 hour acc fcst:ens mean:") is not None


def test_gefs_sbcape_selector_matches_live_inventory_shape() -> None:
    pattern = GEFS_MODEL.get_var("sbcape__mean").selectors.search[0]
    assert re.search(pattern, ":CAPE:180-0 mb above ground:6 hour fcst:ens mean") is not None


def test_gefs_tmp850_selector_matches_live_inventory_shape() -> None:
    pattern = GEFS_MODEL.get_var("tmp850__mean").selectors.search[0]
    assert re.search(pattern, ":TMP:850 mb:6 hour fcst:ens mean") is not None


def test_gefs_wspd850_inputs_match_live_inventory_shape() -> None:
    hgt_pattern = GEFS_MODEL.get_var("hgt850__mean").selectors.search[0]
    u_pattern = GEFS_MODEL.get_var("u850__mean").selectors.search[0]
    v_pattern = GEFS_MODEL.get_var("v850__mean").selectors.search[0]
    assert re.search(hgt_pattern, ":HGT:850 mb:6 hour fcst:ens mean") is not None
    assert re.search(u_pattern, ":UGRD:850 mb:6 hour fcst:ens mean") is not None
    assert re.search(v_pattern, ":VGRD:850 mb:6 hour fcst:ens mean") is not None


def test_gefs_wspd300_inputs_match_live_inventory_shape() -> None:
    hgt_pattern = GEFS_MODEL.get_var("hgt300__mean").selectors.search[0]
    u_pattern = GEFS_MODEL.get_var("u300__mean").selectors.search[0]
    v_pattern = GEFS_MODEL.get_var("v300__mean").selectors.search[0]
    assert re.search(hgt_pattern, ":HGT:300 mb:6 hour fcst:ens mean") is not None
    assert re.search(u_pattern, ":UGRD:300 mb:6 hour fcst:ens mean") is not None
    assert re.search(v_pattern, ":VGRD:300 mb:6 hour fcst:ens mean") is not None
