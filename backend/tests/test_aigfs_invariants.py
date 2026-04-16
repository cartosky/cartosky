from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _serialize_model_capability
from app.models.aigfs import AIGFS_MODEL


def test_aigfs_run_discovery_invariants() -> None:
    capabilities = AIGFS_MODEL.capabilities
    assert capabilities is not None
    assert capabilities.run_discovery == {
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["nomads"],
    }


def test_aigfs_target_fhs_invariants() -> None:
    expected = list(range(0, 385, 6))
    assert AIGFS_MODEL.target_fhs(0) == expected
    assert AIGFS_MODEL.target_fhs(12) == expected


def test_aigfs_alias_and_herbie_request_invariants() -> None:
    assert AIGFS_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert AIGFS_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert AIGFS_MODEL.normalize_var_id("2t") == "tmp2m"

    request = AIGFS_MODEL.herbie_request(product="sfc", var_key="tmp2m")
    assert request.model == "aigfs"
    assert request.product == "sfc"
    assert request.herbie_kwargs["priority"] == ["nomads"]


def test_aigfs_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = AIGFS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m"}

    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_run"] == "latest"
    assert capabilities.ui_constraints["supports_sampling"] is True
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {
        "conus": 25000.0,
    }

    tmp2m_spec = AIGFS_MODEL.get_var("tmp2m")
    assert tmp2m_spec is not None
    assert tmp2m_spec.primary is True
    assert tmp2m_spec.derived is False
    assert tmp2m_spec.kind == "continuous"
    assert tmp2m_spec.units == "F"
    assert tmp2m_spec.selectors.search == [":TMP:2 m above ground:"]
    assert tmp2m_spec.selectors.filter_by_keys == {
        "typeOfLevel": "heightAboveGround",
        "level": "2",
    }


def test_aigfs_capabilities_schema_snapshot_invariants() -> None:
    capabilities = AIGFS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("aigfs", capabilities)

    assert payload["model_id"] == "aigfs"
    assert payload["name"] == "AIGFS"
    assert payload["product"] == "sfc"
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