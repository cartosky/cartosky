from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import _resolve_requested_ensemble_view, _runtime_var_id_for_request, _serialize_model_capability
from app.models.eps import EPS_MODEL


def test_eps_target_fhs_invariants() -> None:
    expected = list(range(0, 361, 6))
    assert EPS_MODEL.target_fhs(0) == expected
    assert EPS_MODEL.target_fhs(12) == expected


def test_eps_alias_and_herbie_request_invariants() -> None:
    assert EPS_MODEL.normalize_var_id("tmp2m") == "tmp2m"
    assert EPS_MODEL.normalize_var_id("t2m") == "tmp2m"
    assert EPS_MODEL.normalize_var_id("2t") == "tmp2m"
    assert EPS_MODEL.default_ensemble_view("tmp2m") == "mean"
    assert EPS_MODEL.supported_ensemble_views("tmp2m") == ["mean"]
    assert EPS_MODEL.resolve_runtime_var_id("tmp2m", "mean") == "tmp2m__mean"

    request = EPS_MODEL.herbie_request(product="enfo", var_key="tmp2m", ensemble_view="mean")
    assert request.model == "ifs"
    assert request.product == "enfo"
    assert request.herbie_kwargs["_cartosky_fetch_aggregation"] == "ecmwf_pf_mean"


def test_eps_buildable_var_set_and_defaults_invariants() -> None:
    capabilities = EPS_MODEL.capabilities
    assert capabilities is not None

    buildable_var_keys = {
        var_key
        for var_key, capability in capabilities.variable_catalog.items()
        if capability.buildable
    }
    assert buildable_var_keys == {"tmp2m"}
    assert capabilities.ui_defaults["default_var_key"] == "tmp2m"
    assert capabilities.ui_defaults["default_ensemble_view"] == "mean"
    assert capabilities.canonical_region == "conus"
    assert capabilities.grid_meters_by_region == {"conus": 18000.0}


def test_eps_capabilities_schema_snapshot_invariants() -> None:
    capabilities = EPS_MODEL.capabilities
    assert capabilities is not None
    payload = _serialize_model_capability("eps", capabilities)

    assert payload["model_id"] == "eps"
    assert payload["name"] == "EPS"
    assert payload["product"] == "enfo"
    assert payload["ensemble"]["default_view"] == "mean"
    assert payload["ensemble"]["supported_views"] == ["mean"]
    assert "tmp2m__mean" not in payload["variables"]

    tmp2m = payload["variables"]["tmp2m"]
    assert tmp2m["var_key"] == "tmp2m"
    assert tmp2m["display_name"] == "Surface Temp (Mean)"
    assert tmp2m["buildable"] is True
    assert tmp2m["color_map_id"] == "tmp2m"
    assert tmp2m["default_fh"] == 0
    assert tmp2m["group"] == "Temperature"
    assert tmp2m["ensemble"]["default_view"] == "mean"
    assert tmp2m["ensemble"]["supported_views"] == ["mean"]


def test_eps_runtime_resolution_helpers() -> None:
    assert _resolve_requested_ensemble_view("eps", "tmp2m", None) == "mean"
    assert _runtime_var_id_for_request("eps", "tmp2m", "mean") == "tmp2m__mean"
    try:
        _resolve_requested_ensemble_view("eps", "tmp2m", "spread")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Expected unsupported EPS ensemble view to raise HTTPException")