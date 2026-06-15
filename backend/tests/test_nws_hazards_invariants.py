from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.nws_hazards import NWS_HAZARDS_MODEL
from app.models.serialization import serialize_model_capability
from app.services import nws_hazards


def test_mrms_warnings_overlay_filter_keeps_only_convective_and_precipitation_products() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "risk_label": "Flood Advisory",
                    "active_hazards": ["Flood Advisory"],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "risk_label": "Flood Watch",
                    "active_hazards": ["Flood Watch", "Severe Thunderstorm Watch"],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "risk_label": "Special Marine Warning",
                    "active_hazards": ["Special Marine Warning"],
                },
            },
        ],
    }

    filtered = nws_hazards.filter_geojson_for_mrms_warnings_overlay(payload)

    labels = [feature["properties"]["risk_label"] for feature in filtered["features"]]
    assert labels == ["Flood Watch", "Special Marine Warning"]


def test_nws_hazards_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = NWS_HAZARDS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"active"}
    assert capabilities.ui_defaults["default_var_key"] == "active"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["time_axis_mode"] == "valid"
    assert capabilities.ui_constraints["latest_only"] is True
    assert capabilities.ui_constraints["supports_sampling"] is False


def test_nws_hazards_capabilities_schema_snapshot_invariants() -> None:
    capabilities = NWS_HAZARDS_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("nws_hazards", capabilities)
    assert payload["model_id"] == "nws_hazards"
    assert payload["name"] == "NWS Hazards"
    assert payload["product"] == "hazard"
    assert payload["defaults"]["default_var_key"] == "active"
    assert payload["defaults"]["default_run"] == "latest"
    assert payload["constraints"]["time_axis_mode"] == "valid"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is False

    active = payload["variables"]["active"]
    assert active["display_name"] == "Active Hazards"
    assert active["render_substrates"] == ["vector"]


def test_nws_hazards_aliases_normalize() -> None:
    assert NWS_HAZARDS_MODEL.normalize_var_id("active") == "active"
    assert NWS_HAZARDS_MODEL.normalize_var_id("hazards") == "active"
    assert NWS_HAZARDS_MODEL.normalize_var_id("alerts") == "active"
    assert NWS_HAZARDS_MODEL.normalize_var_id("active_hazards") == "active"