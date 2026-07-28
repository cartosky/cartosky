"""Audited variable-scope classifier for packed grid variables.

Extracted from the retired ``scripts/canary_binary_sampler.py`` (the
COG-vs-binary shadow-comparison tool, deleted with the value-COG substrate;
recoverable from git history). The partition it computes — in-scope /
non-buildable / dead-alias / uncataloged — is the Phase G audit contract that
``test_grid_value_decode.py`` and ``test_binary_sampler_parity.py`` pin, so it
lives on as shared test support.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.registry import MODEL_REGISTRY
from app.services.grid import _PACKING_BY_MODEL_VAR

logger = logging.getLogger(__name__)


def _normalize_model(model: str) -> str:
    return str(model or "").strip().lower()


def _capability_catalog_for_model(model: str) -> dict[str, Any]:
    plugin = MODEL_REGISTRY.get(_normalize_model(model))
    catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", None)
    return catalog if isinstance(catalog, dict) else {}


def _companion_published_vars(catalog: dict[str, Any]) -> set[str]:
    """Variables published as ``companion_vars`` of a buildable catalog entry.

    The scheduler appends companions of every buildable variable to its build
    targets, so these are independently published grid frames even when their
    own capability says ``buildable=False`` (e.g. client-composited layers).
    """
    published: set[str] = set()
    for capability in catalog.values():
        if not bool(getattr(capability, "buildable", False)):
            continue
        frontend = getattr(capability, "frontend", None)
        companions = frontend.get("companion_vars") if isinstance(frontend, dict) else None
        if isinstance(companions, list):
            published.update(
                c.strip() for c in companions if isinstance(c, str) and c.strip()
            )
    return published


def _ensemble_artifact_published_vars(catalog: dict[str, Any]) -> set[str]:
    """Runtime artifacts published via a buildable entry's ``artifact_map``.

    Ensemble models (GEFS, EPS) publish a buildable variable's frames under
    the runtime id resolved through ``ensemble.artifact_map`` (e.g. ``tmp2m``
    -> ``tmp2m__mean`` for the "mean" view). Those runtime ids have their own
    capability entry with ``buildable=False``, but the frames are real
    independently published artifacts on both substrates. Only views listed
    in the entry's ``ensemble.supported_views`` are reachable at runtime, so
    mapped values for other views are ignored.
    """
    published: set[str] = set()
    for capability in catalog.values():
        if not bool(getattr(capability, "buildable", False)):
            continue
        ensemble = getattr(capability, "ensemble", None)
        if not isinstance(ensemble, dict):
            continue
        artifact_map = ensemble.get("artifact_map")
        if not isinstance(artifact_map, dict) or not artifact_map:
            continue
        raw_views = ensemble.get("supported_views")
        views = raw_views if isinstance(raw_views, (list, tuple)) else []
        for view in views:
            normalized_view = str(view or "").strip().lower()
            if not normalized_view:
                continue
            resolved = artifact_map.get(normalized_view)
            if isinstance(resolved, str) and resolved.strip():
                published.add(resolved.strip())
    return published


def _ensemble_dead_alias_vars(catalog: dict[str, Any]) -> set[str]:
    """Buildable ids whose runtime resolution redirects to a different artifact.

    The scheduler resolves every build target through ``resolve_runtime_var_id``
    before writing, so a buildable entry whose ``ensemble.artifact_map`` maps
    every reachable view (per its ``supported_views``) to some *other* var id
    is never written under its own name — it is a runtime alias with no frames
    on disk on either substrate (e.g. GEFS/EPS ``tmp2m_anom`` vs the published
    ``tmp2m_anom__mean``). Entries without an ``artifact_map`` never redirect
    and are unaffected.
    """
    dead: set[str] = set()
    for var_key, capability in catalog.items():
        if not bool(getattr(capability, "buildable", False)):
            continue
        ensemble = getattr(capability, "ensemble", None)
        if not isinstance(ensemble, dict):
            continue
        artifact_map = ensemble.get("artifact_map")
        if not isinstance(artifact_map, dict) or not artifact_map:
            continue
        raw_views = ensemble.get("supported_views")
        views = raw_views if isinstance(raw_views, (list, tuple)) else []
        mapped: set[str] = set()
        for view in views:
            normalized_view = str(view or "").strip().lower()
            if not normalized_view:
                continue
            resolved = artifact_map.get(normalized_view)
            if isinstance(resolved, str) and resolved.strip():
                mapped.add(resolved.strip())
        if mapped and str(var_key).strip() not in mapped:
            dead.add(str(var_key).strip())
    return dead


def _split_scope_by_buildable(
    packed_vars: list[str],
    catalog: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split packed variables into
    (scope, excluded non-buildable, excluded dead-alias, excluded uncataloged).

    A variable is excluded as uncataloged when the model's capability catalog
    has no entry for it at all — checked first because it is the most
    fundamental failure: there is no capability to consult, so no publish
    path can vouch for it. This is distinct from the buckets below (which all
    reason from an existing entry); its typical root cause is a cross-model
    packing loop injecting a key for a model whose own catalog opted out
    (e.g. ecmwf's ``precip_16d_anom`` from the gfs-family precip-anom loop).

    A variable is excluded as non-buildable when its capability says
    ``buildable=False`` and it is neither companion-published nor
    ensemble-artifact-published: such variables are derive-strategy inputs
    consumed in-memory and never written to disk on either substrate.

    A variable is excluded as a dead alias when it is buildable but its own
    ``ensemble.artifact_map`` redirects every reachable view to a different
    artifact id: frames exist only under the redirected id, never this one
    (see ``_ensemble_dead_alias_vars``). The classes are kept separate so
    they stay distinguishable in the summary output.
    """
    published = _companion_published_vars(catalog) | _ensemble_artifact_published_vars(catalog)
    dead_aliases = _ensemble_dead_alias_vars(catalog)
    in_scope: list[str] = []
    excluded_non_buildable: list[str] = []
    excluded_dead_alias: list[str] = []
    excluded_uncataloged: list[str] = []
    for var in packed_vars:
        capability = catalog.get(var)
        if capability is None:
            excluded_uncataloged.append(var)
        elif var in published:
            # Frames exist under this id via another entry's publish path.
            in_scope.append(var)
        elif var in dead_aliases:
            excluded_dead_alias.append(var)
        elif bool(getattr(capability, "buildable", False)):
            in_scope.append(var)
        else:
            excluded_non_buildable.append(var)
    return in_scope, excluded_non_buildable, excluded_dead_alias, excluded_uncataloged


def _scope_for_model(model: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (scope, excluded non-buildable, excluded dead-alias,
    excluded uncataloged) for a model."""
    model_norm = _normalize_model(model)
    packed = sorted(
        var for (mdl, var) in _PACKING_BY_MODEL_VAR if mdl == model_norm
    )
    (
        in_scope,
        excluded_non_buildable,
        excluded_dead_alias,
        excluded_uncataloged,
    ) = _split_scope_by_buildable(packed, _capability_catalog_for_model(model_norm))
    if excluded_uncataloged:
        logger.info(
            "Excluded %d packed variable(s) with no capability catalog entry "
            "from %s comparison scope: %s",
            len(excluded_uncataloged), model_norm, ", ".join(excluded_uncataloged),
        )
    if excluded_non_buildable:
        logger.info(
            "Excluded %d non-buildable, never-published variable(s) from %s "
            "comparison scope: %s",
            len(excluded_non_buildable), model_norm, ", ".join(excluded_non_buildable),
        )
    if excluded_dead_alias:
        logger.info(
            "Excluded %d buildable dead-alias variable(s) from %s comparison "
            "scope (published only under their artifact_map runtime id): %s",
            len(excluded_dead_alias), model_norm, ", ".join(excluded_dead_alias),
        )
    return in_scope, excluded_non_buildable, excluded_dead_alias, excluded_uncataloged
