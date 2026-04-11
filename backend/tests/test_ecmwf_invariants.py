from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.ecmwf import ECMWF_MODEL


def test_ecmwf_run_discovery_invariants() -> None:
    capabilities = ECMWF_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 12,
        "fallback_lag_hours": 6,
        "source_priority": ["azure", "aws", "ecmwf"],
    }


def test_ecmwf_target_fhs_invariants() -> None:
    expected = list(range(0, 145, 3)) + list(range(150, 361, 6))
    assert ECMWF_MODEL.target_fhs(0) == expected
    assert ECMWF_MODEL.target_fhs(12) == expected


def test_ecmwf_alias_and_herbie_request_invariants() -> None:
    assert ECMWF_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("tm2m") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("2t") == "tmp2m"
    assert ECMWF_MODEL.normalize_var_id("dp2m") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("d2m") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("2d") == "dp2m"
    assert ECMWF_MODEL.normalize_var_id("dewpoint") == "dp2m"

    request = ECMWF_MODEL.herbie_request(product="oper", var_key="tmp2m")
    assert request.model == "ifs"
    assert request.product == "oper"
    assert request.herbie_kwargs["priority"] == ["azure", "aws", "ecmwf"]


def test_ecmwf_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = ECMWF_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m", "dp2m"}

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 9000.0,
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