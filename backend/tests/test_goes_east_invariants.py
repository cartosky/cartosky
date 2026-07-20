from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.goes_east import GOES_EAST_MODEL
from app.models.serialization import serialize_model_capability


def test_goes_east_capabilities_disable_sampling() -> None:
    capabilities = GOES_EAST_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.ui_constraints["time_axis_mode"] == "observed"
    assert capabilities.ui_constraints["latest_only"] is True
    assert capabilities.ui_constraints["supports_sampling"] is False


def test_goes_east_includes_wv9_catalog_entry_and_aliases() -> None:
    capabilities = GOES_EAST_MODEL.capabilities
    assert capabilities is not None

    wv9 = capabilities.variable_catalog["wv9"]
    assert wv9.name == "Mid-Level Water Vapor"
    assert wv9.color_map_id == "goes_wv9_enhanced"
    assert GOES_EAST_MODEL.normalize_var_id("water_vapor") == "wv9"
    assert GOES_EAST_MODEL.normalize_var_id("band9") == "wv9"
    assert GOES_EAST_MODEL.normalize_var_id("c09") == "wv9"


def test_goes_east_serialized_capabilities_disable_sampling() -> None:
    capabilities = GOES_EAST_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("goes-east", capabilities)

    assert payload["model_id"] == "goes-east"
    assert payload["constraints"]["time_axis_mode"] == "observed"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is False
