from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.rtma_ru import CURRENT_ANALYSIS_MODEL
from app.models.serialization import serialize_model_capability


def test_current_analysis_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = CURRENT_ANALYSIS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m", "dp2m", "wspd10m", "wgst10m"}
    assert capabilities.variable_catalog["spres"].buildable is False

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_defaults["default_frame_selection"] == "latest"
    assert capabilities.ui_constraints["time_axis_mode"] == "observed"
    assert capabilities.ui_constraints["latest_only"] is True
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {"conus": 2500.0}


def test_current_analysis_capabilities_schema_snapshot_invariants() -> None:
    capabilities = CURRENT_ANALYSIS_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("current_analysis", capabilities)

    assert payload["model_id"] == "current_analysis"
    assert payload["name"] == "Current Analysis"
    assert payload["product"] == "obs"
    assert payload["canonical_region"] == "conus"
    assert payload["defaults"]["default_var_key"] == "tmp2m"
    assert payload["defaults"]["default_run"] == "latest"
    assert payload["defaults"]["default_frame_selection"] == "latest"
    assert payload["constraints"]["time_axis_mode"] == "observed"
    assert payload["constraints"]["latest_only"] is True
    assert payload["constraints"]["supports_sampling"] is True

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["display_name"] == "Temperature"
    assert tmp2m["group"] == "Surface"
    assert tmp2m["order"] == 0
    assert tmp2m["color_map_id"] == "tmp2m"
    assert tmp2m["render_substrates"] == ["grid"]

    dp2m = payload["variables"]["dp2m"]
    assert dp2m["display_name"] == "Dewpoint"
    assert dp2m["group"] == "Surface"
    assert dp2m["order"] == 1
    assert dp2m["color_map_id"] == "dp2m"

    wspd10m = payload["variables"]["wspd10m"]
    assert wspd10m["display_name"] == "Wind Speed"
    assert wspd10m["derived"] is True
    assert wspd10m["derive_strategy_id"] == "wspd10m"
    assert wspd10m["color_map_id"] == "wspd10m"

    wgst10m = payload["variables"]["wgst10m"]
    assert wgst10m["display_name"] == "Wind Gust"
    assert wgst10m["color_map_id"] == "wgst10m"

    spres = payload["variables"]["spres"]
    assert spres["display_name"] == "Surface Pressure"
    assert spres["units"] == "hPa"
    assert spres["group"] == "Surface"
    assert spres["color_map_id"] == "spres"
    assert spres["buildable"] is False


def test_current_analysis_aliases_normalize() -> None:
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("temperature") == "tmp2m"
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("td2m") == "dp2m"
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("dewpoint") == "dp2m"
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("wind_speed") == "wspd10m"
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("wind_gust") == "wgst10m"
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("slp") == "spres"
    assert CURRENT_ANALYSIS_MODEL.normalize_var_id("surface_pressure") == "spres"