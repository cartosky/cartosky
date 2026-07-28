"""Artifact-domain contract (Phase 2A).

A *domain* is the published build-region ID an artifact was produced for —
``conus`` for HRRR/NAM/NBM/WPC/CPC, ``na`` for GFS/GEFS/AIFS/ECMWF/EPS, and
(from Phase 3) ``global``. Today ``region`` is accepted and discarded
throughout the artifact path, so a global publish would overwrite the
canonical run in place. This module is the single authority that turns a
requested/declared region into a normalized domain ID and maps it onto the
physical artifact layout.

Layout (locked design §2 — "Option B, parallel domain run tree"):

    canonical:      {root}/{model}/...
    non-canonical:  {root}/{model}/domains/{d}/...

``domains`` is a reserved word precisely because it can never match
``RUN_ID_RE``, so every existing run scanner skips it unchanged. The
canonical branch is byte-for-byte today's path — with no model declaring
extra ``supported_build_regions`` (the 2A shipping state) nothing observable
changes.

Import-light on purpose: registry / grid-params imports are lazy so this
stays a leaf module with no cycles.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .run_ids import RUN_ID_RE

# A domain is an open, normalized string id — not an enum. New domains arrive
# as capability data (a RegionSpec + grid params + supported_build_regions),
# never as a code change here.
DomainId = str

#: Fallback canonical domain for models that declare no ``canonical_region``.
DEFAULT_CANONICAL_DOMAIN: DomainId = "conus"

#: The namespace segment separating non-canonical domain trees.
DOMAINS_NAMESPACE = "domains"

#: Ids no region may ever use (they would collide with the namespace itself).
RESERVED_DOMAIN_IDS = frozenset({DOMAINS_NAMESPACE})

# Domain ids become path segments and URL segments: keep them to a shape that
# cannot traverse, hide, or need escaping.
_DOMAIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def is_reserved_domain_id(domain: Any) -> bool:
    """Whether ``domain`` may never be used as a region/domain id.

    Reserved: the empty string, the ``domains`` namespace literal, anything
    that parses as a run id (it would be indistinguishable from a run dir),
    and anything outside the safe path-segment grammar.
    """
    token = _normalize_token(domain)
    if not token:
        return True
    if token in RESERVED_DOMAIN_IDS:
        return True
    if RUN_ID_RE.match(token) is not None:
        return True
    return _DOMAIN_ID_RE.match(token) is None


def _resolve_plugin(plugin_or_model: Any) -> Any:
    if not isinstance(plugin_or_model, str):
        return plugin_or_model
    from ..models.registry import MODEL_REGISTRY

    return MODEL_REGISTRY.get(_normalize_token(plugin_or_model))


def canonical_domain(plugin_or_capabilities: Any) -> DomainId:
    """The canonical domain for a model id, plugin, or capabilities object.

    Single authority replacing ``scheduler._default_build_region`` and
    ``main._model_canonical_region``.
    """
    target = _resolve_plugin(plugin_or_capabilities)
    capabilities = getattr(target, "capabilities", None)
    if capabilities is None:
        capabilities = target
    configured = _normalize_token(getattr(capabilities, "canonical_region", ""))
    return configured or DEFAULT_CANONICAL_DOMAIN


def normalize_domain(model_id: Any, domain: str | None) -> DomainId:
    """Normalize a requested domain for a model.

    ``None``/``""`` resolve to the model's canonical domain — this is the
    chokepoint that makes "absent ``domain=`` resolves exactly as today"
    true, and makes ``?domain=na`` on GFS identical to no domain at all.
    """
    token = _normalize_token(domain)
    if not token:
        return canonical_domain(model_id)
    return token


def is_canonical(model_id: Any, domain: str | None) -> bool:
    return normalize_domain(model_id, domain) == canonical_domain(model_id)


def _grid_params_available(model_id: str, domain: DomainId) -> bool:
    try:
        from .builder.raster_grid import get_grid_params

        get_grid_params(model_id, domain)
    except Exception:
        return False
    return True


def declared_domains_for_var(plugin: Any, var_key: str) -> tuple[DomainId, ...]:
    """Domains a variable is declared buildable for — canonical first.

    Extras come from ``VariableCapability.supported_build_regions`` and are
    kept only when the plugin actually defines the region and grid params
    exist for it. In the 2A shipping state no model declares extras, so this
    returns exactly ``(canonical,)``.
    """
    resolved = _resolve_plugin(plugin)
    canonical = canonical_domain(resolved)
    domains: list[DomainId] = [canonical]

    get_var_capability = getattr(resolved, "get_var_capability", None)
    capability = get_var_capability(var_key) if callable(get_var_capability) else None
    declared = getattr(capability, "supported_build_regions", None)
    if not isinstance(declared, (list, tuple)):
        return tuple(domains)

    model_id = _normalize_token(getattr(resolved, "id", ""))
    get_region = getattr(resolved, "get_region", None)
    for raw in declared:
        token = _normalize_token(raw)
        if not token or token in domains or is_reserved_domain_id(token):
            continue
        if callable(get_region) and get_region(token) is None:
            continue
        if not _grid_params_available(model_id, token):
            continue
        domains.append(token)
    return tuple(domains)


def declared_domains_for_model(plugin_or_model: Any) -> tuple[DomainId, ...]:
    """Union of :func:`declared_domains_for_var` over a model's catalog."""
    resolved = _resolve_plugin(plugin_or_model)
    canonical = canonical_domain(resolved)
    domains: list[DomainId] = [canonical]
    catalog = getattr(getattr(resolved, "capabilities", None), "variable_catalog", None)
    if isinstance(catalog, dict):
        for var_key in catalog:
            for domain in declared_domains_for_var(resolved, var_key):
                if domain not in domains:
                    domains.append(domain)
    return tuple(domains)


def validate_requested_domain(model_id: Any, domain: str | None) -> DomainId | None:
    """Validate an API-supplied ``domain=``.

    Returns the normalized domain, or ``None`` when the request names a
    domain this model does not publish — callers map that to their existing
    404 bodies. Absent/empty resolves to the canonical domain.
    """
    token = _normalize_token(domain)
    if not token:
        return canonical_domain(model_id)
    if is_reserved_domain_id(token):
        return None
    if token == canonical_domain(model_id):
        return token
    if token in declared_domains_for_model(model_id):
        return token
    return None


def domain_scoped_model_root(
    root: Path,
    model: str,
    domain: DomainId,
    *,
    canonical: DomainId,
) -> Path:
    """``{root}/{model}`` for the canonical domain, ``{root}/{model}/domains/{d}``
    otherwise.

    The canonical branch is today's literal path, byte-for-byte.
    """
    base = Path(root) / model
    normalized = _normalize_token(domain)
    if not normalized or normalized == _normalize_token(canonical):
        return base
    return base / DOMAINS_NAMESPACE / normalized


def model_root_for_domain(root: Path, model: str, domain: str | None = None) -> Path:
    """Convenience wrapper: normalize ``domain`` for ``model``, then scope.

    Every path helper in the artifact layer routes through this, so the
    canonical/non-canonical branch is written once.
    """
    return domain_scoped_model_root(
        root,
        model,
        normalize_domain(model, domain),
        canonical=canonical_domain(model),
    )


def domain_url_prefix(model_id: Any, domain: str | None) -> str:
    """URL path fragment inserted immediately before ``{model}``.

    Empty for the canonical domain — canonical artifact URLs contain neither
    a ``domains/`` segment nor ``domain=``.
    """
    if is_canonical(model_id, domain):
        return ""
    return f"{DOMAINS_NAMESPACE}/{normalize_domain(model_id, domain)}/"
