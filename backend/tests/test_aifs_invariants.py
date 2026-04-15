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
    assert AIFS_MODEL.normalize_var_id("wspd10m") == "wspd10m"
    assert AIFS_MODEL.normalize_var_id("wind10m") == "wspd10m"

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
    assert buildable_var_keys == {"tmp2m", "dp2m", "wspd10m"}

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 9000.0,
    }

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


def test_aifs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = AIFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("aifs", capabilities)

    assert payload["model_id"] == "aifs"
    assert payload["name"] == "AIFS"
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

    assert payload["defaults"] == {
        "default_var_key": "tmp2m",
        "default_run": "latest",
        "default_render_substrate": "grid",
    }