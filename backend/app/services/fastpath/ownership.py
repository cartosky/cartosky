"""Per-``(variable, domain)`` source ownership (design §3).

The single most load-bearing rule of the fast-path integration:

    **A ``(variable, domain)`` pair is built exactly once, by its owner, and
    the delayed path skips fast-owned pairs by CONFIG — not by "skip if
    already present".**

Skip-by-config matters operationally: if the fast path stalls, the delayed
build must *not* silently pick the pairs back up at its own build window (that
is the surprise double memory load design §3 rule 2 calls out). Recovery is the
explicit failover transition in :mod:`.state`, which revokes ownership first.

Resolution order
================

1. ``CARTOSKY_FASTPATH_MODELS`` — comma-separated model allowlist, **empty
   default**. Unset/empty ⇒ :func:`source_for` returns ``delayed`` for
   everything and this module never imports ``sources.openmeteo``. Same
   pattern and kill-switch semantics as ``CARTOSKY_MEMBER_PUBLISH_MODELS`` /
   ``CARTOSKY_SOUNDING_MODELS`` (``app.config``).
2. The model's launch set, read from the Open-Meteo catalog so the ownership
   table stays *data* (design §2 model-agnosticism): every
   ``publishable_variables()`` entry plus every ``derived_variables`` entry,
   crossed with the catalog's ``fast_domains``. For ECMWF that is
   ``{tmp2m, dp2m, precip_total, snowfall_total, wgst10m, wspd10m} ×
   {na, global}``.
3. **Intersected with the domains this environment actually enables**, via the
   same ``domains.declared_domains_for_var`` chokepoint the delayed path uses
   (see "Domain enablement" below).
4. ``CARTOSKY_FASTPATH_VAR_OVERRIDES`` — the emergency per-pair lever, applied
   last so it can force a pair either way without a deploy. It cannot force a
   domain the environment has not enabled.

Domain enablement
=================

The catalog's ``fast_domains`` is a *declaration* ("this model's fast path
knows how to build these domains"), not a licence. Whether a non-canonical
domain like ``global`` is live is an environment decision owned by
``CARTOSKY_GLOBAL_DOMAIN_MODELS`` and enforced for the delayed path by
``domains.declared_domains_for_var``. The fast path must honour the same gate,
for two reasons:

* without it, an environment where ``global`` is off would still see the fast
  sub-loop publish global artifacts that the delayed path would never produce;
* worse, failover for a ``(var, global)`` pair would revoke ownership to a
  delayed builder that does not build that domain at all — the pair would
  simply stop, with nobody to hand it back to.

So every resolution below intersects with ``declared_domains_for_var``. With
``CARTOSKY_GLOBAL_DOMAIN_MODELS`` empty (the default) that returns
``(canonical,)``, so ECMWF's fast path is ``na``-only until global is switched
on for the environment — flipping the two flags is independent and order does
not matter.

Override grammar
================

Comma-separated ``model:var:domain=source`` triples::

    CARTOSKY_FASTPATH_VAR_OVERRIDES="ecmwf:tmp2m:na=delayed,ecmwf:dp2m:global=fast"

``domain`` accepts ``*`` for "every domain of that model"; ``source`` is
``fast`` or ``delayed``. A more specific entry (explicit domain) wins over a
``*`` entry. Malformed entries are logged and ignored rather than crashing a
scheduler at start-up — an unparseable kill switch must not take the model
down with it. Overrides apply only to models in the allowlist: with the
allowlist empty the flag is inert, so a stale override cannot resurrect the
fast path.

Both env reads are deliberately **not** cached: flipping ownership is an
emergency lever that has to take effect on the next scheduler poll, and the
reads are trivially cheap.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)

#: Ownership tokens. ``delayed`` is the Herbie/GRIB production path.
SOURCE_DELAYED = "delayed"
SOURCE_FAST = "fast"

VALID_SOURCES = frozenset({SOURCE_DELAYED, SOURCE_FAST})

ENV_FASTPATH_MODELS = "CARTOSKY_FASTPATH_MODELS"
ENV_FASTPATH_VAR_OVERRIDES = "CARTOSKY_FASTPATH_VAR_OVERRIDES"

#: Wildcard accepted in the ``domain`` position of an override.
DOMAIN_WILDCARD = "*"

__all__ = [
    "DOMAIN_WILDCARD",
    "ENV_FASTPATH_MODELS",
    "ENV_FASTPATH_VAR_OVERRIDES",
    "SOURCE_DELAYED",
    "SOURCE_FAST",
    "VALID_SOURCES",
    "fast_domains_for_model",
    "fast_owned_pairs",
    "fastpath_enabled",
    "fastpath_models",
    "is_fast_owned",
    "launch_set_pairs",
    "parse_var_overrides",
    "source_for",
]


def _token(value: object) -> str:
    return str(value or "").strip().lower()


def fastpath_models() -> frozenset[str]:
    """Models whose scheduler runs the fast-source sub-loop.

    Empty (the default) means the fast path is off everywhere and this process
    behaves exactly as it did before WP3 — no new imports, no new I/O.
    """
    raw = _token(os.environ.get(ENV_FASTPATH_MODELS))
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def fastpath_enabled(model_id: object) -> bool:
    """Whether ``model_id`` is in the fast-path allowlist."""
    model = _token(model_id)
    if not model:
        return False
    return model in fastpath_models()


def parse_var_overrides(
    raw: str | None,
) -> dict[tuple[str, str, str], str]:
    """Parse ``CARTOSKY_FASTPATH_VAR_OVERRIDES`` into ``(model, var, domain) -> source``.

    ``domain`` may be :data:`DOMAIN_WILDCARD`. Malformed entries are dropped
    with a warning (see the module docstring on why this never raises).
    """
    overrides: dict[tuple[str, str, str], str] = {}
    text = str(raw or "").strip()
    if not text:
        return overrides
    for entry in text.split(","):
        item = entry.strip()
        if not item:
            continue
        key_part, sep, value_part = item.partition("=")
        if not sep:
            logger.warning("fastpath: ignoring override %r (expected key=source)", item)
            continue
        source = _token(value_part)
        if source not in VALID_SOURCES:
            logger.warning(
                "fastpath: ignoring override %r (source must be one of %s)",
                item,
                sorted(VALID_SOURCES),
            )
            continue
        parts = [_token(part) for part in key_part.split(":")]
        if len(parts) != 3 or not all(parts[:2]) or not parts[2]:
            logger.warning(
                "fastpath: ignoring override %r (expected model:var:domain=source)", item
            )
            continue
        overrides[(parts[0], parts[1], parts[2])] = source
    return overrides


def _overrides() -> dict[tuple[str, str, str], str]:
    return parse_var_overrides(os.environ.get(ENV_FASTPATH_VAR_OVERRIDES))


def _catalog(model_id: str):
    """Lazily resolve the Open-Meteo catalog for an ENABLED model.

    Callers must have checked :func:`fastpath_enabled` first — this import is
    what keeps ``sources.openmeteo`` (and therefore ``omfiles``/``scipy``) off
    the hot path of every non-fastpath scheduler process.
    """
    from ..sources.openmeteo.catalog import catalog_for  # noqa: PLC0415

    return catalog_for(model_id)


def _declared_domains(model_id: str, var_id: str) -> tuple[str, ...]:
    """Domains this environment builds ``var_id`` for — the delayed path's gate.

    Thin wrapper so a registry/capability hiccup degrades to "no fast
    ownership" rather than raising inside the scheduler's target selection.
    """
    from ..domains import declared_domains_for_var  # noqa: PLC0415

    try:
        return tuple(_token(domain) for domain in declared_domains_for_var(model_id, var_id))
    except Exception:
        logger.exception(
            "fastpath: could not resolve declared domains for %s/%s — treating as delayed",
            model_id,
            var_id,
        )
        return ()


def _catalog_fast_domains(model_id: str) -> tuple[str, ...]:
    """The catalog's raw ``fast_domains`` declaration, before enablement."""
    try:
        catalog = _catalog(model_id)
    except KeyError:
        logger.warning(
            "fastpath: %s is allowlisted but has no Open-Meteo catalog entry — "
            "treating every variable as delayed",
            model_id,
        )
        return ()
    return tuple(_token(domain) for domain in catalog.fast_domains if _token(domain))


def fast_domains_for_model(model_id: object) -> tuple[str, ...]:
    """Artifact domains the fast path may own for ``model_id`` (empty if off).

    The catalog declaration intersected with the domains this environment
    enables for at least one launch-set variable. Used to expand a ``*``
    override; per-variable resolution goes through :func:`launch_set_pairs`.
    """
    model = _token(model_id)
    if not fastpath_enabled(model):
        return ()
    declared: set[str] = set()
    for var_id in _launch_variables(model):
        declared.update(_declared_domains(model, var_id))
    return tuple(
        domain for domain in _catalog_fast_domains(model) if domain in declared
    )


def _launch_variables(model_id: str) -> tuple[str, ...]:
    """CartoSky variables the catalog says the fast path can produce."""
    try:
        catalog = _catalog(model_id)
    except KeyError:
        return ()
    variables: list[str] = []
    for variable in catalog.publishable_variables():
        var_id = _token(variable.cartosky_var)
        if var_id:
            variables.append(var_id)
    for var_id in catalog.derived_variables:
        token = _token(var_id)
        if token:
            variables.append(token)
    return tuple(dict.fromkeys(variables))


def _fast_can_produce(model_id: str, var_id: str) -> bool:
    """Whether the fast source can actually BUILD ``var_id`` for this model.

    A ``=fast`` override that names a variable the sub-loop's plan cannot
    produce is worse than a no-op: ownership would carve the variable out of
    the delayed work list while nothing writes it, so nobody builds it until
    the failover deadline fires an hour or two later. ``ecmwf:tmp850:na=fast``
    is the concrete case — the 9 km surface set carries no pressure-level
    fields at all.

    So a fast-direction override is validated against the catalog's producible
    set (enabled publishable variables + declared derivables) and **fails
    closed to delayed** with a loud error when it does not match. The
    delayed direction stays unconditional: turning something off must never
    depend on the fast path agreeing that it could have built it.
    """
    if var_id in _launch_variables(model_id):
        return True
    logger.error(
        "fastpath: ignoring override %s:%s=fast — the Open-Meteo catalog for %s "
        "cannot produce %r (producible: %s). Falling back to delayed ownership so "
        "the variable still gets built.",
        model_id,
        var_id,
        model_id,
        var_id,
        sorted(_launch_variables(model_id)),
    )
    return False


def launch_set_pairs(model_id: object) -> frozenset[tuple[str, str]]:
    """``(var, domain)`` pairs the catalog declares as the fast launch set.

    Pure config: this is the table *before* the env overrides in
    :func:`source_for` are applied, but *after* the domain-enablement
    intersection — an environment that has not enabled ``global`` never sees a
    ``(var, global)`` pair here at all. Empty when the model is not
    allowlisted.
    """
    model = _token(model_id)
    if not fastpath_enabled(model):
        return frozenset()

    catalog_domains = set(_catalog_fast_domains(model))
    if not catalog_domains:
        return frozenset()

    pairs: set[tuple[str, str]] = set()
    for var_id in _launch_variables(model):
        for domain in _declared_domains(model, var_id):
            if domain in catalog_domains:
                pairs.add((var_id, domain))
    return frozenset(pairs)


def source_for(model_id: object, var_id: object, domain: object) -> str:
    """Resolve the owning source for one ``(model, variable, domain)``.

    Returns :data:`SOURCE_FAST` or :data:`SOURCE_DELAYED`. Everything is
    ``delayed`` unless the model is allowlisted AND the pair is in the launch
    set (or forced by an override).
    """
    model = _token(model_id)
    var = _token(var_id)
    dom = _token(domain)
    if not model or not var or not dom:
        return SOURCE_DELAYED
    if not fastpath_enabled(model):
        return SOURCE_DELAYED

    overrides = _overrides()
    forced = overrides.get((model, var, dom))
    if forced is None:
        forced = overrides.get((model, var, DOMAIN_WILDCARD))
    if forced == SOURCE_DELAYED:
        return SOURCE_DELAYED

    # Fast ownership — whether from the launch set or forced by an override —
    # requires this environment to actually build the domain. An override is a
    # kill switch and an emergency lever, not a way to route around the
    # domain-enablement gate: revoking a pair no delayed builder covers would
    # leave it with nobody to hand back to (see the module docstring).
    if dom not in _declared_domains(model, var):
        return SOURCE_DELAYED
    if forced == SOURCE_FAST:
        return SOURCE_FAST if _fast_can_produce(model, var) else SOURCE_DELAYED

    return SOURCE_FAST if (var, dom) in launch_set_pairs(model) else SOURCE_DELAYED


def is_fast_owned(model_id: object, var_id: object, domain: object) -> bool:
    """Whether the fast source owns this pair (config only — see :mod:`.state`
    for the run-scoped failover overlay that can revoke it)."""
    return source_for(model_id, var_id, domain) == SOURCE_FAST


def fast_owned_pairs(model_id: object) -> frozenset[tuple[str, str]]:
    """Every ``(var, domain)`` pair the fast source owns after overrides."""
    model = _token(model_id)
    if not fastpath_enabled(model):
        return frozenset()

    pairs = set(launch_set_pairs(model))
    domains = fast_domains_for_model(model)
    for (o_model, o_var, o_domain), source in _overrides().items():
        if o_model != model:
            continue
        targets = domains if o_domain == DOMAIN_WILDCARD else (o_domain,)
        for target in targets:
            if source == SOURCE_FAST:
                # Never let an override mint a pair for a domain this
                # environment does not build, or for a variable the fast
                # source cannot produce (see :func:`source_for`).
                if target in _declared_domains(model, o_var) and _fast_can_produce(
                    model, o_var
                ):
                    pairs.add((o_var, target))
            else:
                pairs.discard((o_var, target))
    # A wildcard-fast entry must still lose to a more specific delayed entry.
    for (o_model, o_var, o_domain), source in _overrides().items():
        if o_model != model or o_domain == DOMAIN_WILDCARD:
            continue
        if source == SOURCE_DELAYED:
            pairs.discard((o_var, o_domain))
    return frozenset(pairs)


def ownership_table(model_id: object) -> Mapping[tuple[str, str], str]:
    """Debug/ops view: the resolved table for every launch-set + override pair."""
    model = _token(model_id)
    keys = set(launch_set_pairs(model)) | set(fast_owned_pairs(model))
    return {key: source_for(model, key[0], key[1]) for key in sorted(keys)}
