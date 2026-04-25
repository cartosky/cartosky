from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.aigfs import AIGFS_MODEL


def test_aigfs_run_discovery_invariants() -> None:
    capabilities = AIGFS_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["nomads"],
    }


def test_aigfs_target_fhs_invariants() -> None:
    expected = list(range(0, 385, 6))
    assert AIGFS_MODEL.target_fhs(0) == expected
    assert AIGFS_MODEL.target_fhs(12) == expected


def test_aigfs_alias_and_herbie_request_invariants() -> None:
    assert AIGFS_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert AIGFS_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert AIGFS_MODEL.normalize_var_id("2t") == "tmp2m"
    assert AIGFS_MODEL.normalize_var_id("precip_total") == "precip_total"
    assert AIGFS_MODEL.normalize_var_id("total_precip") == "precip_total"
    assert AIGFS_MODEL.normalize_var_id("apcp") == "precip_total"
    assert AIGFS_MODEL.normalize_var_id("qpf") == "precip_total"
    assert AIGFS_MODEL.normalize_var_id("tmp850") == "tmp850"
    assert AIGFS_MODEL.normalize_var_id("t850") == "tmp850"
    assert AIGFS_MODEL.normalize_var_id("temp850") == "tmp850"
    assert AIGFS_MODEL.normalize_var_id("wspd850") == "wspd850"
    assert AIGFS_MODEL.normalize_var_id("wind850") == "wspd850"
    assert AIGFS_MODEL.normalize_var_id("850mb_heights_winds") == "wspd850"
    assert AIGFS_MODEL.normalize_var_id("z850") == "hgt850"
    assert AIGFS_MODEL.normalize_var_id("gh850") == "hgt850"
    assert AIGFS_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert AIGFS_MODEL.normalize_var_id("wind300") == "wspd300"
    assert AIGFS_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"
    assert AIGFS_MODEL.normalize_var_id("z300") == "hgt300"
    assert AIGFS_MODEL.normalize_var_id("gh300") == "hgt300"
    assert AIGFS_MODEL.normalize_var_id("hgt500_anom") == "hgt500_anom"
    assert AIGFS_MODEL.normalize_var_id("500mb_height_anom") == "hgt500_anom"
    assert AIGFS_MODEL.normalize_var_id("vort500") == "vort500"
    assert AIGFS_MODEL.normalize_var_id("500mb_vorticity") == "vort500"
    assert AIGFS_MODEL.normalize_var_id("absv500") == "vort500"
    assert AIGFS_MODEL.normalize_var_id("z500") == "hgt500"
    assert AIGFS_MODEL.normalize_var_id("gh500") == "hgt500"
    assert AIGFS_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert AIGFS_MODEL.normalize_var_id("wind10m") == "wspd10m"
    assert AIGFS_MODEL.normalize_var_id("10mwind") == "wspd10m"
    assert AIGFS_MODEL.normalize_var_id("10u") == "10u"
    assert AIGFS_MODEL.normalize_var_id("u10") == "10u"
    assert AIGFS_MODEL.normalize_var_id("10v") == "10v"
    assert AIGFS_MODEL.normalize_var_id("v10") == "10v"

    request = AIGFS_MODEL.herbie_request(product="sfc", var_key="tmp2m")
    assert request.model == "aigfs"
    assert request.product == "sfc"
    assert request.herbie_kwargs["priority"] == ["nomads"]

    tmp850_request = AIGFS_MODEL.herbie_request(product="sfc", var_key="tmp850")
    assert tmp850_request.model == "aigfs"
    assert tmp850_request.product == "pres"
    assert tmp850_request.herbie_kwargs["priority"] == ["nomads"]

    wspd850_request = AIGFS_MODEL.herbie_request(product="sfc", var_key="wspd850")
    assert wspd850_request.model == "aigfs"
    assert wspd850_request.product == "pres"
    assert wspd850_request.herbie_kwargs["priority"] == ["nomads"]

    wspd300_request = AIGFS_MODEL.herbie_request(product="sfc", var_key="wspd300")
    assert wspd300_request.model == "aigfs"
    assert wspd300_request.product == "pres"
    assert wspd300_request.herbie_kwargs["priority"] == ["nomads"]

    vort500_request = AIGFS_MODEL.herbie_request(product="sfc", var_key="vort500")
    assert vort500_request.model == "aigfs"
    assert vort500_request.product == "pres"
    assert vort500_request.herbie_kwargs["priority"] == ["nomads"]


def test_aigfs_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = AIGFS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m", "precip_total", "tmp850", "wspd850", "wspd300", "hgt500_anom", "vort500", "wspd10m"}

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "na"
    assert capabilities.grid_meters_by_region == {
        "conus": 25000.0,
        "na": 25000.0,
    }

    from app.services.grid import _PACKING_BY_MODEL_VAR

    assert ("aigfs", "hgt500_anom") in _PACKING_BY_MODEL_VAR

    tmp2m_spec = AIGFS_MODEL.get_var("tmp2m")
    assert tmp2m_spec is not None
    assert tmp2m_spec.primary is True
    assert tmp2m_spec.derived is False
    assert tmp2m_spec.kind == "continuous"
    assert tmp2m_spec.units == "F"
    assert tmp2m_spec.selectors.search == [":TMP:2 m above ground:"]
    assert tmp2m_spec.selectors.filter_by_keys == {
        "typeOfLevel": "heightAboveGround",
        "level": "2",
    }

    precip_total_spec = AIGFS_MODEL.get_var("precip_total")
    assert precip_total_spec is not None
    assert precip_total_spec.primary is True
    assert precip_total_spec.derived is False
    assert precip_total_spec.kind == "continuous"
    assert precip_total_spec.units == "in"
    assert precip_total_spec.selectors.search == [
        r":APCP:surface:0-[0-9]+ hour acc[^:]*:$",
        r":APCP:surface:0-[0-9]+ day acc[^:]*:$",
    ]
    assert precip_total_spec.selectors.filter_by_keys == {
        "shortName": "apcp",
        "typeOfLevel": "surface",
    }

    tmp850_spec = AIGFS_MODEL.get_var("tmp850")
    assert tmp850_spec is not None
    assert tmp850_spec.primary is True
    assert tmp850_spec.derived is False
    assert tmp850_spec.kind == "continuous"
    assert tmp850_spec.units == "C"
    assert tmp850_spec.selectors.search == [":TMP:850 mb:"]
    assert tmp850_spec.selectors.filter_by_keys == {
        "shortName": "t",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }
    assert tmp850_spec.selectors.hints["product"] == "pres"

    wspd850_spec = AIGFS_MODEL.get_var("wspd850")
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
    assert wspd850_spec.selectors.hints["product"] == "pres"

    wspd300_spec = AIGFS_MODEL.get_var("wspd300")
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
    assert wspd300_spec.selectors.hints["product"] == "pres"

    hgt500_anom_spec = AIGFS_MODEL.get_var("hgt500_anom")
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
    assert hgt500_anom_spec.selectors.hints["contour_conversion"] == "m_to_dam"
    assert hgt500_anom_spec.selectors.hints["product"] == "pres"

    vort500_spec = AIGFS_MODEL.get_var("vort500")
    assert vort500_spec is not None
    assert vort500_spec.primary is True
    assert vort500_spec.derived is True
    assert vort500_spec.derive == "vort500_from_uv"
    assert vort500_spec.kind == "continuous"
    assert vort500_spec.units == "10^-5 s^-1"
    assert vort500_spec.selectors.search == []
    assert vort500_spec.selectors.hints["u_component"] == "u500"
    assert vort500_spec.selectors.hints["v_component"] == "v500"
    assert vort500_spec.selectors.hints["contour_component"] == "hgt500"
    assert vort500_spec.selectors.hints["contour_interval"] == "60"
    assert vort500_spec.selectors.hints["contour_key"] == "height_500mb"
    assert vort500_spec.selectors.hints["product"] == "pres"

    hgt850_spec = AIGFS_MODEL.get_var("hgt850")
    assert hgt850_spec is not None
    assert hgt850_spec.primary is False
    assert hgt850_spec.derived is False
    assert hgt850_spec.selectors.search == [":HGT:850 mb:"]
    assert hgt850_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }
    assert hgt850_spec.selectors.hints["product"] == "pres"

    u850_spec = AIGFS_MODEL.get_var("u850")
    assert u850_spec is not None
    assert u850_spec.primary is False
    assert u850_spec.derived is False
    assert u850_spec.selectors.search == [":UGRD:850 mb:"]
    assert u850_spec.selectors.filter_by_keys == {
        "shortName": "ugrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }
    assert u850_spec.selectors.hints["product"] == "pres"

    v850_spec = AIGFS_MODEL.get_var("v850")
    assert v850_spec is not None
    assert v850_spec.primary is False
    assert v850_spec.derived is False
    assert v850_spec.selectors.search == [":VGRD:850 mb:"]
    assert v850_spec.selectors.filter_by_keys == {
        "shortName": "vgrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "850",
    }
    assert v850_spec.selectors.hints["product"] == "pres"

    hgt300_spec = AIGFS_MODEL.get_var("hgt300")
    assert hgt300_spec is not None
    assert hgt300_spec.primary is False
    assert hgt300_spec.derived is False
    assert hgt300_spec.selectors.search == [":HGT:300 mb:"]
    assert hgt300_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }
    assert hgt300_spec.selectors.hints["product"] == "pres"

    u300_spec = AIGFS_MODEL.get_var("u300")
    assert u300_spec is not None
    assert u300_spec.primary is False
    assert u300_spec.derived is False
    assert u300_spec.selectors.search == [":UGRD:300 mb:"]
    assert u300_spec.selectors.filter_by_keys == {
        "shortName": "ugrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }
    assert u300_spec.selectors.hints["product"] == "pres"

    v300_spec = AIGFS_MODEL.get_var("v300")
    assert v300_spec is not None
    assert v300_spec.primary is False
    assert v300_spec.derived is False
    assert v300_spec.selectors.search == [":VGRD:300 mb:"]
    assert v300_spec.selectors.filter_by_keys == {
        "shortName": "vgrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "300",
    }
    assert v300_spec.selectors.hints["product"] == "pres"

    hgt500_spec = AIGFS_MODEL.get_var("hgt500")
    assert hgt500_spec is not None
    assert hgt500_spec.primary is False
    assert hgt500_spec.derived is False
    assert hgt500_spec.selectors.search == [":HGT:500 mb:"]
    assert hgt500_spec.selectors.filter_by_keys == {
        "shortName": "gh",
        "typeOfLevel": "isobaricInhPa",
        "level": "500",
    }
    assert hgt500_spec.selectors.hints["product"] == "pres"

    u500_spec = AIGFS_MODEL.get_var("u500")
    assert u500_spec is not None
    assert u500_spec.primary is False
    assert u500_spec.derived is False
    assert u500_spec.selectors.search == [":UGRD:500 mb:"]
    assert u500_spec.selectors.filter_by_keys == {
        "shortName": "ugrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "500",
    }
    assert u500_spec.selectors.hints["product"] == "pres"

    v500_spec = AIGFS_MODEL.get_var("v500")
    assert v500_spec is not None
    assert v500_spec.primary is False
    assert v500_spec.derived is False
    assert v500_spec.selectors.search == [":VGRD:500 mb:"]
    assert v500_spec.selectors.filter_by_keys == {
        "shortName": "vgrd",
        "typeOfLevel": "isobaricInhPa",
        "level": "500",
    }
    assert v500_spec.selectors.hints["product"] == "pres"

    wspd10m_spec = AIGFS_MODEL.get_var("wspd10m")
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

    u10_spec = AIGFS_MODEL.get_var("10u")
    assert u10_spec is not None
    assert u10_spec.primary is False
    assert u10_spec.derived is False
    assert u10_spec.selectors.search == [":UGRD:10 m above ground:"]
    assert u10_spec.selectors.filter_by_keys == {
        "typeOfLevel": "heightAboveGround",
        "level": "10",
    }

    v10_spec = AIGFS_MODEL.get_var("10v")
    assert v10_spec is not None
    assert v10_spec.primary is False
    assert v10_spec.derived is False
    assert v10_spec.selectors.search == [":VGRD:10 m above ground:"]
    assert v10_spec.selectors.filter_by_keys == {
        "typeOfLevel": "heightAboveGround",
        "level": "10",
    }


def test_aigfs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = AIGFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("aigfs", capabilities)

    assert payload["model_id"] == "aigfs"
    assert payload["name"] == "AIGFS"
    assert payload["product"] == "sfc"
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
    assert precip_total["default_fh"] == 6
    assert precip_total["constraints"] == {"min_fh": 6}
    assert precip_total["render_substrates"] == ["grid"]

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

    vort500 = payload["variables"]["vort500"]
    assert vort500["var_key"] == "vort500"
    assert vort500["display_name"] == "500mb Heights + Vorticity"
    assert vort500["kind"] == "continuous"
    assert vort500["units"] == "10^-5 s^-1"
    assert vort500["buildable"] is True
    assert vort500["derived"] is True
    assert vort500["derive_strategy_id"] == "vort500_from_uv"
    assert vort500["color_map_id"] == "vort500"
    assert vort500["order"] == 5
    assert vort500["group"] == "Dynamics"
    assert vort500["default_fh"] == 0
    assert vort500["render_substrates"] == ["grid"]

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
