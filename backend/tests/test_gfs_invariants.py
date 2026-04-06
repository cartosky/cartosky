from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.gfs import GFS_MODEL


def test_gfs_target_fhs_invariants() -> None:
    expected = list(range(0, 241, 3)) + list(range(246, 385, 6))

    assert GFS_MODEL.target_fhs(0) == expected
    assert GFS_MODEL.target_fhs(6) == expected
    assert GFS_MODEL.target_fhs(12) == expected
    assert GFS_MODEL.target_fhs(18) == expected


def test_gfs_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = GFS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {
        "tmp2m",
        "dp2m",
        "tmp850",
        "mlcape",
        "mucape",
        "wspd10m",
        "wgst10m",
        "precip_ptype",
        "precip_total",
        "snowfall_total",
        "snowfall_kuchera_total",
    }

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 25000.0,
        "pnw": 25000.0,
    }


def test_gfs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = GFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("gfs", capabilities)
    assert payload["constraints"]["supports_sampling"] is True

    precip_ptype = payload["variables"]["precip_ptype"]
    assert precip_ptype["buildable"] is True
    assert precip_ptype["derived"] is True
    assert precip_ptype["derive_strategy_id"] == "precip_ptype_blend"
    assert precip_ptype["units"] == "in/hr"
    assert precip_ptype["order"] == 10

    precip_total = payload["variables"]["precip_total"]
    assert precip_total["buildable"] is True
    assert precip_total["derived"] is True
    assert precip_total["derive_strategy_id"] == "precip_total_cumulative"
    assert precip_total["kind"] == "continuous"
    assert precip_total["constraints"]["min_fh"] == 3
    assert precip_total["display_name"] == "Total Precip"
    assert precip_total["order"] == 6
    assert precip_total["display_resampling_override"] is None

    tmp850 = payload["variables"]["tmp850"]
    assert tmp850["buildable"] is True
    assert tmp850["derived"] is False
    assert tmp850["units"] == "C"
    assert tmp850["display_name"] == "850mb Temp"
    assert tmp850["order"] == 3

    dp2m = payload["variables"]["dp2m"]
    assert dp2m["buildable"] is True
    assert dp2m["derived"] is False
    assert dp2m["units"] == "F"
    assert dp2m["display_name"] == "Surface Dew Point"
    assert dp2m["order"] == 2

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["buildable"] is True
    assert tmp2m["derived"] is False
    assert tmp2m["units"] == "F"
    assert tmp2m["display_name"] == "Surface Temp"
    assert tmp2m["order"] == 1

    mlcape = payload["variables"]["mlcape"]
    assert mlcape["buildable"] is True
    assert mlcape["derived"] is False
    assert mlcape["kind"] == "continuous"
    assert mlcape["units"] == "J/kg"
    assert mlcape["display_name"] == "Mixed-Layer CAPE"
    assert mlcape["group"] == "Instability"
    assert mlcape["color_map_id"] == "mlcape"
    assert mlcape["order"] == 4
    assert mlcape["display_resampling_override"] is None

    mucape = payload["variables"]["mucape"]
    assert mucape["buildable"] is True
    assert mucape["derived"] is False
    assert mucape["kind"] == "continuous"
    assert mucape["units"] == "J/kg"
    assert mucape["display_name"] == "Most-Unstable CAPE"
    assert mucape["group"] == "Instability"
    assert mucape["color_map_id"] == "mlcape"
    assert mucape["order"] == 5
    assert mucape["display_resampling_override"] is None

    wgst10m = payload["variables"]["wgst10m"]
    assert wgst10m["buildable"] is True
    assert wgst10m["derived"] is False
    assert wgst10m["units"] == "mph"
    assert wgst10m["display_name"] == "10m Wind Gust"
    assert wgst10m["order"] == 9

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["units"] == "mph"
    assert wspd10m["display_name"] == "10m Wind Speed"
    assert wspd10m["order"] == 8

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["buildable"] is True
    assert snowfall_total["derived"] is True
    assert snowfall_total["derive_strategy_id"] == "snowfall_total_10to1_cumulative"
    assert snowfall_total["units"] == "in"
    assert snowfall_total["constraints"]["min_fh"] == 3
    assert snowfall_total["default_fh"] == 6
    assert snowfall_total["display_name"] == "Total Snowfall (10:1)"
    assert snowfall_total["order"] == 7
    assert snowfall_total["display_resampling_override"] is None

    snowfall_kuchera_total = payload["variables"]["snowfall_kuchera_total"]
    assert snowfall_kuchera_total["buildable"] is True
    assert snowfall_kuchera_total["derived"] is True
    assert snowfall_kuchera_total["derive_strategy_id"] == "snowfall_kuchera_total_cumulative"
    assert snowfall_kuchera_total["units"] == "in"
    assert snowfall_kuchera_total["constraints"]["min_fh"] == 3
    assert snowfall_kuchera_total["default_fh"] == 6
    assert snowfall_kuchera_total["display_name"] == "Total Snowfall (Kuchera)"
    assert snowfall_kuchera_total["order"] == 13

    qpf6h = payload["variables"]["qpf6h"]
    assert qpf6h["buildable"] is False


def test_gfs_precip_total_aliases_normalize() -> None:
    assert GFS_MODEL.normalize_var_id("apcp") == "precip_total"
    assert GFS_MODEL.normalize_var_id("qpf") == "precip_total"
    assert GFS_MODEL.normalize_var_id("total_precip") == "precip_total"


def test_gfs_temp850_and_gust_aliases_normalize() -> None:
    assert GFS_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert GFS_MODEL.normalize_var_id("t850") == "tmp850"
    assert GFS_MODEL.normalize_var_id("t850mb") == "tmp850"
    assert GFS_MODEL.normalize_var_id("mlcape") == "mlcape"
    assert GFS_MODEL.normalize_var_id("mucape") == "mucape"
    assert GFS_MODEL.normalize_var_id("wgst10m") == "wgst10m"
    assert GFS_MODEL.normalize_var_id("gust") == "wgst10m"
    assert GFS_MODEL.normalize_var_id("gust10m") == "wgst10m"


def test_gfs_mlcape_selector_invariants() -> None:
    var_spec = GFS_MODEL.get_var("mlcape")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.kind == "continuous"
    assert var_spec.units == "J/kg"
    assert var_spec.selectors.search == [":CAPE:90-0 mb above ground:"]
    assert var_spec.selectors.filter_by_keys == {
        "shortName": "cape",
        "typeOfLevel": "pressureFromGroundLayer",
        "topLevel": "0",
        "bottomLevel": "90",
    }


def test_gfs_mucape_selector_invariants() -> None:
    var_spec = GFS_MODEL.get_var("mucape")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.kind == "continuous"
    assert var_spec.units == "J/kg"
    assert var_spec.selectors.search == [":CAPE:255-0 mb above ground:"]
    assert var_spec.selectors.filter_by_keys == {
        "shortName": "cape",
        "typeOfLevel": "pressureFromGroundLayer",
        "topLevel": "0",
        "bottomLevel": "255",
    }


def test_gfs_dewpoint_and_snow_aliases_normalize() -> None:
    assert GFS_MODEL.normalize_var_id("dp2m") == "dp2m"
    assert GFS_MODEL.normalize_var_id("d2m") == "dp2m"
    assert GFS_MODEL.normalize_var_id("2d") == "dp2m"
    assert GFS_MODEL.normalize_var_id("dpt2m") == "dp2m"

    assert GFS_MODEL.normalize_var_id("snowfall_total") == "snowfall_total"
    assert GFS_MODEL.normalize_var_id("snowfall_kuchera_total") == "snowfall_kuchera_total"
    assert GFS_MODEL.normalize_var_id("asnow") == "snowfall_total"
    assert GFS_MODEL.normalize_var_id("snow10") == "snowfall_total"


def test_gfs_snowfall_total_search_patterns_include_upstream_fallback() -> None:
    var_spec = GFS_MODEL.get_var("snowfall_total")
    assert var_spec is not None
    assert var_spec.selectors.search == []
    assert var_spec.derived is True
    assert var_spec.derive == "snowfall_total_10to1_cumulative"
    assert var_spec.selectors.hints["step_transition_fh"] == "240"
    assert var_spec.selectors.hints["step_hours_after_fh"] == "6"
    assert var_spec.selectors.hints["snow_mask_threshold"] == "0.5"
    assert var_spec.selectors.hints["snow_interval_sample_mode"] == "three_point"
