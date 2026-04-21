from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from datetime import datetime, timezone

from app.models.ecmwf import ECMWF_MODEL


def test_ecmwf_run_discovery_invariants() -> None:
    capabilities = ECMWF_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 3],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "allow_grib_without_idx": True,
        "source_priority": ["azure", "aws", "ecmwf"],
    }


def test_ecmwf_target_fhs_invariants() -> None:
    synoptic_expected = list(range(0, 145, 3)) + list(range(150, 361, 6))
    off_cycle_expected = list(range(0, 145, 3))
    assert ECMWF_MODEL.target_fhs(0) == synoptic_expected
    assert ECMWF_MODEL.target_fhs(12) == synoptic_expected
    assert ECMWF_MODEL.target_fhs(6) == off_cycle_expected
    assert ECMWF_MODEL.target_fhs(18) == off_cycle_expected


def test_ecmwf_alias_and_herbie_request_invariants() -> None:
    assert ECMWF_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("tm2m") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("2t") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("dp2m") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("d2m") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("2d") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("dewpoint") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert ECMWF_MODEL.normalize_var_id("t850") == "tmp850"
    assert ECMWF_MODEL.normalize_var_id("temp850") == "tmp850"
    assert ECMWF_MODEL.normalize_var_id("wspd850") == "wspd850"
    assert ECMWF_MODEL.normalize_var_id("850mb_heights_winds") == "wspd850"
    assert ECMWF_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert ECMWF_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"
    assert ECMWF_MODEL.normalize_var_id("vort500") == "vort500"
    assert ECMWF_MODEL.normalize_var_id("500mb_vorticity") == "vort500"
    assert ECMWF_MODEL.normalize_var_id("precip_total") == "precip_total"
    assert ECMWF_MODEL.normalize_var_id("apcp") == "precip_total"
    assert ECMWF_MODEL.normalize_var_id("qpf") == "precip_total"
    assert ECMWF_MODEL.normalize_var_id("snowfall_total") == "snowfall_total"
    assert ECMWF_MODEL.normalize_var_id("asnow") == "snowfall_total"
    assert ECMWF_MODEL.normalize_var_id("snow10") == "snowfall_total"
    assert ECMWF_MODEL.normalize_var_id("total_snow") == "snowfall_total"
    assert ECMWF_MODEL.normalize_var_id("snowfall_kuchera_total") == "snowfall_kuchera_total"
    assert ECMWF_MODEL.normalize_var_id("snowkuchera") == "snowfall_kuchera_total"
    assert ECMWF_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert ECMWF_MODEL.normalize_var_id("wind10m") == "wspd10m"
    assert ECMWF_MODEL.normalize_var_id("wgst10m") == "wgst10m"
    assert ECMWF_MODEL.normalize_var_id("gust10m") == "wgst10m"
    assert ECMWF_MODEL.normalize_var_id("10m_gust") == "wgst10m"
    assert ECMWF_MODEL.normalize_var_id("gust") == "wgst10m"
    assert ECMWF_MODEL.normalize_var_id("mucape") == "mucape"
    assert ECMWF_MODEL.normalize_var_id("most_unstable_cape") == "mucape"
    assert ECMWF_MODEL.normalize_var_id("pwat") == "pwat"
    assert ECMWF_MODEL.normalize_var_id("precipitable_water") == "pwat"
    assert ECMWF_MODEL.normalize_var_id("precipitablewater") == "pwat"
    assert ECMWF_MODEL.normalize_var_id("tcwv") == "pwat"
    assert ECMWF_MODEL.normalize_var_id("ptype_intensity") == "ptype_intensity"
    assert ECMWF_MODEL.normalize_var_id("precip_ptype") == "ptype_intensity"
    assert ECMWF_MODEL.normalize_var_id("ptype") == "ptype_intensity"
    assert ECMWF_MODEL.normalize_var_id("10u") == "10u"
    assert ECMWF_MODEL.normalize_var_id("u10") == "10u"
    assert ECMWF_MODEL.normalize_var_id("10v") == "10v"
    assert ECMWF_MODEL.normalize_var_id("v10") == "10v"

    synoptic_request = ECMWF_MODEL.herbie_request(
        product="oper",
        var_key="tmp2m",
        run_date=datetime(2026, 4, 20, 0, tzinfo=timezone.utc),
    )
    assert synoptic_request.model == "ifs"
    assert synoptic_request.product == "oper"
    assert synoptic_request.herbie_kwargs["priority"] == ["azure", "aws", "ecmwf"]

    off_cycle_request = ECMWF_MODEL.herbie_request(
        product="oper",
        var_key="tmp2m",
        run_date=datetime(2026, 4, 20, 6, tzinfo=timezone.utc),
    )
    assert off_cycle_request.model == "ifs"
    assert off_cycle_request.product == "scda"
    assert off_cycle_request.herbie_kwargs["priority"] == ["azure", "aws", "ecmwf"]


def test_ecmwf_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = ECMWF_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m", "dp2m", "tmp850", "wspd850", "wspd300", "vort500", "precip_total", "ptype_intensity", "snowfall_total", "snowfall_kuchera_total", "wspd10m", "wgst10m", "mucape", "pwat"}

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 9000.0,
        "na": 9000.0,
    }
    gust_spec = ECMWF_MODEL.get_var("wgst10m")
    assert gust_spec is not None
    assert gust_spec.selectors.search == [":10fg:", ":10fg3:"]
    assert ECMWF_MODEL.search_patterns_for_var(var_key="wgst10m", fh=90) == [":10fg:", ":10fg3:"]
    assert ECMWF_MODEL.search_patterns_for_var(var_key="wgst10m", fh=93) == [":10fg3:", ":10fg:"]
    assert ECMWF_MODEL.search_patterns_for_var(var_key="wgst10m", fh=150) == [":10fg:", ":10fg3:"]

    precip_spec = ECMWF_MODEL.get_var("precip_total")
    assert precip_spec is not None
    assert precip_spec.selectors.search == [":tp:sfc:", ":tp:"]

    snowfall_spec = ECMWF_MODEL.get_var("snowfall_total")
    assert snowfall_spec is not None
    assert snowfall_spec.selectors.search == [":sf:sfc:", ":sf:"]

    tmp850_spec = ECMWF_MODEL.get_var("tmp850")
    assert tmp850_spec is not None
    assert tmp850_spec.primary is True
    assert tmp850_spec.derived is False
    assert tmp850_spec.kind == "continuous"
    assert tmp850_spec.units == "C"
    assert tmp850_spec.selectors.search == [":t:850:pl:"]

    wspd850_spec = ECMWF_MODEL.get_var("wspd850")
    assert wspd850_spec is not None
    assert wspd850_spec.primary is True
    assert wspd850_spec.derived is True
    assert wspd850_spec.derive == "wspd10m"
    assert wspd850_spec.kind == "continuous"
    assert wspd850_spec.units == "kt"
    assert wspd850_spec.selectors.search == []
    assert wspd850_spec.selectors.hints["u_component"] == "u850"
    assert wspd850_spec.selectors.hints["v_component"] == "v850"
    assert wspd850_spec.selectors.hints["contour_component"] == "hgt850"
    assert wspd850_spec.selectors.hints["contour_interval"] == "30"
    assert wspd850_spec.selectors.hints["contour_key"] == "height_850mb"

    wspd300_spec = ECMWF_MODEL.get_var("wspd300")
    assert wspd300_spec is not None
    assert wspd300_spec.primary is True
    assert wspd300_spec.derived is True
    assert wspd300_spec.derive == "wspd10m"
    assert wspd300_spec.kind == "continuous"
    assert wspd300_spec.units == "kt"
    assert wspd300_spec.selectors.search == []
    assert wspd300_spec.selectors.hints["u_component"] == "u300"
    assert wspd300_spec.selectors.hints["v_component"] == "v300"
    assert wspd300_spec.selectors.hints["contour_component"] == "hgt300"
    assert wspd300_spec.selectors.hints["contour_interval"] == "120"
    assert wspd300_spec.selectors.hints["contour_key"] == "height_300mb"

    vort500_spec = ECMWF_MODEL.get_var("vort500")
    assert vort500_spec is not None
    assert vort500_spec.primary is True
    assert vort500_spec.derived is False
    assert vort500_spec.kind == "continuous"
    assert vort500_spec.units == "10^-5 s^-1"
    assert vort500_spec.selectors.search == [":vo:500:", ":vo:500:pl:"]
    assert vort500_spec.selectors.hints["contour_component"] == "hgt500"
    assert vort500_spec.selectors.hints["contour_interval"] == "60"
    assert vort500_spec.selectors.hints["contour_key"] == "height_500mb"

    snowfall_kuchera_spec = ECMWF_MODEL.get_var("snowfall_kuchera_total")
    assert snowfall_kuchera_spec is not None
    assert snowfall_kuchera_spec.derive == "snowfall_kuchera_total_cumulative"
    assert snowfall_kuchera_spec.selectors.hints["kuchera_lwe_component"] == "sf"
    assert snowfall_kuchera_spec.selectors.hints["cumulative_cache_version"] == "ecmwf_sf_v2"
    assert snowfall_kuchera_spec.selectors.hints["kuchera_profile_mode"] == "simplified"

    mucape_spec = ECMWF_MODEL.get_var("mucape")
    assert mucape_spec is not None
    assert mucape_spec.selectors.search == [":mucape:sfc:", ":mucape:"]

    pwat_spec = ECMWF_MODEL.get_var("pwat")
    assert pwat_spec is not None
    assert pwat_spec.primary is True
    assert pwat_spec.derived is False
    assert pwat_spec.kind == "continuous"
    assert pwat_spec.units == "in"
    assert pwat_spec.selectors.search == [":tcwv:"]
    assert pwat_spec.selectors.filter_by_keys == {
        "shortName": "tcwv",
        "typeOfLevel": "atmosphereSingleLayer",
    }

    ptype_intensity_spec = ECMWF_MODEL.get_var("ptype_intensity")
    assert ptype_intensity_spec is not None
    assert ptype_intensity_spec.primary is True
    assert ptype_intensity_spec.derived is True
    assert ptype_intensity_spec.derive == "ptype_intensity_ecmwf"
    assert ptype_intensity_spec.kind == "indexed"
    assert ptype_intensity_spec.units == "in/hr"
    assert ptype_intensity_spec.selectors.hints["precip_component"] == "precip_total"
    assert ptype_intensity_spec.selectors.hints["snow_component"] == "sf"
    assert ptype_intensity_spec.selectors.hints["contour_component"] == "msl"

    msl_spec = ECMWF_MODEL.get_var("msl")
    assert msl_spec is not None
    assert msl_spec.selectors.search == [":msl:"]

    hgt850_spec = ECMWF_MODEL.get_var("hgt850")
    assert hgt850_spec is not None
    assert hgt850_spec.selectors.search == [":gh:850:"]
    assert hgt850_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }

    u850_spec = ECMWF_MODEL.get_var("u850")
    assert u850_spec is not None
    assert u850_spec.selectors.search == [":u:850:pl:"]
    assert u850_spec.selectors.filter_by_keys == {
        "shortName": "u",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }

    v850_spec = ECMWF_MODEL.get_var("v850")
    assert v850_spec is not None
    assert v850_spec.selectors.search == [":v:850:pl:"]
    assert v850_spec.selectors.filter_by_keys == {
        "shortName": "v",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }

    hgt300_spec = ECMWF_MODEL.get_var("hgt300")
    assert hgt300_spec is not None
    assert hgt300_spec.selectors.search == [":gh:300:"]
    assert hgt300_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }

    u300_spec = ECMWF_MODEL.get_var("u300")
    assert u300_spec is not None
    assert u300_spec.selectors.search == [":u:300:"]
    assert u300_spec.selectors.filter_by_keys == {
        "shortName": "u",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }

    v300_spec = ECMWF_MODEL.get_var("v300")
    assert v300_spec is not None
    assert v300_spec.selectors.search == [":v:300:"]
    assert v300_spec.selectors.filter_by_keys == {
        "shortName": "v",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }

    hgt500_spec = ECMWF_MODEL.get_var("hgt500")
    assert hgt500_spec is not None
    assert hgt500_spec.selectors.search == [":gh:500:"]
    assert hgt500_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "500",
    }


def test_ecmwf_capabilities_schema_snapshot_invariants() -> None:
    capabilities = ECMWF_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("ecmwf", capabilities)

    assert payload["model_id"] == "ecmwf"
    assert payload["name"] == "ECMWF"
    assert payload["product"] == "oper"
    assert payload["canonical_region"] == "conus"
    assert payload["constraints"]["supports_sampling"] is True

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["var_key"] == "tmp2m"
    assert tmp2m["display_name"] == "Surface Temp"
    assert tmp2m["kind"] == "continuous"
    assert tmp2m["units"] == "F"
    assert tmp2m["buildable"] is True
    assert tmp2m["derived"] is False
    assert tmp2m["color_map_id"] == "tmp2m"
    assert tmp2m["order"] == 1
    assert tmp2m["group"] == "Temperature"
    assert tmp2m["default_fh"] == 0
    assert tmp2m["render_substrates"] == ["grid"]

    dp2m = payload["variables"]["dp2m"]
    assert dp2m["var_key"] == "dp2m"
    assert dp2m["display_name"] == "Surface Dew Point"
    assert dp2m["kind"] == "continuous"
    assert dp2m["units"] == "F"
    assert dp2m["buildable"] is True
    assert dp2m["derived"] is False
    assert dp2m["color_map_id"] == "dp2m"
    assert dp2m["order"] == 2
    assert dp2m["group"] == "Temperature"
    assert dp2m["default_fh"] == 0
    assert dp2m["render_substrates"] == ["grid"]

    tmp850 = payload["variables"]["tmp850"]
    assert tmp850["var_key"] == "tmp850"
    assert tmp850["display_name"] == "850mb Temp"
    assert tmp850["kind"] == "continuous"
    assert tmp850["units"] == "C"
    assert tmp850["buildable"] is True
    assert tmp850["derived"] is False
    assert tmp850["color_map_id"] == "tmp850"
    assert tmp850["order"] == 3
    assert tmp850["group"] == "Temperature"
    assert tmp850["default_fh"] == 0
    assert tmp850["render_substrates"] == ["grid"]

    wspd850 = payload["variables"]["wspd850"]
    assert wspd850["var_key"] == "wspd850"
    assert wspd850["display_name"] == "850mb Heights + Winds"
    assert wspd850["kind"] == "continuous"
    assert wspd850["units"] == "kt"
    assert wspd850["buildable"] is True
    assert wspd850["derived"] is True
    assert wspd850["derive_strategy_id"] == "wspd10m"
    assert wspd850["color_map_id"] == "wspd850"
    assert wspd850["order"] == 4
    assert wspd850["group"] == "Wind"
    assert wspd850["default_fh"] == 0
    assert wspd850["render_substrates"] == ["grid"]

    wspd300 = payload["variables"]["wspd300"]
    assert wspd300["var_key"] == "wspd300"
    assert wspd300["display_name"] == "300mb Heights + Winds"
    assert wspd300["kind"] == "continuous"
    assert wspd300["units"] == "kt"
    assert wspd300["buildable"] is True
    assert wspd300["derived"] is True
    assert wspd300["derive_strategy_id"] == "wspd10m"
    assert wspd300["color_map_id"] == "wspd300"
    assert wspd300["order"] == 18
    assert wspd300["group"] == "Wind"
    assert wspd300["default_fh"] == 0
    assert wspd300["render_substrates"] == ["grid"]

    vort500 = payload["variables"]["vort500"]
    assert vort500["var_key"] == "vort500"
    assert vort500["display_name"] == "500mb Vorticity"
    assert vort500["kind"] == "continuous"
    assert vort500["units"] == "10^-5 s^-1"
    assert vort500["buildable"] is True
    assert vort500["derived"] is False
    assert vort500["color_map_id"] == "vort500"
    assert vort500["order"] == 5
    assert vort500["group"] == "Dynamics"
    assert vort500["default_fh"] == 0
    assert vort500["render_substrates"] == ["grid"]

    precip_total = payload["variables"]["precip_total"]
    assert precip_total["var_key"] == "precip_total"
    assert precip_total["display_name"] == "Total Precip"
    assert precip_total["kind"] == "continuous"
    assert precip_total["units"] == "in"
    assert precip_total["buildable"] is True
    assert precip_total["derived"] is False
    assert precip_total["color_map_id"] == "precip_total"
    assert precip_total["order"] == 10
    assert precip_total["group"] == "Precipitation"
    assert precip_total["default_fh"] == 3
    assert precip_total["constraints"] == {"min_fh": 3}
    assert precip_total["render_substrates"] == ["grid"]

    ptype_intensity = payload["variables"]["ptype_intensity"]
    assert ptype_intensity["var_key"] == "ptype_intensity"
    assert ptype_intensity["display_name"] == "Precipitation Type & Intensity"
    assert ptype_intensity["kind"] == "indexed"
    assert ptype_intensity["units"] == "in/hr"
    assert ptype_intensity["buildable"] is True
    assert ptype_intensity["derived"] is True
    assert ptype_intensity["derive_strategy_id"] == "ptype_intensity_ecmwf"
    assert ptype_intensity["color_map_id"] == "ptype_intensity"
    assert ptype_intensity["order"] == 15
    assert ptype_intensity["group"] == "Precipitation"
    assert ptype_intensity["default_fh"] == 6
    assert ptype_intensity["constraints"] == {"min_fh": 3}
    assert ptype_intensity["render_substrates"] == ["grid"]

    pwat = payload["variables"]["pwat"]
    assert pwat["var_key"] == "pwat"
    assert pwat["display_name"] == "Precipitable Water"
    assert pwat["kind"] == "continuous"
    assert pwat["units"] == "in"
    assert pwat["buildable"] is True
    assert pwat["derived"] is False
    assert pwat["color_map_id"] == "pwat"
    assert pwat["order"] == 9
    assert pwat["group"] == "Moisture"
    assert pwat["default_fh"] == 0
    assert pwat["render_substrates"] == ["grid"]

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["var_key"] == "snowfall_total"
    assert snowfall_total["display_name"] == "Total Snowfall (10:1)"
    assert snowfall_total["kind"] == "continuous"
    assert snowfall_total["units"] == "in"
    assert snowfall_total["buildable"] is True
    assert snowfall_total["derived"] is False
    assert snowfall_total["color_map_id"] == "snowfall_total"
    assert snowfall_total["order"] == 11
    assert snowfall_total["group"] == "Precipitation"
    assert snowfall_total["default_fh"] == 3
    assert snowfall_total["constraints"] == {"min_fh": 3}
    assert snowfall_total["render_substrates"] == ["grid"]

    snowfall_kuchera_total = payload["variables"]["snowfall_kuchera_total"]
    assert snowfall_kuchera_total["var_key"] == "snowfall_kuchera_total"
    assert snowfall_kuchera_total["display_name"] == "Total Snowfall (Kuchera)"
    assert snowfall_kuchera_total["kind"] == "continuous"
    assert snowfall_kuchera_total["units"] == "in"
    assert snowfall_kuchera_total["buildable"] is True
    assert snowfall_kuchera_total["derived"] is True
    assert snowfall_kuchera_total["derive_strategy_id"] == "snowfall_kuchera_total_cumulative"
    assert snowfall_kuchera_total["color_map_id"] == "snowfall_total"
    assert snowfall_kuchera_total["order"] == 14
    assert snowfall_kuchera_total["group"] == "Precipitation"
    assert snowfall_kuchera_total["default_fh"] == 3
    assert snowfall_kuchera_total["constraints"] == {"min_fh": 3}
    assert snowfall_kuchera_total["render_substrates"] == ["grid"]

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["var_key"] == "wspd10m"
    assert wspd10m["display_name"] == "10m Wind Speed"
    assert wspd10m["kind"] == "continuous"
    assert wspd10m["units"] == "mph"
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["color_map_id"] == "wspd10m"
    assert wspd10m["order"] == 12
    assert wspd10m["group"] == "Wind"
    assert wspd10m["default_fh"] == 0
    assert wspd10m["render_substrates"] == ["grid"]

    wgst10m = payload["variables"]["wgst10m"]
    assert wgst10m["var_key"] == "wgst10m"
    assert wgst10m["display_name"] == "10m Wind Gust"
    assert wgst10m["kind"] == "continuous"
    assert wgst10m["units"] == "mph"
    assert wgst10m["buildable"] is True
    assert wgst10m["derived"] is False
    assert wgst10m["color_map_id"] == "wgst10m"
    assert wgst10m["order"] == 13
    assert wgst10m["group"] == "Wind"
    assert wgst10m["default_fh"] == 3
    assert wgst10m["constraints"] == {"min_fh": 3}
    assert wgst10m["render_substrates"] == ["grid"]

    mucape = payload["variables"]["mucape"]
    assert mucape["var_key"] == "mucape"
    assert mucape["display_name"] == "Most-Unstable CAPE"
    assert mucape["kind"] == "continuous"
    assert mucape["units"] == "J/kg"
    assert mucape["buildable"] is True
    assert mucape["derived"] is False
    assert mucape["color_map_id"] == "mlcape"
    assert mucape["order"] == 20
    assert mucape["group"] == "Instability"
    assert mucape["default_fh"] == 0
    assert mucape["render_substrates"] == ["grid"]
