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
        "tmp2m_anom",
        "dp2m",
        "tmp850",
        "tmp850_anom",
        "hgt500_anom",
        "wspd850",
        "wspd300",
        "vort500",
        "sbcape",
        "mlcape",
        "mucape",
        "pwat",
        "wspd10m",
        "wgst10m",
        "ptype_intensity",
        "precip_total",
        "snowfall_total",
        "snowfall_kuchera_total",
    }

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "na"
    assert capabilities.grid_meters_by_region == {
        "conus": 25000.0,
        "na": 25000.0,
        "pnw": 25000.0,
    }

    from app.services.grid import _PACKING_BY_MODEL_VAR

    assert ("gfs", "tmp2m_anom") in _PACKING_BY_MODEL_VAR
    assert ("gfs", "tmp850_anom") in _PACKING_BY_MODEL_VAR
    assert ("gfs", "hgt500_anom") in _PACKING_BY_MODEL_VAR


def test_gfs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = GFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("gfs", capabilities)
    assert payload["constraints"]["supports_sampling"] is True

    ptype_intensity = payload["variables"]["ptype_intensity"]
    assert ptype_intensity["buildable"] is True
    assert ptype_intensity["derived"] is True
    assert ptype_intensity["derive_strategy_id"] == "ptype_intensity_gfs"
    assert ptype_intensity["units"] == "in/hr"
    assert ptype_intensity["kind"] == "indexed"
    assert ptype_intensity["color_map_id"] == "ptype_intensity"
    assert ptype_intensity["order"] == 15

    precip_total = payload["variables"]["precip_total"]
    assert precip_total["buildable"] is True
    assert precip_total["derived"] is True
    assert precip_total["derive_strategy_id"] == "precip_total_cumulative"
    assert precip_total["kind"] == "continuous"
    assert precip_total["constraints"]["min_fh"] == 3
    assert precip_total["display_name"] == "Total Precip"
    assert precip_total["order"] == 10
    assert precip_total["display_resampling_override"] is None

    tmp850 = payload["variables"]["tmp850"]
    assert tmp850["buildable"] is True
    assert tmp850["derived"] is False
    assert tmp850["units"] == "C"
    assert tmp850["display_name"] == "850mb Temp"
    assert tmp850["order"] == 3

    tmp850_anom = payload["variables"]["tmp850_anom"]
    assert tmp850_anom["buildable"] is True
    assert tmp850_anom["derived"] is True
    assert tmp850_anom["derive_strategy_id"] == "anomaly_departure"
    assert tmp850_anom["kind"] == "continuous"
    assert tmp850_anom["units"] == "F"
    assert tmp850_anom["display_name"] == "850mb Temperature Anomaly"
    assert tmp850_anom["group"] == "Temperature"
    assert tmp850_anom["color_map_id"] == "tmp850_anom"
    assert tmp850_anom["order"] == 3.5

    tmp2m_anom = payload["variables"]["tmp2m_anom"]
    assert tmp2m_anom["buildable"] is True
    assert tmp2m_anom["derived"] is True
    assert tmp2m_anom["derive_strategy_id"] == "anomaly_departure"
    assert tmp2m_anom["kind"] == "continuous"
    assert tmp2m_anom["units"] == "F"
    assert tmp2m_anom["display_name"] == "Surface Temperature Anomaly"
    assert tmp2m_anom["group"] == "Temperature"
    assert tmp2m_anom["color_map_id"] == "tmp2m_anom"
    assert tmp2m_anom["order"] == 2

    hgt500_anom = payload["variables"]["hgt500_anom"]
    assert hgt500_anom["buildable"] is True
    assert hgt500_anom["derived"] is True
    assert hgt500_anom["derive_strategy_id"] == "anomaly_departure"
    assert hgt500_anom["kind"] == "continuous"
    assert hgt500_anom["units"] == "dam"
    assert hgt500_anom["display_name"] == "500mb Height Anomaly"
    assert hgt500_anom["group"] == "Dynamics"
    assert hgt500_anom["color_map_id"] == "hgt500_anom"
    assert hgt500_anom["order"] == 5
    assert hgt500_anom["display_resampling_override"] == "bilinear"

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

    vort500 = payload["variables"]["vort500"]
    assert vort500["buildable"] is True
    assert vort500["derived"] is False
    assert vort500["kind"] == "continuous"
    assert vort500["units"] == "10^-5 s^-1"
    assert vort500["display_name"] == "500mb Heights + Vorticity"
    assert vort500["group"] == "Dynamics"
    assert vort500["color_map_id"] == "vort500"
    assert vort500["order"] == 5
    assert vort500["display_resampling_override"] is None

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

    wgst10m = payload["variables"]["wgst10m"]
    assert wgst10m["buildable"] is True
    assert wgst10m["derived"] is False
    assert wgst10m["units"] == "mph"
    assert wgst10m["display_name"] == "10m Wind Gust"
    assert wgst10m["order"] == 13

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["units"] == "mph"
    assert wspd10m["display_name"] == "10m Wind Speed"
    assert wspd10m["order"] == 12

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["buildable"] is True
    assert snowfall_total["derived"] is True
    assert snowfall_total["derive_strategy_id"] == "snowfall_total_10to1_cumulative"
    assert snowfall_total["units"] == "in"
    assert snowfall_total["constraints"]["min_fh"] == 3
    assert snowfall_total["default_fh"] == 6
    assert snowfall_total["display_name"] == "Total Snowfall (10:1)"
    assert snowfall_total["order"] == 11
    assert snowfall_total["display_resampling_override"] is None

    snowfall_kuchera_total = payload["variables"]["snowfall_kuchera_total"]
    assert snowfall_kuchera_total["buildable"] is True
    assert snowfall_kuchera_total["derived"] is True
    assert snowfall_kuchera_total["derive_strategy_id"] == "snowfall_kuchera_total_cumulative"
    assert snowfall_kuchera_total["units"] == "in"
    assert snowfall_kuchera_total["constraints"]["min_fh"] == 3
    assert snowfall_kuchera_total["default_fh"] == 6
    assert snowfall_kuchera_total["display_name"] == "Total Snowfall (Kuchera)"
    assert snowfall_kuchera_total["order"] == 17

    qpf6h = payload["variables"]["qpf6h"]
    assert qpf6h["buildable"] is False


def test_gfs_precip_total_aliases_normalize() -> None:
    assert GFS_MODEL.normalize_var_id("apcp") == "precip_total"
    assert GFS_MODEL.normalize_var_id("qpf") == "precip_total"
    assert GFS_MODEL.normalize_var_id("total_precip") == "precip_total"
    assert GFS_MODEL.normalize_var_id("pwat") == "pwat"
    assert GFS_MODEL.normalize_var_id("precipitable_water") == "pwat"


def test_gfs_temp850_and_gust_aliases_normalize() -> None:
    assert GFS_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert GFS_MODEL.normalize_var_id("tmp850_anom") == "tmp850_anom"
    assert GFS_MODEL.normalize_var_id("t850_anom") == "tmp850_anom"
    assert GFS_MODEL.normalize_var_id("850mb_temp_anom") == "tmp850_anom"
    assert GFS_MODEL.normalize_var_id("tmp2m_anom") == "tmp2m_anom"
    assert GFS_MODEL.normalize_var_id("surface_temp_anom") == "tmp2m_anom"
    assert GFS_MODEL.normalize_var_id("t850") == "tmp850"
    assert GFS_MODEL.normalize_var_id("t850mb") == "tmp850"
    assert GFS_MODEL.normalize_var_id("wspd850") == "wspd850"
    assert GFS_MODEL.normalize_var_id("850mb_heights_winds") == "wspd850"
    assert GFS_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert GFS_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"
    assert GFS_MODEL.normalize_var_id("sbcape") == "sbcape"
    assert GFS_MODEL.normalize_var_id("mlcape") == "mlcape"
    assert GFS_MODEL.normalize_var_id("mucape") == "mucape"
    assert GFS_MODEL.normalize_var_id("wgst10m") == "wgst10m"
    assert GFS_MODEL.normalize_var_id("gust") == "wgst10m"
    assert GFS_MODEL.normalize_var_id("gust10m") == "wgst10m"


def test_gfs_hgt500_anom_uses_hgt500_component_and_height_contours() -> None:
    var_spec = GFS_MODEL.get_var("hgt500_anom")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.derived is True
    assert var_spec.derive == "anomaly_departure"
    assert var_spec.kind == "continuous"
    assert var_spec.units == "dam"
    assert var_spec.selectors.hints["base_component"] == "hgt500"
    assert var_spec.selectors.hints["baseline_field"] == "hgt500"
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500"
    assert var_spec.selectors.hints["contour_conversion"] == "m_to_dam"


def test_gfs_tmp2m_anom_uses_tmp2m_component_and_era5_baseline() -> None:
    var_spec = GFS_MODEL.get_var("tmp2m_anom")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.derived is True
    assert var_spec.derive == "anomaly_departure"
    assert var_spec.kind == "continuous"
    assert var_spec.units == "F"
    assert var_spec.selectors.hints["base_component"] == "tmp2m"
    assert var_spec.selectors.hints["baseline_field"] == "tmp2m"
    assert var_spec.selectors.hints["baseline_source"] == "era5"
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["baseline_version"] == "v1"
    assert var_spec.selectors.hints["reference_period"] == "1991-2020"


def test_gfs_tmp850_anom_uses_tmp850_component_and_era5_baseline() -> None:
    var_spec = GFS_MODEL.get_var("tmp850_anom")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.derived is True
    assert var_spec.derive == "anomaly_departure"
    assert var_spec.kind == "continuous"
    assert var_spec.units == "F"
    assert var_spec.selectors.hints["base_component"] == "tmp850"
    assert var_spec.selectors.hints["base_conversion"] == "c_to_f"
    assert var_spec.selectors.hints["baseline_field"] == "tmp850"
    assert var_spec.selectors.hints["baseline_source"] == "era5"
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["baseline_version"] == "v1"
    assert var_spec.selectors.hints["reference_period"] == "1991-2020"


def test_gfs_sbcape_selector_invariants() -> None:
    var_spec = GFS_MODEL.get_var("sbcape")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.kind == "continuous"
    assert var_spec.units == "J/kg"
    assert var_spec.selectors.search == [":CAPE:surface:"]
    assert var_spec.selectors.filter_by_keys == {
        "shortName": "cape",
        "typeOfLevel": "surface",
    }


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


def test_gfs_pwat_selector_invariants() -> None:
    var_spec = GFS_MODEL.get_var("pwat")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.derived is False
    assert var_spec.kind == "continuous"
    assert var_spec.units == "in"
    assert var_spec.selectors.search == [":PWAT:entire atmosphere (considered as a single layer):"]
    assert var_spec.selectors.filter_by_keys == {
        "shortName": "pwat",
        "typeOfLevel": "atmosphereSingleLayer",
    }


def test_gfs_wspd850_uses_850mb_components_and_height_contours() -> None:
    var_spec = GFS_MODEL.get_var("wspd850")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.derived is True
    assert var_spec.derive == "wspd10m"
    assert var_spec.kind == "continuous"
    assert var_spec.units == "kt"
    assert var_spec.selectors.search == []
    assert var_spec.selectors.hints["u_component"] == "u850"
    assert var_spec.selectors.hints["v_component"] == "v850"
    assert var_spec.selectors.hints["contour_component"] == "hgt850"
    assert var_spec.selectors.hints["contour_interval"] == "30"
    assert var_spec.selectors.hints["contour_key"] == "height_850mb"


def test_gfs_wspd300_uses_300mb_components_and_height_contours() -> None:
    var_spec = GFS_MODEL.get_var("wspd300")
    assert var_spec is not None
    assert var_spec.primary is True
    assert var_spec.derived is True
    assert var_spec.derive == "wspd10m"
    assert var_spec.kind == "continuous"
    assert var_spec.units == "kt"
    assert var_spec.selectors.search == []
    assert var_spec.selectors.hints["u_component"] == "u300"
    assert var_spec.selectors.hints["v_component"] == "v300"
    assert var_spec.selectors.hints["contour_component"] == "hgt300"
    assert var_spec.selectors.hints["contour_interval"] == "120"
    assert var_spec.selectors.hints["contour_key"] == "height_300mb"


def test_gfs_850mb_component_selectors_invariants() -> None:
    hgt_spec = GFS_MODEL.get_var("hgt850")
    assert hgt_spec is not None
    assert hgt_spec.selectors.search == [":HGT:850 mb:"]
    assert hgt_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }

    u_spec = GFS_MODEL.get_var("u850")
    assert u_spec is not None
    assert u_spec.selectors.search == [":UGRD:850 mb:"]
    assert u_spec.selectors.filter_by_keys == {
        "shortName": "ugrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }

    v_spec = GFS_MODEL.get_var("v850")
    assert v_spec is not None
    assert v_spec.selectors.search == [":VGRD:850 mb:"]
    assert v_spec.selectors.filter_by_keys == {
        "shortName": "vgrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }


def test_gfs_300mb_component_selectors_invariants() -> None:
    hgt_spec = GFS_MODEL.get_var("hgt300")
    assert hgt_spec is not None
    assert hgt_spec.selectors.search == [":HGT:300 mb:"]
    assert hgt_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }

    u_spec = GFS_MODEL.get_var("u300")
    assert u_spec is not None
    assert u_spec.selectors.search == [":UGRD:300 mb:"]
    assert u_spec.selectors.filter_by_keys == {
        "shortName": "ugrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }

    v_spec = GFS_MODEL.get_var("v300")
    assert v_spec is not None
    assert v_spec.selectors.search == [":VGRD:300 mb:"]
    assert v_spec.selectors.filter_by_keys == {
        "shortName": "vgrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
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


def test_gfs_precip_total_mixed_cadence_invariants() -> None:
    var_spec = GFS_MODEL.get_var("precip_total")
    assert var_spec is not None
    assert var_spec.derived is True
    assert var_spec.derive == "precip_total_cumulative"
    assert var_spec.selectors.hints["step_hours"] == "3"
    assert var_spec.selectors.hints["step_transition_fh"] == "240"
    assert var_spec.selectors.hints["step_hours_after_fh"] == "6"


def test_gfs_kuchera_profile_mode_invariants() -> None:
    var_spec = GFS_MODEL.get_var("snowfall_kuchera_total")
    assert var_spec is not None
    assert var_spec.derived is True
    assert var_spec.derive == "snowfall_kuchera_total_cumulative"
    assert var_spec.selectors.hints["kuchera_profile_mode"] == "simplified"
    assert var_spec.selectors.hints["kuchera_levels_hpa"] == "925,850,700,600"
    assert var_spec.selectors.hints["kuchera_require_rh"] == "false"
    assert var_spec.selectors.hints["kuchera_min_levels"] == "4"
