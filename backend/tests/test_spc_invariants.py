from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.serialization import serialize_model_capability
from app.models.spc import SPC_MODEL


def test_spc_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = SPC_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"convective"}

    assert capabilities.ui_defaults["default_var_key"] == "convective"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_defaults["default_frame_selection"] == "first"
    assert capabilities.ui_constraints["time_axis_mode"] == "valid"
    assert capabilities.ui_constraints["latest_only"] is True
    assert capabilities.ui_constraints["supports_sampling"] is False
    assert capabilities.canonical_region == "conus"


def test_spc_capabilities_schema_snapshot_invariants() -> None:
    capabilities = SPC_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("spc", capabilities)

    assert payload["model_id"] == "spc"
    assert payload["name"] == "SPC"
    assert payload["product"] == "outlook"
    assert payload["canonical_region"] == "conus"
    assert payload["defaults"]["default_var_key"] == "convective"
    assert payload["defaults"]["default_run"] == "latest"
    assert payload["defaults"]["default_frame_selection"] == "first"
    assert payload["defaults"]["default_render_substrate"] == "vector"
    assert payload["constraints"]["time_axis_mode"] == "valid"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is False

    convective = payload["variables"]["convective"]
    assert convective["var_key"] == "convective"
    assert convective["buildable"] is True
    assert convective["derived"] is False
    assert convective["kind"] == "categorical"
    assert convective["display_name"] == "SPC Convective Outlook"
    assert convective["group"] == "Outlooks"
    assert convective["render_substrates"] == ["vector"]


def test_spc_aliases_normalize() -> None:
    assert SPC_MODEL.normalize_var_id("convective") == "convective"
    assert SPC_MODEL.normalize_var_id("categorical") == "convective"
    assert SPC_MODEL.normalize_var_id("convective_outlook") == "convective"
    assert SPC_MODEL.normalize_var_id("spc_convective") == "convective"
    assert SPC_MODEL.normalize_var_id("day1_3_convective") == "convective"