from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.hrrr import HRRR_MODEL


def test_hrrr_run_discovery_invariants() -> None:
    capabilities = HRRR_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_enabled": True,
        "cycle_cadence_hours": 1,
        "probe_attempts": 4,
        "fallback_lag_hours": 2,
    }


def test_hrrr_target_fhs_invariants() -> None:
    assert HRRR_MODEL.target_fhs(0) == list(range(0, 49))
    assert HRRR_MODEL.target_fhs(6) == list(range(0, 49))
    assert HRRR_MODEL.target_fhs(12) == list(range(0, 49))
    assert HRRR_MODEL.target_fhs(18) == list(range(0, 49))
    assert HRRR_MODEL.target_fhs(1) == list(range(0, 19))
    assert HRRR_MODEL.target_fhs(23) == list(range(0, 19))


def test_hrrr_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = HRRR_MODEL.capabilities
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
        "tmp850_anom",
        "wspd850",
        "wspd300",
        "sbcape",
        "mlcape",
        "mucape",
        "pwat",
        "snowfall_total",
        "snowfall_kuchera_total",
        "precip_total",
        "wspd10m",
        "wgst10m",
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

    from app.services.grid import _PACKING_BY_MODEL_VAR

    assert ("hrrr", "tmp850_anom") in _PACKING_BY_MODEL_VAR


def test_hrrr_capabilities_schema_snapshot_invariants() -> None:
    capabilities = HRRR_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("hrrr", capabilities)

    assert set(payload.keys()) == {
        "model_id",
        "name",
        "product",
        "canonical_region",
        "defaults",
        "constraints",
        "run_discovery",
        "variables",
    }
    assert payload["model_id"] == "hrrr"
    assert payload["name"] == "HRRR"
    assert payload["product"] == "sfc"
    assert payload["canonical_region"] == "conus"
    assert payload["constraints"]["supports_sampling"] is True

    tmp2m = payload["variables"]["tmp2m"]
    assert set(tmp2m.keys()) == {
        "var_key",
        "display_name",
        "kind",
        "display_resampling_override",
        "render_substrates",
        "units",
        "order",
        "group",
        "default_fh",
        "buildable",
        "color_map_id",
        "constraints",
        "derived",
        "derive_strategy_id",
    }
    assert tmp2m["var_key"] == "tmp2m"
    assert tmp2m["kind"] == "continuous"
    assert tmp2m["buildable"] is True
    assert tmp2m["display_resampling_override"] is None
    assert tmp2m["render_substrates"] == ["grid"]

    wspd850 = payload["variables"]["wspd850"]
    assert wspd850["buildable"] is True
    assert wspd850["derived"] is True
    assert wspd850["derive_strategy_id"] == "wspd10m"
    assert wspd850["kind"] == "continuous"
    assert wspd850["units"] == "kt"
    assert wspd850["display_name"] == "850mb Heights + Winds"
    assert wspd850["order"] == 4
    assert wspd850["group"] == "Wind"
    assert wspd850["color_map_id"] == "wspd850"
    assert wspd850["display_resampling_override"] is None

    tmp850_anom = payload["variables"]["tmp850_anom"]
    assert tmp850_anom["var_key"] == "tmp850_anom"
    assert tmp850_anom["display_name"] == "850mb Temperature Anomaly"
    assert tmp850_anom["kind"] == "continuous"
    assert tmp850_anom["units"] == "F"
    assert tmp850_anom["buildable"] is True
    assert tmp850_anom["derived"] is True
    assert tmp850_anom["derive_strategy_id"] == "anomaly_departure"
    assert tmp850_anom["color_map_id"] == "tmp850_anom"
    assert tmp850_anom["order"] == 3.5
    assert tmp850_anom["group"] == "Temperature"
    assert tmp850_anom["display_resampling_override"] is None

    wspd300 = payload["variables"]["wspd300"]
    assert wspd300["buildable"] is True
    assert wspd300["derived"] is True
    assert wspd300["derive_strategy_id"] == "wspd10m"
    assert wspd300["kind"] == "continuous"
    assert wspd300["units"] == "kt"
    assert wspd300["display_name"] == "300mb Heights + Winds"
    assert wspd300["order"] == 999
    assert wspd300["group"] == "Wind"
    assert wspd300["color_map_id"] == "wspd300"
    assert wspd300["display_resampling_override"] is None

    sbcape = payload["variables"]["sbcape"]
    assert sbcape["var_key"] == "sbcape"
    assert sbcape["buildable"] is True
    assert sbcape["derived"] is False
    assert sbcape["kind"] == "continuous"
    assert sbcape["units"] == "J/kg"
    assert sbcape["display_name"] == "Surface-Based CAPE"
    assert sbcape["order"] == 6
    assert sbcape["group"] == "Instability"
    assert sbcape["color_map_id"] == "mlcape"
    assert sbcape["display_resampling_override"] is None

    mlcape = payload["variables"]["mlcape"]
    assert mlcape["var_key"] == "mlcape"
    assert mlcape["buildable"] is True
    assert mlcape["derived"] is False
    assert mlcape["kind"] == "continuous"
    assert mlcape["units"] == "J/kg"
    assert mlcape["display_name"] == "Mixed-Layer CAPE"
    assert mlcape["order"] == 7
    assert mlcape["group"] == "Instability"
    assert mlcape["color_map_id"] == "mlcape"
    assert mlcape["display_resampling_override"] is None

    mucape = payload["variables"]["mucape"]
    assert mucape["var_key"] == "mucape"
    assert mucape["buildable"] is True
    assert mucape["derived"] is False
    assert mucape["kind"] == "continuous"
    assert mucape["units"] == "J/kg"
    assert mucape["display_name"] == "Most-Unstable CAPE"
    assert mucape["order"] == 8
    assert mucape["group"] == "Instability"
    assert mucape["color_map_id"] == "mlcape"
    assert mucape["display_resampling_override"] is None

    pwat = payload["variables"]["pwat"]
    assert pwat["var_key"] == "pwat"
    assert pwat["buildable"] is True
    assert pwat["derived"] is False
    assert pwat["kind"] == "continuous"
    assert pwat["units"] == "in"
    assert pwat["display_name"] == "Precipitable Water"
    assert pwat["order"] == 9
    assert pwat["group"] == "Moisture"
    assert pwat["color_map_id"] == "pwat"
    assert pwat["display_resampling_override"] is None

    radar_ptype = payload["variables"]["radar_ptype"]
    assert radar_ptype["buildable"] is True
    assert radar_ptype["derived"] is True
    assert radar_ptype["derive_strategy_id"] == "radar_ptype_combo"

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["display_resampling_override"] is None

    snowfall_kuchera_total = payload["variables"]["snowfall_kuchera_total"]
    assert snowfall_kuchera_total["buildable"] is True
    assert snowfall_kuchera_total["derived"] is True
    assert snowfall_kuchera_total["derive_strategy_id"] == "snowfall_kuchera_total_cumulative"


def test_hrrr_sbcape_selector_and_alias_invariants() -> None:
    assert HRRR_MODEL.normalize_var_id("sbcape") == "sbcape"

    sbcape_spec = HRRR_MODEL.get_var("sbcape")
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


def test_hrrr_mlcape_selector_and_alias_invariants() -> None:
    assert HRRR_MODEL.normalize_var_id("mlcape") == "mlcape"

    mlcape_spec = HRRR_MODEL.get_var("mlcape")
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


def test_hrrr_mucape_selector_and_alias_invariants() -> None:
    assert HRRR_MODEL.normalize_var_id("mucape") == "mucape"

    mucape_spec = HRRR_MODEL.get_var("mucape")
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


def test_hrrr_pwat_selector_and_alias_invariants() -> None:
    assert HRRR_MODEL.normalize_var_id("pwat") == "pwat"
    assert HRRR_MODEL.normalize_var_id("precipitable_water") == "pwat"
    assert HRRR_MODEL.normalize_var_id("tmp850_anom") == "tmp850_anom"
    assert HRRR_MODEL.normalize_var_id("t850_anom") == "tmp850_anom"
    assert HRRR_MODEL.normalize_var_id("850mb_temp_anom") == "tmp850_anom"
    assert HRRR_MODEL.normalize_var_id("wspd300") == "wspd300"
    assert HRRR_MODEL.normalize_var_id("300mb_heights_winds") == "wspd300"

    pwat_spec = HRRR_MODEL.get_var("pwat")
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


def test_hrrr_wspd850_uses_850mb_components_and_height_contours() -> None:
    wspd_spec = HRRR_MODEL.get_var("wspd850")
    assert wspd_spec is not None
    assert wspd_spec.derived is True
    assert wspd_spec.derive == "wspd10m"
    assert wspd_spec.selectors.hints["u_component"] == "u850"
    assert wspd_spec.selectors.hints["v_component"] == "v850"
    assert wspd_spec.selectors.hints["contour_component"] == "hgt850"
    assert wspd_spec.selectors.hints["contour_key"] == "height_850mb"
    assert wspd_spec.selectors.hints["contour_product"] == "prs"


def test_hrrr_wspd300_uses_300mb_components_and_height_contours() -> None:
    wspd_spec = HRRR_MODEL.get_var("wspd300")
    assert wspd_spec is not None
    assert wspd_spec.derived is True
    assert wspd_spec.derive == "wspd10m"
    assert wspd_spec.selectors.hints["u_component"] == "u300"
    assert wspd_spec.selectors.hints["v_component"] == "v300"
    assert wspd_spec.selectors.hints["contour_component"] == "hgt300"
    assert wspd_spec.selectors.hints["contour_key"] == "height_300mb"
    assert wspd_spec.selectors.hints["contour_product"] == "prs"
