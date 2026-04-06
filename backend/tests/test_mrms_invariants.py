from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    assert buildable_var_keys == {"reflectivity", "mrms_radar_ptype"}

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
    assert payload["name"] == "Radar"
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
    assert reflectivity["display_name"] == "Base Reflectivity"
    assert reflectivity["order"] == 0
    assert reflectivity["group"] == "Radar"
    assert reflectivity["color_map_id"] == "mrms_reflectivity"

    mrms_radar_ptype = payload["variables"]["mrms_radar_ptype"]
    assert mrms_radar_ptype["var_key"] == "mrms_radar_ptype"
    assert mrms_radar_ptype["buildable"] is True
    assert mrms_radar_ptype["derived"] is False
    assert mrms_radar_ptype["kind"] == "discrete"
    assert mrms_radar_ptype["units"] == "dBZ"
    assert mrms_radar_ptype["display_name"] == "Reflectivity + Precip Type"
    assert mrms_radar_ptype["order"] == 1
    assert mrms_radar_ptype["group"] == "Radar"
    assert mrms_radar_ptype["color_map_id"] == "mrms_radar_ptype"


def test_mrms_aliases_normalize() -> None:
    assert MRMS_MODEL.normalize_var_id("reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("base_reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("merged_base_reflectivity_qc") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("mrms_reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("dbz") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("mrms_radar_ptype") == "mrms_radar_ptype"
    assert MRMS_MODEL.normalize_var_id("radar_ptype") == "mrms_radar_ptype"
    assert MRMS_MODEL.normalize_var_id("reflectivity_ptype") == "mrms_radar_ptype"


def test_mrms_capability_advertises_grid_substrate() -> None:
    capabilities = MRMS_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("mrms", capabilities)

    reflectivity = payload["variables"]["reflectivity"]
    assert reflectivity["render_substrates"] == ["grid"]
    assert payload["defaults"]["default_render_substrate"] == "grid"


def test_mrms_radar_ptype_grid_packing_config() -> None:
    from app.services.grid import _PACKING_BY_MODEL_VAR

    key = ("mrms", "mrms_radar_ptype")
    assert key in _PACKING_BY_MODEL_VAR, (
        f"Expected grid packing config for {key}"
    )
    config = _PACKING_BY_MODEL_VAR[key]
    assert config["dtype"] == "uint8"
    assert config["scale"] == 1.0
    assert config["offset"] == 0.0
    assert config["nodata"] == 255


def test_mrms_reflectivity_grid_packing_config() -> None:
    from app.services.grid import _PACKING_BY_MODEL_VAR

    key = ("mrms", "reflectivity")
    assert key in _PACKING_BY_MODEL_VAR, (
        f"Expected grid packing config for {key}"
    )
    config = _PACKING_BY_MODEL_VAR[key]
    assert config["dtype"] == "uint8"
    assert config["scale"] == 0.5
    assert config["offset"] == -10.0
    assert config["nodata"] == 255
