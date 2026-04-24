from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.aifs import AIFS_MODEL


def test_aifs_run_discovery_invariants() -> None:
    capabilities = AIFS_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["azure", "aws", "ecmwf"],
    }


def test_aifs_target_fhs_invariants() -> None:
    expected = list(range(0, 361, 6))
    assert AIFS_MODEL.target_fhs(0) == expected
    assert AIFS_MODEL.target_fhs(12) == expected


def test_aifs_alias_and_herbie_request_invariants() -> None:
    assert AIFS_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert AIFS_MODEL.normalize_var_id("tm2m") == "tmp2m"
    assert AIFS_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert AIFS_MODEL.normalize_var_id("2t") == "tmp2m"
    assert AIFS_MODEL.normalize_var_id("dp2m") == "dp2m"
    assert AIFS_MODEL.normalize_var_id("d2m") == "dp2m"
    assert AIFS_MODEL.normalize_var_id("2d") == "dp2m"
    assert AIFS_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert AIFS_MODEL.normalize_var_id("t850") == "tmp850"
    assert AIFS_MODEL.normalize_var_id("temp850") == "tmp850"
    assert AIFS_MODEL.normalize_var_id("wspd850") == "wspd850"
    assert AIFS_MODEL.normalize_var_id("wind850") == "wspd850"
    assert AIFS_MODEL.normalize_var_id("850mb_heights_winds") == "wspd850"
    assert AIFS_MODEL.normalize_var_id("z850") == "hgt850"
    assert AIFS_MODEL.normalize_var_id("gh850") == "hgt850"
    assert AIFS_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert AIFS_MODEL.normalize_var_id("wind300") == "wspd300"
    assert AIFS_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"
    assert AIFS_MODEL.normalize_var_id("z300") == "hgt300"
    assert AIFS_MODEL.normalize_var_id("gh300") == "hgt300"
    assert AIFS_MODEL.normalize_var_id("hgt500") == "hgt500"
    assert AIFS_MODEL.normalize_var_id("z500") == "hgt500"
    assert AIFS_MODEL.normalize_var_id("gh500") == "hgt500"
    assert AIFS_MODEL.normalize_var_id("hgt500_anom") == "hgt500_anom"
    assert AIFS_MODEL.normalize_var_id("500mb_height_anom") == "hgt500_anom"
    assert AIFS_MODEL.normalize_var_id("precip_total") == "precip_total"
    assert AIFS_MODEL.normalize_var_id("apcp") == "precip_total"
    assert AIFS_MODEL.normalize_var_id("qpf") == "precip_total"
    assert AIFS_MODEL.normalize_var_id("snowfall_total") == "snowfall_total"
    assert AIFS_MODEL.normalize_var_id("asnow") == "snowfall_total"
    assert AIFS_MODEL.normalize_var_id("snow10") == "snowfall_total"
    assert AIFS_MODEL.normalize_var_id("total_snow") == "snowfall_total"
    assert AIFS_MODEL.normalize_var_id("pwat") == "pwat"
    assert AIFS_MODEL.normalize_var_id("precipitable_water") == "pwat"
    assert AIFS_MODEL.normalize_var_id("precipitablewater") == "pwat"
    assert AIFS_MODEL.normalize_var_id("tcwv") == "pwat"
    assert AIFS_MODEL.normalize_var_id("tcw") == "pwat"
    assert AIFS_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert AIFS_MODEL.normalize_var_id("wind10m") == "wspd10m"
    assert AIFS_MODEL.normalize_var_id("10u") == "10u"
    assert AIFS_MODEL.normalize_var_id("u10") == "10u"
    assert AIFS_MODEL.normalize_var_id("10v") == "10v"
    assert AIFS_MODEL.normalize_var_id("v10") == "10v"

    request = AIFS_MODEL.herbie_request(product="oper", var_key="tmp2m")
    assert request.model == "aifs"
    assert request.product == "oper"
    assert request.herbie_kwargs["priority"] == ["azure", "aws", "ecmwf"]


def test_aifs_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = AIFS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m", "dp2m", "tmp850", "wspd850", "wspd300", "hgt500_anom", "precip_total", "pwat", "snowfall_total", "wspd10m"}

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "na"
    assert capabilities.grid_meters_by_region == {
        "conus": 9000.0,
        "na": 9000.0,
    }

    from app.services.grid import _PACKING_BY_MODEL_VAR

    assert ("aifs", "hgt500_anom") in _PACKING_BY_MODEL_VAR

    tmp2m_spec = AIFS_MODEL.get_var("tmp2m")
    assert tmp2m_spec is not None
    assert tmp2m_spec.primary is True
    assert tmp2m_spec.derived is False
    assert tmp2m_spec.kind == "continuous"
    assert tmp2m_spec.units == "F"
    assert tmp2m_spec.selectors.search == [":2t:"]
    assert tmp2m_spec.selectors.filter_by_keys == {
        "shortName": "2t",
        "typeOfLevel": "surface",
    }

    dp2m_spec = AIFS_MODEL.get_var("dp2m")
    assert dp2m_spec is not None
    assert dp2m_spec.primary is True
    assert dp2m_spec.derived is False
    assert dp2m_spec.kind == "continuous"
    assert dp2m_spec.units == "F"
    assert dp2m_spec.selectors.search == [":2d:"]
    assert dp2m_spec.selectors.filter_by_keys == {
        "shortName": "2d",
        "typeOfLevel": "surface",
    }

    tmp850_spec = AIFS_MODEL.get_var("tmp850")
    assert tmp850_spec is not None
    assert tmp850_spec.primary is True
    assert tmp850_spec.derived is False
    assert tmp850_spec.kind == "continuous"
    assert tmp850_spec.units == "C"
    assert tmp850_spec.selectors.search == [":t:850:pl:"]

    wspd850_spec = AIFS_MODEL.get_var("wspd850")
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
    assert wspd850_spec.selectors.hints["contour_conversion"] == "geopotential_to_height_m"

    u850_spec = AIFS_MODEL.get_var("u850")
    assert u850_spec is not None
    assert u850_spec.primary is False
    assert u850_spec.derived is False
    assert u850_spec.selectors.search == [":u:850:pl:"]

    v850_spec = AIFS_MODEL.get_var("v850")
    assert v850_spec is not None
    assert v850_spec.primary is False
    assert v850_spec.derived is False
    assert v850_spec.selectors.search == [":v:850:pl:"]

    hgt850_spec = AIFS_MODEL.get_var("hgt850")
    assert hgt850_spec is not None
    assert hgt850_spec.primary is False
    assert hgt850_spec.derived is False
    assert hgt850_spec.selectors.search == [":z:850:pl:", ":z:850:"]

    wspd300_spec = AIFS_MODEL.get_var("wspd300")
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
    assert wspd300_spec.selectors.hints["contour_conversion"] == "geopotential_to_height_m"

    u300_spec = AIFS_MODEL.get_var("u300")
    assert u300_spec is not None
    assert u300_spec.primary is False
    assert u300_spec.derived is False
    assert u300_spec.selectors.search == [":u:300:"]

    v300_spec = AIFS_MODEL.get_var("v300")
    assert v300_spec is not None
    assert v300_spec.primary is False
    assert v300_spec.derived is False
    assert v300_spec.selectors.search == [":v:300:"]

    hgt300_spec = AIFS_MODEL.get_var("hgt300")
    assert hgt300_spec is not None
    assert hgt300_spec.primary is False
    assert hgt300_spec.derived is False
    assert hgt300_spec.selectors.search == [":z:300:pl:", ":z:300:"]

    hgt500_spec = AIFS_MODEL.get_var("hgt500")
    assert hgt500_spec is not None
    assert hgt500_spec.primary is False
    assert hgt500_spec.derived is False
    assert hgt500_spec.selectors.search == [":z:500:pl:", ":z:500:"]

    hgt500_capability = capabilities.variable_catalog["hgt500"]
    assert hgt500_capability.buildable is False
    assert hgt500_capability.frontend == {"internal_only": True}

    hgt500_anom_spec = AIFS_MODEL.get_var("hgt500_anom")
    assert hgt500_anom_spec is not None
    assert hgt500_anom_spec.primary is True
    assert hgt500_anom_spec.derived is True
    assert hgt500_anom_spec.derive == "anomaly_departure"
    assert hgt500_anom_spec.kind == "continuous"
    assert hgt500_anom_spec.units == "dam"
    assert hgt500_anom_spec.selectors.hints["base_component"] == "hgt500"
    assert hgt500_anom_spec.selectors.hints["baseline_field"] == "hgt500"
    assert hgt500_anom_spec.selectors.hints["baseline_region"] == "na"
    assert hgt500_anom_spec.selectors.hints["contour_component"] == "hgt500"
    assert hgt500_anom_spec.selectors.hints["contour_conversion"] == "geopotential_to_height_dam"

    precip_spec = AIFS_MODEL.get_var("precip_total")
    assert precip_spec is not None
    assert precip_spec.primary is True
    assert precip_spec.derived is False
    assert precip_spec.kind == "continuous"
    assert precip_spec.units == "in"
    assert precip_spec.selectors.search == [":tp:sfc:", ":tp:"]
    assert precip_spec.selectors.filter_by_keys == {
        "shortName": "tp",
        "typeOfLevel": "surface",
    }
    precip_capability = capabilities.variable_catalog["precip_total"]
    assert precip_capability.conversion == "kgm2_to_in"

    snowfall_spec = AIFS_MODEL.get_var("snowfall_total")
    assert snowfall_spec is not None
    assert snowfall_spec.primary is True
    assert snowfall_spec.derived is False
    assert snowfall_spec.kind == "continuous"
    assert snowfall_spec.units == "in"
    assert snowfall_spec.selectors.search == [":sf:sfc:", ":sf:"]
    assert snowfall_spec.selectors.filter_by_keys == {
        "shortName": "sf",
        "typeOfLevel": "surface",
    }
    snowfall_capability = capabilities.variable_catalog["snowfall_total"]
    assert snowfall_capability.conversion == "kgm2_swe_to_in_10to1"

    pwat_spec = AIFS_MODEL.get_var("pwat")
    assert pwat_spec is not None
    assert pwat_spec.primary is True
    assert pwat_spec.derived is False
    assert pwat_spec.kind == "continuous"
    assert pwat_spec.units == "in"
    assert pwat_spec.selectors.search == [":tcw:", ":tcw:sfc:"]
    assert pwat_spec.selectors.filter_by_keys == {
        "shortName": "tcw",
        "typeOfLevel": "surface",
    }
    pwat_capability = capabilities.variable_catalog["pwat"]
    assert pwat_capability.conversion == "kgm2_to_in"

    wspd10m_spec = AIFS_MODEL.get_var("wspd10m")
    assert wspd10m_spec is not None
    assert wspd10m_spec.primary is False
    assert wspd10m_spec.derived is True
    assert wspd10m_spec.derive == "wspd10m"
    assert wspd10m_spec.kind == "continuous"
    assert wspd10m_spec.units == "mph"
    assert wspd10m_spec.selectors.hints == {
        "u_component": "10u",
        "v_component": "10v",
    }

    u10_spec = AIFS_MODEL.get_var("10u")
    assert u10_spec is not None
    assert u10_spec.primary is False
    assert u10_spec.derived is False
    assert u10_spec.selectors.search == [":10u:"]
    assert u10_spec.selectors.filter_by_keys == {
        "shortName": "10u",
        "typeOfLevel": "surface",
    }

    v10_spec = AIFS_MODEL.get_var("10v")
    assert v10_spec is not None
    assert v10_spec.primary is False
    assert v10_spec.derived is False
    assert v10_spec.selectors.search == [":10v:"]
    assert v10_spec.selectors.filter_by_keys == {
        "shortName": "10v",
        "typeOfLevel": "surface",
    }


def test_aifs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = AIFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("aifs", capabilities)

    assert payload["model_id"] == "aifs"
    assert payload["name"] == "AIFS"
    assert payload["product"] == "oper"
    assert payload["canonical_region"] == "na"
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

    hgt500_anom = payload["variables"]["hgt500_anom"]
    assert hgt500_anom["var_key"] == "hgt500_anom"
    assert hgt500_anom["display_name"] == "500mb Height Anomaly"
    assert hgt500_anom["kind"] == "continuous"
    assert hgt500_anom["units"] == "dam"
    assert hgt500_anom["buildable"] is True
    assert hgt500_anom["derived"] is True
    assert hgt500_anom["derive_strategy_id"] == "anomaly_departure"
    assert hgt500_anom["color_map_id"] == "hgt500_anom"
    assert hgt500_anom["order"] == 5
    assert hgt500_anom["group"] == "Dynamics"
    assert hgt500_anom["default_fh"] == 0
    assert hgt500_anom["display_resampling_override"] == "bilinear"
    assert hgt500_anom["render_substrates"] == ["grid"]

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

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["var_key"] == "wspd10m"
    assert wspd10m["display_name"] == "10m Wind Speed"
    assert wspd10m["kind"] == "continuous"
    assert wspd10m["units"] == "mph"
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["derive_strategy_id"] == "wspd10m"
    assert wspd10m["color_map_id"] == "wspd10m"
    assert wspd10m["order"] == 12
    assert wspd10m["group"] == "Wind"
    assert wspd10m["default_fh"] == 0
    assert wspd10m["render_substrates"] == ["grid"]

    assert "10u" not in payload["variables"]
    assert "10v" not in payload["variables"]
    assert "u850" not in payload["variables"]
    assert "v850" not in payload["variables"]
    assert "hgt850" not in payload["variables"]
    assert "u300" not in payload["variables"]
    assert "v300" not in payload["variables"]
    assert "hgt300" not in payload["variables"]
    assert "hgt500" not in payload["variables"]

    assert payload["defaults"] == {
        "default_var_key": "tmp2m",
        "default_run": "latest",
        "default_render_substrate": "grid",
    }
