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
    assert buildable_var_keys == {
        "reflectivity",
        "mrms_radar_ptype",
        "mrms_recent_precip_6h",
        "mrms_recent_precip_24h",
        "mrms_recent_precip_72h",
    }

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
    assert payload["name"] == "MRMS"
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
    assert reflectivity["group"] == "Radar"
    assert reflectivity["color_map_id"] == "mrms_reflectivity"

    mrms_radar_ptype = payload["variables"]["mrms_radar_ptype"]
    assert mrms_radar_ptype["var_key"] == "mrms_radar_ptype"
    assert mrms_radar_ptype["buildable"] is True
    assert mrms_radar_ptype["derived"] is False
    assert mrms_radar_ptype["kind"] == "discrete"
    assert mrms_radar_ptype["units"] == "dBZ"
    assert mrms_radar_ptype["display_name"] == "Reflectivity + Ptype"
    assert mrms_radar_ptype["group"] == "Radar"
    assert mrms_radar_ptype["color_map_id"] == "mrms_radar_ptype"

    recent_precip_6h = payload["variables"]["mrms_recent_precip_6h"]
    assert recent_precip_6h["var_key"] == "mrms_recent_precip_6h"
    assert recent_precip_6h["buildable"] is True
    assert recent_precip_6h["derived"] is False
    assert recent_precip_6h["kind"] == "continuous"
    assert recent_precip_6h["units"] == "in"
    assert recent_precip_6h["display_name"] == "Recent Precip (6h)"
    assert recent_precip_6h["group"] == "Precipitation"
    assert recent_precip_6h["color_map_id"] == "mrms_recent_precip_6h"

    recent_precip_24h = payload["variables"]["mrms_recent_precip_24h"]
    assert recent_precip_24h["display_name"] == "Recent Precip (24h)"
    assert recent_precip_24h["group"] == "Precipitation"
    assert recent_precip_24h["color_map_id"] == "mrms_recent_precip_24h"

    recent_precip_72h = payload["variables"]["mrms_recent_precip_72h"]
    assert recent_precip_72h["display_name"] == "Recent Precip (72h)"
    assert recent_precip_72h["group"] == "Precipitation"
    assert recent_precip_72h["color_map_id"] == "mrms_recent_precip_72h"


def test_mrms_aliases_normalize() -> None:
    assert MRMS_MODEL.normalize_var_id("reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("base_reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("merged_base_reflectivity_qc") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("mrms_reflectivity") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("dbz") == "reflectivity"
    assert MRMS_MODEL.normalize_var_id("mrms_radar_ptype") == "mrms_radar_ptype"
    assert MRMS_MODEL.normalize_var_id("radar_ptype") == "mrms_radar_ptype"
    assert MRMS_MODEL.normalize_var_id("reflectivity_ptype") == "mrms_radar_ptype"
    assert MRMS_MODEL.normalize_var_id("mrms_recent_precip_6h") == "mrms_recent_precip_6h"
    assert MRMS_MODEL.normalize_var_id("recent_precip_6h") == "mrms_recent_precip_6h"
    assert MRMS_MODEL.normalize_var_id("mrms_recent_precip_24h") == "mrms_recent_precip_24h"
    assert MRMS_MODEL.normalize_var_id("recent_precip_24h") == "mrms_recent_precip_24h"
    assert MRMS_MODEL.normalize_var_id("mrms_recent_precip_72h") == "mrms_recent_precip_72h"
    assert MRMS_MODEL.normalize_var_id("recent_precip_72h") == "mrms_recent_precip_72h"

def test_mrms_capability_advertises_grid_substrate() -> None:
    capabilities = MRMS_MODEL.capabilities
    assert capabilities is not None

    payload = serialize_model_capability("mrms", capabilities)

    reflectivity = payload["variables"]["reflectivity"]
    assert reflectivity["render_substrates"] == ["grid"]
    assert payload["variables"]["mrms_recent_precip_24h"]["render_substrates"] == ["grid"]
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


@pytest.mark.parametrize(
    ("var_key", "expected_dtype", "expected_scale", "expected_offset", "expected_nodata"),
    [
        ("mrms_recent_precip_6h", "uint16", 0.01, 0.0, 65535),
        ("mrms_recent_precip_24h", "uint16", 0.01, 0.0, 65535),
        ("mrms_recent_precip_72h", "uint16", 0.01, 0.0, 65535),
    ],
)
def test_mrms_recent_precip_grid_packing_config(
    var_key: str,
    expected_dtype: str,
    expected_scale: float,
    expected_offset: float,
    expected_nodata: int,
) -> None:
    from app.services.grid import _PACKING_BY_MODEL_VAR

    key = ("mrms", var_key)
    assert key in _PACKING_BY_MODEL_VAR, f"Expected grid packing config for {key}"
    config = _PACKING_BY_MODEL_VAR[key]
    assert config["dtype"] == expected_dtype
    assert config["scale"] == expected_scale
    assert config["offset"] == expected_offset
    assert config["nodata"] == expected_nodata
    assert config["units"] == "in"


def test_mrms_reflectivity_palette_uses_nws_enhanced_low_end() -> None:
    from app.services.colormaps import get_color_map_spec

    spec = get_color_map_spec("mrms_reflectivity")
    levels = spec["levels"]
    colors = spec["colors"]

    assert levels[0] == 5.0
    assert levels[-1] == 80.0
    assert colors[levels.index(5.0)] == "#04e9e7"
    assert colors[levels.index(10.0)] == "#019ff4"
    assert colors[levels.index(15.0)] == "#0300f4"
    assert colors[levels.index(20.0)] == "#02fd02"
    assert colors[levels.index(35.0)] == "#fdf802"
    assert colors[levels.index(65.0)] == "#f800fd"
    assert spec["transparent_below_min"] is True


def test_modeled_reflectivity_palette_helper_shapes() -> None:
    from app.services.colormaps import _build_modeled_reflectivity_palette

    for ptype in ("rain", "frzr", "sleet", "snow"):
        levels, colors = _build_modeled_reflectivity_palette(ptype)
        assert len(levels) in {len(colors), len(colors) + 1}, f"{ptype}: level/color length mismatch"
        if ptype == "rain":
            assert levels[0] == 8.0
            assert levels[-1] == 72.0
            assert colors[0] == "#ffffff"
            assert colors[-1] == "#fdfdfd"
        else:
            assert levels[0] == 5.0
            assert levels[-1] == 70.0
        assert all(color.startswith("#") for color in colors)


def test_mrms_radar_ptype_palette_reserves_blue_for_snow() -> None:
    from app.services.colormaps import get_color_map_spec

    spec = get_color_map_spec("mrms_radar_ptype")
    colors = spec["colors"]
    breaks = spec["ptype_breaks"]

    rain_break = breaks["rain"]
    snow_break = breaks["snow"]
    rain_colors = colors[rain_break["offset"]: rain_break["offset"] + rain_break["count"]]
    snow_colors = colors[snow_break["offset"]: snow_break["offset"] + snow_break["count"]]

    assert rain_colors[:4] == ["#d7f7cf", "#9cf29a", "#4be85a", "#02fd02"]
    assert rain_colors[9] == "#fdf802"
    assert rain_colors[-2:] == ["#f800fd", "#fdfdfd"]
    assert snow_colors[:4] == ["#ffffff", "#55ffff", "#4feaff", "#48d3ff"]
    assert "#019ff4" not in rain_colors
    assert "#0300f4" not in rain_colors
