from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.main import _serialize_model_capability
from app.models.nam import NAM_MODEL


def test_nam_target_fhs_invariants() -> None:
    assert NAM_MODEL.target_fhs(0) == list(range(0, 61))
    assert NAM_MODEL.target_fhs(6) == list(range(0, 61))
    assert NAM_MODEL.target_fhs(12) == list(range(0, 61))
    assert NAM_MODEL.target_fhs(18) == list(range(0, 61))


def test_nam_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = NAM_MODEL.capabilities
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
        "wspd850",
        "wspd300",
        "vort500",
        "sbcape",
        "mlcape",
        "mucape",
        "pwat",
        "wspd10m",
        "wgst10m",
        "precip_total",
        "snowfall_total",
        "snowfall_kuchera_total",
        "radar_ptype",
    }

    assert capabilities.ui_defaults["default_var_key"] == "radar_ptype"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 3000.0,
        "pnw": 3000.0,
    }


def test_nam_capabilities_schema_snapshot_invariants() -> None:
    capabilities = NAM_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("nam", capabilities)

    assert payload["model_id"] == "nam"
    assert payload["name"] == "NAM"
    assert payload["product"] == "conusnest.hiresf"
    assert payload["canonical_region"] == "conus"
    assert payload["constraints"]["supports_sampling"] is True

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["buildable"] is True
    assert tmp2m["derived"] is False
    assert tmp2m["kind"] == "continuous"
    assert tmp2m["units"] == "F"
    assert tmp2m["display_name"] == "Surface Temp"
    assert tmp2m["order"] == 1

    dp2m = payload["variables"]["dp2m"]
    assert dp2m["buildable"] is True
    assert dp2m["derived"] is False
    assert dp2m["kind"] == "continuous"
    assert dp2m["units"] == "F"
    assert dp2m["display_name"] == "Surface Dew Point"
    assert dp2m["order"] == 2

    tmp850 = payload["variables"]["tmp850"]
    assert tmp850["buildable"] is True
    assert tmp850["derived"] is False
    assert tmp850["kind"] == "continuous"
    assert tmp850["units"] == "C"
    assert tmp850["display_name"] == "850mb Temp"
    assert tmp850["order"] == 3

    wspd850 = payload["variables"]["wspd850"]
    assert wspd850["buildable"] is True
    assert wspd850["derived"] is True
    assert wspd850["derive_strategy_id"] == "wspd10m"
    assert wspd850["kind"] == "continuous"
    assert wspd850["units"] == "kt"
    assert wspd850["display_name"] == "850mb Heights + Winds"
    assert wspd850["group"] == "Wind"
    assert wspd850["color_map_id"] == "wspd850"
    assert wspd850["order"] == 4
    assert wspd850["display_resampling_override"] is None

    wspd300 = payload["variables"]["wspd300"]
    assert wspd300["buildable"] is True
    assert wspd300["derived"] is True
    assert wspd300["derive_strategy_id"] == "wspd10m"
    assert wspd300["kind"] == "continuous"
    assert wspd300["units"] == "kt"
    assert wspd300["display_name"] == "300mb Heights + Winds"
    assert wspd300["group"] == "Wind"
    assert wspd300["color_map_id"] == "wspd300"
    assert wspd300["order"] == 999
    assert wspd300["display_resampling_override"] is None

    sbcape = payload["variables"]["sbcape"]
    assert sbcape["buildable"] is True
    assert sbcape["derived"] is False
    assert sbcape["kind"] == "continuous"
    assert sbcape["units"] == "J/kg"
    assert sbcape["display_name"] == "Surface-Based CAPE"
    assert sbcape["group"] == "Instability"
    assert sbcape["color_map_id"] == "mlcape"
    assert sbcape["order"] == 6
    assert sbcape["display_resampling_override"] is None

    mlcape = payload["variables"]["mlcape"]
    assert mlcape["buildable"] is True
    assert mlcape["derived"] is False
    assert mlcape["kind"] == "continuous"
    assert mlcape["units"] == "J/kg"
    assert mlcape["display_name"] == "Mixed-Layer CAPE"
    assert mlcape["group"] == "Instability"
    assert mlcape["color_map_id"] == "mlcape"
    assert mlcape["order"] == 7
    assert mlcape["display_resampling_override"] is None

    mucape = payload["variables"]["mucape"]
    assert mucape["buildable"] is True
    assert mucape["derived"] is False
    assert mucape["kind"] == "continuous"
    assert mucape["units"] == "J/kg"
    assert mucape["display_name"] == "Most-Unstable CAPE"
    assert mucape["group"] == "Instability"
    assert mucape["color_map_id"] == "mlcape"
    assert mucape["order"] == 8
    assert mucape["display_resampling_override"] is None

    pwat = payload["variables"]["pwat"]
    assert pwat["buildable"] is True
    assert pwat["derived"] is False
    assert pwat["kind"] == "continuous"
    assert pwat["units"] == "in"
    assert pwat["display_name"] == "Precipitable Water"
    assert pwat["group"] == "Moisture"
    assert pwat["color_map_id"] == "pwat"
    assert pwat["order"] == 9
    assert pwat["display_resampling_override"] is None

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["derive_strategy_id"] == "wspd10m"
    assert wspd10m["kind"] == "continuous"
    assert wspd10m["units"] == "mph"
    assert wspd10m["display_name"] == "10m Wind Speed"
    assert wspd10m["order"] == 12

    wgst10m = payload["variables"]["wgst10m"]
    assert wgst10m["buildable"] is True
    assert wgst10m["derived"] is False
    assert wgst10m["kind"] == "continuous"
    assert wgst10m["units"] == "mph"
    assert wgst10m["display_name"] == "10m Wind Gust"
    assert wgst10m["order"] == 13

    precip_total = payload["variables"]["precip_total"]
    assert precip_total["buildable"] is True
    assert precip_total["derived"] is True
    assert precip_total["derive_strategy_id"] == "precip_total_cumulative"
    assert precip_total["kind"] == "continuous"
    assert precip_total["units"] == "in"
    assert precip_total["default_fh"] == 1
    assert precip_total["constraints"] == {"min_fh": 1}
    assert precip_total["display_name"] == "Total Precip"
    assert precip_total["order"] == 10
    assert precip_total["display_resampling_override"] is None

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["buildable"] is True
    assert snowfall_total["derived"] is True
    assert snowfall_total["derive_strategy_id"] == "snowfall_total_10to1_cumulative"
    assert snowfall_total["kind"] == "continuous"
    assert snowfall_total["units"] == "in"
    assert snowfall_total["default_fh"] == 1
    assert snowfall_total["constraints"] == {"min_fh": 1}
    assert snowfall_total["display_name"] == "Total Snowfall (10:1)"
    assert snowfall_total["order"] == 11
    assert snowfall_total["display_resampling_override"] is None

    snowfall_kuchera_total = payload["variables"]["snowfall_kuchera_total"]
    assert snowfall_kuchera_total["buildable"] is True
    assert snowfall_kuchera_total["derived"] is True
    assert snowfall_kuchera_total["derive_strategy_id"] == "snowfall_kuchera_total_cumulative"
    assert snowfall_kuchera_total["kind"] == "continuous"
    assert snowfall_kuchera_total["units"] == "in"
    assert snowfall_kuchera_total["default_fh"] == 1
    assert snowfall_kuchera_total["constraints"] == {"min_fh": 1}
    assert snowfall_kuchera_total["display_name"] == "Total Snowfall (Kuchera)"
    assert snowfall_kuchera_total["order"] == 14

    radar_ptype = payload["variables"]["radar_ptype"]
    assert radar_ptype["buildable"] is True
    assert radar_ptype["derived"] is True
    assert radar_ptype["derive_strategy_id"] == "radar_ptype_combo"
    assert radar_ptype["kind"] == "discrete"
    assert radar_ptype["units"] == "dBZ"
    assert radar_ptype["default_fh"] == 1
    assert radar_ptype["display_name"] == "Composite Reflectivity + Ptype"
    assert radar_ptype["order"] == 0
    radar_ptype_spec = NAM_MODEL.get_var("radar_ptype")
    assert radar_ptype_spec is not None
    assert radar_ptype_spec.selectors.hints["min_visible_dbz"] == "10.0"
    assert radar_ptype_spec.selectors.hints["min_mask_value"] == "0.5"
    assert radar_ptype_spec.selectors.hints["despeckle_min_neighbors"] == "2"

    u10 = payload["variables"]["10u"]
    assert u10["buildable"] is False

    v10 = payload["variables"]["10v"]
    assert v10["buildable"] is False

    si10 = payload["variables"]["10si"]
    assert si10["buildable"] is False

    apcp_step = payload["variables"]["apcp_step"]
    assert apcp_step["buildable"] is False

    refc = payload["variables"]["refc"]
    assert refc["buildable"] is False

    crain = payload["variables"]["crain"]
    assert crain["buildable"] is False

    csnow = payload["variables"]["csnow"]
    assert csnow["buildable"] is False

    cicep = payload["variables"]["cicep"]
    assert cicep["buildable"] is False

    cfrzr = payload["variables"]["cfrzr"]
    assert cfrzr["buildable"] is False


def test_nam_aliases_normalize() -> None:
    assert NAM_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert NAM_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert NAM_MODEL.normalize_var_id("2t") == "tmp2m"
    assert NAM_MODEL.normalize_var_id("dp2m") == "dp2m"
    assert NAM_MODEL.normalize_var_id("d2m") == "dp2m"
    assert NAM_MODEL.normalize_var_id("2d") == "dp2m"
    assert NAM_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert NAM_MODEL.normalize_var_id("t850") == "tmp850"
    assert NAM_MODEL.normalize_var_id("temp850") == "tmp850"
    assert NAM_MODEL.normalize_var_id("wspd850") == "wspd850"
    assert NAM_MODEL.normalize_var_id("850mb_heights_winds") == "wspd850"
    assert NAM_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert NAM_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"
    assert NAM_MODEL.normalize_var_id("sbcape") == "sbcape"
    assert NAM_MODEL.normalize_var_id("mlcape") == "mlcape"
    assert NAM_MODEL.normalize_var_id("mucape") == "mucape"
    assert NAM_MODEL.normalize_var_id("pwat") == "pwat"
    assert NAM_MODEL.normalize_var_id("precipitable_water") == "pwat"
    assert NAM_MODEL.normalize_var_id("wgst10m") == "wgst10m"
    assert NAM_MODEL.normalize_var_id("gust") == "wgst10m"
    assert NAM_MODEL.normalize_var_id("gust10m") == "wgst10m"
    assert NAM_MODEL.normalize_var_id("precip_total") == "precip_total"
    assert NAM_MODEL.normalize_var_id("apcp") == "precip_total"
    assert NAM_MODEL.normalize_var_id("qpf") == "precip_total"
    assert NAM_MODEL.normalize_var_id("snowfall_total") == "snowfall_total"
    assert NAM_MODEL.normalize_var_id("snowfall_kuchera_total") == "snowfall_kuchera_total"
    assert NAM_MODEL.normalize_var_id("asnow") == "snowfall_total"
    assert NAM_MODEL.normalize_var_id("snow10") == "snowfall_total"
    assert NAM_MODEL.normalize_var_id("refc") == "refc"
    assert NAM_MODEL.normalize_var_id("cref") == "refc"
    assert NAM_MODEL.normalize_var_id("radar_ptype") == "radar_ptype"
    assert NAM_MODEL.normalize_var_id("radarptype") == "radar_ptype"
    assert NAM_MODEL.normalize_var_id("u10") == "10u"
    assert NAM_MODEL.normalize_var_id("v10") == "10v"
    assert NAM_MODEL.normalize_var_id("10si") == "10si"
    assert NAM_MODEL.normalize_var_id("wind10m") == "10si"
    assert NAM_MODEL.normalize_var_id("wspd10m") == "wspd10m"


def test_nam_kuchera_profile_mode_invariants() -> None:
    var_spec = NAM_MODEL.get_var("snowfall_kuchera_total")
    assert var_spec is not None
    assert var_spec.derived is True
    assert var_spec.derive == "snowfall_kuchera_total_cumulative"
    assert var_spec.selectors.hints["kuchera_profile_mode"] == "simplified"
    assert var_spec.selectors.hints["kuchera_levels_hpa"] == "925,850,700,600"
    assert var_spec.selectors.hints["kuchera_require_rh"] == "false"
    assert var_spec.selectors.hints["kuchera_min_levels"] == "4"


def test_nam_sbcape_selector_invariants() -> None:
    sbcape_spec = NAM_MODEL.get_var("sbcape")
    assert sbcape_spec is not None
    assert sbcape_spec.primary is True
    assert sbcape_spec.derived is False
    assert sbcape_spec.kind == "continuous"
    assert sbcape_spec.units == "J/kg"
    assert sbcape_spec.selectors.search == [":CAPE:surface:"]
    assert sbcape_spec.selectors.filter_by_keys == {
        "shortName": "cape",
        "typeOfLevel": "surface",
    }
    assert sbcape_spec.selectors.hints["upstream_var"] == "sbcape"
    assert sbcape_spec.selectors.hints["cape_layer"] == "surface"


def test_nam_mlcape_selector_invariants() -> None:
    mlcape_spec = NAM_MODEL.get_var("mlcape")
    assert mlcape_spec is not None
    assert mlcape_spec.primary is True
    assert mlcape_spec.derived is False
    assert mlcape_spec.kind == "continuous"
    assert mlcape_spec.units == "J/kg"
    assert mlcape_spec.selectors.search == [":CAPE:90-0 mb above ground:"]
    assert mlcape_spec.selectors.filter_by_keys == {
        "shortName": "cape",
        "typeOfLevel": "pressureFromGroundLayer",
        "topLevel": "0",
        "bottomLevel": "90",
    }
    assert mlcape_spec.selectors.hints["upstream_var"] == "mlcape"
    assert mlcape_spec.selectors.hints["cape_layer"] == "90-0 mb above ground"


def test_nam_mucape_selector_invariants() -> None:
    mucape_spec = NAM_MODEL.get_var("mucape")
    assert mucape_spec is not None
    assert mucape_spec.primary is True
    assert mucape_spec.derived is False
    assert mucape_spec.kind == "continuous"
    assert mucape_spec.units == "J/kg"
    assert mucape_spec.selectors.search == [":CAPE:255-0 mb above ground:"]
    assert mucape_spec.selectors.filter_by_keys == {
        "shortName": "cape",
        "typeOfLevel": "pressureFromGroundLayer",
        "topLevel": "0",
        "bottomLevel": "255",
    }
    assert mucape_spec.selectors.hints["upstream_var"] == "mucape"
    assert mucape_spec.selectors.hints["cape_layer"] == "255-0 mb above ground"


def test_nam_wspd10m_uses_vector_components_only() -> None:
    wspd_spec = NAM_MODEL.get_var("wspd10m")
    assert wspd_spec is not None
    assert wspd_spec.derived is True
    assert wspd_spec.derive == "wspd10m"
    assert wspd_spec.selectors.hints["u_component"] == "10u"
    assert wspd_spec.selectors.hints["v_component"] == "10v"
    assert "speed_component" not in wspd_spec.selectors.hints


def test_nam_wspd850_uses_850mb_components_and_height_contours() -> None:
    wspd_spec = NAM_MODEL.get_var("wspd850")
    assert wspd_spec is not None
    assert wspd_spec.derived is True
    assert wspd_spec.derive == "wspd10m"
    assert wspd_spec.selectors.hints["u_component"] == "u850"
    assert wspd_spec.selectors.hints["v_component"] == "v850"
    assert wspd_spec.selectors.hints["contour_component"] == "hgt850"
    assert wspd_spec.selectors.hints["contour_key"] == "height_850mb"


def test_nam_wspd300_uses_300mb_components_and_height_contours() -> None:
    wspd_spec = NAM_MODEL.get_var("wspd300")
    assert wspd_spec is not None
    assert wspd_spec.derived is True
    assert wspd_spec.derive == "wspd10m"
    assert wspd_spec.selectors.hints["u_component"] == "u300"
    assert wspd_spec.selectors.hints["v_component"] == "v300"
    assert wspd_spec.selectors.hints["contour_component"] == "hgt300"
    assert wspd_spec.selectors.hints["contour_key"] == "height_300mb"


def test_nam_pwat_selector_invariants() -> None:
    pwat_spec = NAM_MODEL.get_var("pwat")
    assert pwat_spec is not None
    assert pwat_spec.primary is True
    assert pwat_spec.derived is False
    assert pwat_spec.kind == "continuous"
    assert pwat_spec.units == "in"
    assert pwat_spec.selectors.search == [":PWAT:entire atmosphere (considered as a single layer):"]
    assert pwat_spec.selectors.filter_by_keys == {
        "shortName": "pwat",
        "typeOfLevel": "atmosphereSingleLayer",
    }
