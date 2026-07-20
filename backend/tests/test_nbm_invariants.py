from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.nbm import NBM_FHS_OFF_CYCLE, NBM_FHS_SYNOPTIC, NBM_MODEL


def test_nbm_target_fhs_invariants() -> None:
    expected_synoptic = list(NBM_FHS_SYNOPTIC)
    expected_off_cycle = list(NBM_FHS_OFF_CYCLE)

    # Synoptic cycles: 00z, 06z, 12z, 18z  →  hourly 0-36, then 6-hourly 42..264
    for cycle in (0, 6, 12, 18):
        fhs = NBM_MODEL.target_fhs(cycle)
        assert fhs == expected_synoptic, f"Mismatch for cycle {cycle}z"
        assert fhs[0] == 0
        assert fhs[-1] == 264

    # Off cycles: 03z, 09z, 15z, 21z  →  hourly 0-36, then 6-hourly 39..261
    for cycle in (3, 9, 15, 21):
        fhs = NBM_MODEL.target_fhs(cycle)
        assert fhs == expected_off_cycle, f"Mismatch for cycle {cycle}z"
        assert fhs[0] == 0
        assert fhs[-1] == 261

    # Structural checks
    assert list(range(0, 37)) == expected_synoptic[:37]
    assert list(range(0, 37)) == expected_off_cycle[:37]
    assert all((fh % 6 == 0) for fh in expected_synoptic[37:])
    assert all((fh % 6 == 3) for fh in expected_off_cycle[37:])


def test_nbm_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = NBM_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {
        "tmp2m",
        "sbcape",
        "precip_total",
        "snowfall_total",
        "wspd10m",
    }

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 13000.0,
        "pnw": 13000.0,
    }
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 3,
        "fallback_lag_hours": 5,
    }


def test_nbm_capabilities_schema_snapshot_invariants() -> None:
    capabilities = NBM_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("nbm", capabilities)

    assert payload["model_id"] == "nbm"
    assert payload["name"] == "NBM"
    assert payload["product"] == "co"
    assert payload["canonical_region"] == "conus"
    assert payload["constraints"]["supports_sampling"] is True

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["buildable"] is True
    assert tmp2m["derived"] is False
    assert tmp2m["kind"] == "continuous"
    assert tmp2m["units"] == "F"
    assert tmp2m["display_name"] == "Surface Temp"

    sbcape = payload["variables"]["sbcape"]
    assert sbcape["buildable"] is True
    assert sbcape["derived"] is False
    assert sbcape["kind"] == "continuous"
    assert sbcape["units"] == "J/kg"
    assert sbcape["display_name"] == "Surface-Based CAPE"
    assert sbcape["group"] == "Instability"
    assert sbcape["color_map_id"] == "mlcape"
    assert sbcape["display_resampling_override"] is None

    precip_total = payload["variables"]["precip_total"]
    assert precip_total["buildable"] is True
    assert precip_total["derived"] is True
    assert precip_total["derive_strategy_id"] == "precip_total_cumulative"
    assert precip_total["kind"] == "continuous"
    assert precip_total["units"] == "in"
    assert precip_total["default_fh"] == 6
    assert precip_total["constraints"] == {"min_fh": 6}
    assert precip_total["display_name"] == "Total Precip"
    assert precip_total["display_resampling_override"] is None

    snowfall_total = payload["variables"]["snowfall_total"]
    assert snowfall_total["buildable"] is True
    assert snowfall_total["derived"] is True
    assert snowfall_total["derive_strategy_id"] == "precip_total_cumulative"
    assert snowfall_total["kind"] == "continuous"
    assert snowfall_total["units"] == "in"
    assert snowfall_total["default_fh"] == 6
    assert snowfall_total["constraints"] == {"min_fh": 6}
    assert snowfall_total["display_name"] == "Total Snowfall (10:1)"
    assert snowfall_total["display_resampling_override"] is None

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["buildable"] is True
    assert wspd10m["derived"] is True
    assert wspd10m["derive_strategy_id"] == "wspd10m"
    assert wspd10m["kind"] == "continuous"
    assert wspd10m["units"] == "mph"
    assert wspd10m["display_name"] == "10m Wind Speed"

    u10 = payload["variables"]["10u"]
    assert u10["buildable"] is False

    v10 = payload["variables"]["10v"]
    assert v10["buildable"] is False

    si10 = payload["variables"]["10si"]
    assert si10["buildable"] is False

    apcp_step = payload["variables"]["apcp_step"]
    assert apcp_step["buildable"] is False

    asnow_step = payload["variables"]["asnow_step"]
    assert asnow_step["buildable"] is False

    snowfall_spec = NBM_MODEL.get_var("snowfall_total")
    assert snowfall_spec is not None
    assert snowfall_spec.selectors.hints["apcp_component"] == "asnow_step"
    assert snowfall_spec.selectors.hints["step_hours"] == "1"
    assert snowfall_spec.selectors.hints["step_transition_fh"] == "36"
    assert snowfall_spec.selectors.hints["step_hours_after_fh"] == "6"

    apcp_component_spec = NBM_MODEL.get_var("apcp_step")
    assert apcp_component_spec is not None
    assert apcp_component_spec.selectors.search == [
        ":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:$",
    ]
    assert apcp_component_spec.selectors.filter_by_keys["shortName"] == "apcp"

    precip_spec = NBM_MODEL.get_var("precip_total")
    assert precip_spec is not None
    assert precip_spec.selectors.hints["apcp_component"] == "apcp_step"
    assert precip_spec.selectors.hints["step_hours"] == "1"
    assert precip_spec.selectors.hints["step_transition_fh"] == "36"
    assert precip_spec.selectors.hints["step_hours_after_fh"] == "6"

    asnow_component_spec = NBM_MODEL.get_var("asnow_step")
    assert asnow_component_spec is not None
    assert asnow_component_spec.selectors.search == [
        ":ASNOW:surface:[0-9]+-[0-9]+ hour acc[^:]*:$",
    ]
    assert asnow_component_spec.selectors.filter_by_keys["shortName"] == "asnow"


def test_nbm_aliases_normalize() -> None:
    assert NBM_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert NBM_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert NBM_MODEL.normalize_var_id("2t") == "tmp2m"
    assert NBM_MODEL.normalize_var_id("sbcape") == "sbcape"
    assert NBM_MODEL.normalize_var_id("precip_total") == "precip_total"
    assert NBM_MODEL.normalize_var_id("apcp") == "precip_total"
    assert NBM_MODEL.normalize_var_id("qpf") == "precip_total"
    assert NBM_MODEL.normalize_var_id("snowfall_total") == "snowfall_total"
    assert NBM_MODEL.normalize_var_id("asnow") == "snowfall_total"
    assert NBM_MODEL.normalize_var_id("total_snow") == "snowfall_total"
    assert NBM_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert NBM_MODEL.normalize_var_id("wind10m") == "10si"
    assert NBM_MODEL.normalize_var_id("10si") == "10si"
    assert NBM_MODEL.normalize_var_id("u10") == "10u"
    assert NBM_MODEL.normalize_var_id("v10") == "10v"


def test_nbm_sbcape_selector_invariants() -> None:
    sbcape_spec = NBM_MODEL.get_var("sbcape")
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
