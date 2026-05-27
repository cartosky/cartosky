from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.serialization import serialize_model_capability
from app.models.wpc import WPC_MODEL


def test_wpc_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = WPC_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"precip_total"}

    assert capabilities.ui_defaults["default_var_key"] == "precip_total"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_defaults["default_frame_selection"] == "first"
    assert capabilities.ui_constraints["time_axis_mode"] == "valid"
    assert capabilities.ui_constraints["latest_only"] is True
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"


def test_wpc_capabilities_schema_snapshot_invariants() -> None:
    capabilities = WPC_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("wpc", capabilities)

    assert payload["model_id"] == "wpc"
    assert payload["name"] == "WPC"
    assert payload["product"] == "forecast"
    assert payload["canonical_region"] == "conus"
    assert payload["defaults"]["default_var_key"] == "precip_total"
    assert payload["defaults"]["default_run"] == "latest"
    assert payload["defaults"]["default_frame_selection"] == "first"
    assert payload["constraints"]["time_axis_mode"] == "valid"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is True

    precip = payload["variables"]["precip_total"]
    assert precip["var_key"] == "precip_total"
    assert precip["buildable"] is True
    assert precip["derived"] is False
    assert precip["kind"] == "continuous"
    assert precip["display_name"] == "Total Precip"
    assert precip["group"] == "Precipitation"
    assert precip["color_map_id"] == "precip_total"
    assert precip["default_fh"] == 6
    assert precip["constraints"]["min_fh"] == 6
    assert precip["constraints"]["max_fh"] == 168


def test_wpc_aliases_normalize() -> None:
    assert WPC_MODEL.normalize_var_id("precip_total") == "precip_total"
    assert WPC_MODEL.normalize_var_id("total_precip") == "precip_total"
    assert WPC_MODEL.normalize_var_id("qpf") == "precip_total"
    assert WPC_MODEL.normalize_var_id("apcp") == "precip_total"