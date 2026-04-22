from __future__ import annotations

from typing import Any

from ..services.render_resampling import display_resampling_override


def _render_substrates_for_variable(model_id: str, capability: Any | None) -> list[str]:
    del model_id
    configured = getattr(capability, "render_substrates", None) if capability is not None else None
    if isinstance(configured, (list, tuple)):
        normalized: list[str] = []
        for item in configured:
            substrate = str(item or "").strip().lower()
            if substrate and substrate not in normalized:
                normalized.append(substrate)
        if normalized:
            return normalized
    return ["grid"]


def serialize_variable_capability(model_id: str, capability: Any) -> dict[str, Any]:
    constraints = getattr(capability, "constraints", None)
    constraints_payload = dict(constraints) if isinstance(constraints, dict) else {}
    supported_build_regions = getattr(capability, "supported_build_regions", None)
    supported_build_regions_payload = [
        str(region).strip().lower()
        for region in supported_build_regions
        if str(region).strip()
    ] if isinstance(supported_build_regions, list) else []
    var_key = str(getattr(capability, "var_key", ""))
    ensemble = getattr(capability, "ensemble", None)
    ensemble_payload = dict(ensemble) if isinstance(ensemble, dict) else {}
    ensemble_payload.pop("artifact_map", None)
    payload = {
        "var_key": var_key,
        "display_name": str(getattr(capability, "name", "")),
        "kind": getattr(capability, "kind", None),
        "units": getattr(capability, "units", None),
        "order": getattr(capability, "order", None),
        "group": getattr(capability, "group", None),
        "default_fh": getattr(capability, "default_fh", None),
        "buildable": bool(getattr(capability, "buildable", False)),
        "color_map_id": getattr(capability, "color_map_id", None),
        "display_resampling_override": display_resampling_override(model_id, var_key),
        "render_substrates": _render_substrates_for_variable(model_id, capability),
        "supported_build_regions": supported_build_regions_payload,
        "constraints": constraints_payload,
        "derived": bool(getattr(capability, "derived", False)),
        "derive_strategy_id": getattr(capability, "derive_strategy_id", None),
    }
    if ensemble_payload:
        payload["ensemble"] = ensemble_payload
    return payload


def serialize_model_capability(model_id: str, capability: Any) -> dict[str, Any]:
    variable_catalog = getattr(capability, "variable_catalog", {}) or {}
    ordered_items = sorted(
        (
            (var_key, var_capability)
            for var_key, var_capability in variable_catalog.items()
            if not bool(
                isinstance(getattr(var_capability, "frontend", None), dict)
                and getattr(var_capability, "frontend", {}).get("internal_only")
            )
        ),
        key=lambda item: (
            getattr(item[1], "order", None) is None,
            getattr(item[1], "order", 0) if getattr(item[1], "order", None) is not None else 0,
            item[0],
        ),
    )
    variables_payload = {
        var_key: serialize_variable_capability(model_id, var_capability)
        for var_key, var_capability in ordered_items
    }

    defaults = getattr(capability, "ui_defaults", None)
    constraints = getattr(capability, "ui_constraints", None)
    run_discovery = getattr(capability, "run_discovery", None)
    ensemble = getattr(capability, "ensemble", None)
    defaults_payload = dict(defaults) if isinstance(defaults, dict) else {}
    default_var_key = str(defaults_payload.get("default_var_key") or "").strip()
    default_var_capability = variable_catalog.get(default_var_key) if isinstance(variable_catalog, dict) else None
    default_substrates = _render_substrates_for_variable(model_id, default_var_capability)
    defaults_payload["default_render_substrate"] = default_substrates[0] if default_substrates else "grid"
    payload = {
        "model_id": model_id,
        "name": str(getattr(capability, "name", model_id.upper())),
        "product": getattr(capability, "product", None),
        "canonical_region": getattr(capability, "canonical_region", None),
        "defaults": defaults_payload,
        "constraints": dict(constraints) if isinstance(constraints, dict) else {},
        "run_discovery": dict(run_discovery) if isinstance(run_discovery, dict) else {},
        "variables": variables_payload,
    }
    if isinstance(ensemble, dict) and ensemble:
        payload["ensemble"] = dict(ensemble)
    return payload
