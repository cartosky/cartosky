from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.base import ModelCapabilities
from app.models.serialization import serialize_model_capability


def test_serialize_model_capability_preserves_observed_source_contract_fields() -> None:
    capabilities = ModelCapabilities(
        model_id="mrms",
        name="MRMS Radar",
        product="obs",
        canonical_region="conus",
        ui_defaults={
            "default_var_key": "reflectivity",
            "default_run": "latest",
            "default_frame_selection": "latest",
        },
        ui_constraints={
            "canonical_region": "conus",
            "time_axis_mode": "observed",
            "latest_only": True,
            "supports_sampling": True,
        },
        variable_catalog={},
    )

    payload = serialize_model_capability("mrms", capabilities)

    assert payload["model_id"] == "mrms"
    assert payload["product"] == "obs"
    assert payload["defaults"]["default_var_key"] == "reflectivity"
    assert payload["defaults"]["default_run"] == "latest"
    assert payload["defaults"]["default_frame_selection"] == "latest"
    assert payload["constraints"]["canonical_region"] == "conus"
    assert payload["constraints"]["time_axis_mode"] == "observed"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is True
