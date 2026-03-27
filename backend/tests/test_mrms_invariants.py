from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.mrms import MRMS_MODEL
from app.models.serialization import serialize_model_capability


def test_mrms_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = MRMS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"reflectivity"}

    assert capabilities.ui_defaults["default_var_key"] == "reflectivity"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_defaults["default_frame_selection"] == "latest"
    assert capabilities.ui_constraints["time_axis_mode"] == "observed"
    assert capabilities.ui_constraints["latest_only"] is True
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 1000.0,
    }


def test_mrms_capabilities_schema_snapshot_invariants() -> None:
    capabilities = MRMS_MODEL.capabilities
    assert capabilities is not None
    payload = serialize_model_capability("mrms", capabilities)

    assert payload["model_id"] == "mrms"
    assert payload["name"] == "MRMS Radar"
    assert payload["product"] == "obs"
    assert payload["canonical_region"] == "conus"
    assert payload["defaults"]["default_var_key"] == "reflectivity"
    assert payload["defaults"]["default_run"] == "latest"
    assert payload["defaults"]["default_frame_selection"] == "latest"
    assert payload["constraints"]["time_axis_mode"] == "observed"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is True

    reflectivity = payload["variables"]["reflectivity"]
    assert reflectivity["var_key"] == "reflectivity"
    assert reflectivity["buildable"] is True
    assert reflectivity["derived"] is False
    assert reflectivity["kind"] == "discrete"
    assert reflectivity["units"] == "dBZ"
    assert reflectivity["display_name"] == "Merged Base Reflectivity QC"
    assert reflectivity["order"] == 0
    assert reflectivity["group"] == "Radar"
    assert reflectivity["color_map_id"] == "mrms_reflectivity"


def test_mrms_aliases_normalize() -> None:
    assert MRMS_MODEL.normalize_var_id("reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("base_reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("merged_base_reflectivity_qc") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("mrms_reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("dbz") == "reflectivity"
