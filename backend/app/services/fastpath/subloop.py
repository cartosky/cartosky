"""The scheduler's fast-source sub-loop (design §4).

This module is reached **only** from inside a flag-guarded branch
(:func:`..fastpath.ownership.fastpath_enabled`), which is what keeps
``sources.openmeteo`` — and therefore ``omfiles`` and ``scipy`` — out of every
non-fastpath scheduler process. Nothing at import time here touches the
network or the bucket.

Where it sits
=============

Inside the existing per-model scheduler process and its `flock`, not a second
systemd unit (design §4, Codex finding 1). :func:`run_fastpath_pass` is one
*pass*: it is called from ``run_scheduler``'s main loop, does a bounded amount
of work, and returns. State that must survive a pass boundary lives on disk —
the WP2 accumulation checkpoint and the WP3 failover state file, both in the
``staging/{model}/_fastpath`` namespace.

What one pass does
==================

1. **Poll.** ``in-progress.json`` is a *hint* used to pick the candidate run;
   the object LIST is truth for what exists (WP1's liveness rule).
2. **Per new timestep object, in fh order:** one
   :func:`~..sources.openmeteo.source.fetch_frame_fields` pass with
   ``window='global'`` for every enabled variable (design Decision 1 — the same
   read feeds the NA 9 km and global 0.25° targets).
3. **Integrity gates** (design §6) before anything is trusted: decoded shape,
   NaN fraction, and a physical-range check for temperatures. A failed
   *instantaneous* field skips that variable's frame; a failed *accumulation*
   field aborts the pass, because skipping an accumulation step would desync
   the run-cumulative series.
4. **Accumulation** goes through the WP2 ledger. The ledger retains only its
   frontier, so the loop obeys the contract in its module docstring: ingest a
   step, and if that step closes a published window, *consume it immediately*
   (write the frames) and checkpoint before ingesting anything past it.
5. **Write** through the real production writers, exactly the sequence the
   Phase 5 injection script validated end-to-end:
   ``write_grid_frame_for_run_root`` → ``build_sidecar_json``, then the
   shared ``scheduler._publish_run_snapshot_for`` for grid manifests,
   ``_promote_run``, ``_write_run_manifest`` and the LATEST pointer.
   Promotion happens once per *timestep* (after every variable and domain for
   that fh is on disk) rather than once per var×domain: a promote is a
   copytree, and per-timestep is the natural "per-frame" granularity for a
   source that disseminates one file per fh.

Becoming ``latest``
===================

The fast run advances the LATEST pointer through the *existing* promotion
gates — ``_promotion_ready_regions`` over the scheduler's own resolved primary
variables and ``_resolve_promotion_fhs``, evaluated inside the shared publish
function. No fast-specific criterion exists. A run therefore becomes ``latest``
once the fast-owned surface variables satisfy the same gate a delayed run must,
and design §3 rule 4 covers the rest: the manifest is per-variable incremental,
so a run whose Kuchera/upper-air variables are still building reads exactly
like today's catch-up window to the viewer.

Because the fast poller sees a run ~2 h before the delayed probe does, the
shared publish path also refuses to walk LATEST *backwards* onto an older run
(``_run_is_superseded_by_latest``) — otherwise the delayed loop finishing run
*N* would undo the fast path's promotion of run *N+1*.

Cadence
=======

Design Decision 2: match today's 3 h ladder. Hourly ``.om`` steps below FH90
are summed into 3 h accumulation frames by the WP2 windows, and instantaneous
variables are simply *not written* at hourly fhs — but their objects are still
fetched when the ledger needs that step, which is why the fetch loop is driven
by the source ladder while the write loop is driven by the production ladder.

``msl``
=======

The catalog carries ``pressure_msl`` as ``component_only``. It is **not
fetched here.** Its only production consumer is the MSLP contour/pressure-centre
overlay of ``ptype_intensity`` (``ecmwf.py:361``), which is delayed-owned
(design §3 rule 3) and fetches its own component at build time; no fast-owned
variable declares ``contour_component``. Fetching it would be pure bandwidth
with no writer to hand it to. If a fast-owned variable ever gains an MSLP
overlay, add ``pressure_msl`` to :func:`_fetch_var_names` and thread it into
the contour builder — the catalog row and its Pa units audit are already there.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .ownership import fast_owned_pairs, fastpath_enabled
from .state import FastpathRunState, fastpath_namespace_dir

logger = logging.getLogger(__name__)

#: Artifact domain → the ``grids.TARGET_BUILDERS`` key it regrids onto.
#: ECMWF's two declared fast domains (design Decision 1). A new domain needs an
#: entry here and a target builder in ``grids``.
TARGET_BY_DOMAIN: Mapping[str, str] = {
    "na": "na_9km",
    "global": "global_025",
}

#: Dissemination window relative to the cycle hour, from the monitor's measured
#: distribution (design §4 "Poll"). Inside it, poll fast; outside, poll slow.
DISSEMINATION_WINDOW = (timedelta(hours=5, minutes=20), timedelta(hours=6, minutes=30))
FAST_POLL_SECONDS = 60
SLOW_POLL_SECONDS = 600

#: How long the fast source may make ZERO progress on a run before the
#: failover deadline is allowed to revoke its pairs. Dissemination gaps between
#: consecutive ``.om`` objects are seconds to a couple of minutes, and the
#: scheduler polls every 60 s inside the window, so 20 minutes of nothing —
#: while the delayed source demonstrably has the run — is a true stall rather
#: than ordinary lumpiness. Env-tunable for ops.
ENV_FAILOVER_GRACE_SECONDS = "CARTOSKY_FASTPATH_FAILOVER_GRACE_SECONDS"
DEFAULT_FAILOVER_GRACE_SECONDS = 1200.0

#: Integrity gates (design §6). Deliberately loose enough that real weather
#: never trips them and tight enough that a truncated/garbage decode does.
MAX_NAN_FRACTION = 0.05
TEMPERATURE_RANGE_C = (-100.0, 70.0)
#: ``.om`` variables whose values are °C and therefore range-checkable.
_TEMPERATURE_OM_VARS = frozenset({"temperature_2m", "dew_point_2m"})

__all__ = [
    "DISSEMINATION_WINDOW",
    "FAST_POLL_SECONDS",
    "MAX_NAN_FRACTION",
    "SLOW_POLL_SECONDS",
    "TARGET_BY_DOMAIN",
    "TEMPERATURE_RANGE_C",
    "FastpathPassResult",
    "IntegrityError",
    "discover_fast_run",
    "enforce_failover_deadline",
    "in_dissemination_window",
    "poll_interval_seconds",
    "run_fastpath_pass",
]


class IntegrityError(RuntimeError):
    """A decoded field failed a design §6 integrity gate."""


@dataclass
class FastpathPassResult:
    """What one :func:`run_fastpath_pass` did — the shape WP4's canary reads."""

    model_id: str
    run_id: str | None = None
    objects_seen: int = 0
    steps_ingested: int = 0
    frames_written: int = 0
    frames_skipped: int = 0
    timesteps_promoted: int = 0
    stalled: bool = False
    stall_reasons: list[str] = field(default_factory=list)
    active_pairs: tuple[tuple[str, str], ...] = ()
    revoked_pairs: tuple[tuple[str, str], ...] = ()
    #: Domains whose LATEST pointer the last promote was allowed to advance.
    promotion_ready_regions: tuple[str, ...] = ()
    #: Timesteps that staged but failed to promote in this pass (retry queue).
    promotion_retries_pending: int = 0
    #: Previously-failed timesteps this pass successfully promoted.
    promotion_retries_succeeded: int = 0
    #: The fast source has consumed the whole cycle for every active pair.
    completed: bool = False

    def note_stall(self, reason: str) -> None:
        self.stalled = True
        self.stall_reasons.append(str(reason))


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def failover_grace_seconds() -> float:
    """Grace window before a trailing run may be judged stalled."""
    raw = str(os.environ.get(ENV_FAILOVER_GRACE_SECONDS, "")).strip()
    if not raw:
        return DEFAULT_FAILOVER_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "fastpath: invalid %s=%r; using %.0fs",
            ENV_FAILOVER_GRACE_SECONDS,
            raw,
            DEFAULT_FAILOVER_GRACE_SECONDS,
        )
        return DEFAULT_FAILOVER_GRACE_SECONDS
    return max(0.0, value)


def in_dissemination_window(run_dt: datetime, now: datetime | None = None) -> bool:
    """Whether ``now`` is inside the run's expected dissemination window."""
    reference = _as_utc(now or datetime.now(timezone.utc))
    start, end = DISSEMINATION_WINDOW
    base = _as_utc(run_dt)
    return base + start <= reference <= base + end


def poll_interval_seconds(run_dt: datetime, now: datetime | None = None) -> int:
    """Short interval inside the expected window, slow outside (design §4)."""
    return FAST_POLL_SECONDS if in_dissemination_window(run_dt, now) else SLOW_POLL_SECONDS


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------


def _default_source():
    from ..sources.openmeteo import source as om_source  # noqa: PLC0415

    return om_source


def _catalog_for(model_id: str):
    from ..sources.openmeteo.catalog import catalog_for  # noqa: PLC0415

    return catalog_for(model_id)


def discover_fast_run(
    catalog,
    *,
    source: Any | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """The newest run the bucket is currently publishing, or ``None``.

    ``in-progress.json`` is only a hint (WP1 liveness rule): it names a
    candidate, and the object LIST decides whether that run actually exists.
    When the hint is missing or unusable, the most recent catalogued cycle
    boundary at or before ``now`` is tried instead — the fast source must be
    able to discover a run *before* the delayed probe sees it (design §3
    rule 1), so it cannot lean on ``_resolve_latest_run_dt``.
    """
    om_source = source if source is not None else _default_source()
    reference = _as_utc(now or datetime.now(timezone.utc))

    candidates: list[datetime] = []
    try:
        hint = om_source.read_in_progress(catalog)
    except Exception as exc:  # network/parse — hints are never load-bearing
        logger.info("fastpath: in-progress hint unavailable (%s)", exc)
        hint = None
    if hint is not None and hint.reference_time is not None:
        candidates.append(_as_utc(hint.reference_time))

    cycles = sorted(int(hour) for hour in catalog.source_cadence)
    if cycles:
        cursor = reference.replace(minute=0, second=0, microsecond=0)
        for _ in range(48):
            if cursor.hour in cycles:
                aligned = cursor
                if aligned not in candidates:
                    candidates.append(aligned)
                if len(candidates) >= 3:
                    break
            cursor -= timedelta(hours=1)

    for candidate in candidates:
        try:
            objects = om_source.list_run_objects(catalog, candidate)
        except Exception as exc:
            logger.warning("fastpath: LIST failed for %s (%s)", candidate, exc)
            continue
        if objects:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Integrity gates (design §6)
# ---------------------------------------------------------------------------


def check_field_integrity(catalog, om_name: str, values: np.ndarray) -> None:
    """Raise :class:`IntegrityError` if a decoded field cannot be trusted.

    Three cheap checks, in the order a corrupt object usually fails them:
    decoded shape, NaN fraction, and (for temperatures) physical range. Never
    publish a failed decode — a skipped frame is a visible gap, a published
    garbage frame is not.
    """
    array = np.asarray(values)
    expected_points = catalog.source_points
    if expected_points is not None and int(array.size) != int(expected_points):
        raise IntegrityError(
            f"{om_name}: decoded {array.size} points, expected {expected_points}"
        )
    if array.size == 0:
        raise IntegrityError(f"{om_name}: decoded an empty field")

    nan_fraction = float(np.count_nonzero(~np.isfinite(array))) / float(array.size)
    if nan_fraction > MAX_NAN_FRACTION:
        raise IntegrityError(
            f"{om_name}: {nan_fraction:.1%} of values are non-finite "
            f"(limit {MAX_NAN_FRACTION:.0%})"
        )

    if om_name in _TEMPERATURE_OM_VARS:
        finite = array[np.isfinite(array)]
        if finite.size:
            low, high = TEMPERATURE_RANGE_C
            observed_min = float(np.min(finite))
            observed_max = float(np.max(finite))
            if observed_min < low or observed_max > high:
                raise IntegrityError(
                    f"{om_name}: range [{observed_min:.1f}, {observed_max:.1f}] °C is "
                    f"outside the physical band [{low}, {high}]"
                )


# ---------------------------------------------------------------------------
# Variable planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _VarPlan:
    """What the pass must fetch and what it must write, resolved once."""

    #: cartosky var → the ``.om`` variable that feeds it directly.
    instantaneous: Mapping[str, str]
    accumulation: Mapping[str, str]
    #: cartosky var → (u ``.om`` name, v ``.om`` name)
    derived_wind: Mapping[str, tuple[str, str]]
    #: Every ``.om`` name to request per object.
    fetch_names: tuple[str, ...]
    #: cartosky var → the domains it is fast-owned in for this run.
    domains_by_var: Mapping[str, tuple[str, ...]]
    #: What the WP2 ledger tracks. Deliberately the catalog's FULL accumulation
    #: set rather than only the owned subset: the checkpoint key includes the
    #: variable list, so letting ownership changes (a per-pair override, a
    #: partial failover) shrink it would invalidate the checkpoint mid-run and
    #: force a full step replay. Tracking the fixed set costs one extra field
    #: per read and keeps the lineage stable.
    accumulation_om_names: tuple[str, ...] = ()


def _om_by_cartosky(catalog) -> dict[str, Any]:
    return {
        str(variable.cartosky_var): variable
        for variable in catalog.enabled_variables()
        if variable.cartosky_var
    }


def _wind_components(plugin, catalog, var_id: str) -> tuple[str, str] | None:
    """Resolve a derived wind variable's ``.om`` u/v inputs from the plugin.

    The mapping comes from the production VarSpec's ``u_component`` /
    ``v_component`` hints (``ecmwf.py:975-987``) so the fast path and
    ``derive._derive_wspd10m`` cannot drift apart.
    """
    var_spec = plugin.get_var(var_id) if hasattr(plugin, "get_var") else None
    hints = getattr(getattr(var_spec, "selectors", None), "hints", {}) or {}
    if not isinstance(hints, dict):
        return None
    u_key = str(hints.get("u_component") or "").strip()
    v_key = str(hints.get("v_component") or "").strip()
    if not u_key or not v_key:
        return None
    by_var = _om_by_cartosky(catalog)
    u_var = by_var.get(u_key)
    v_var = by_var.get(v_key)
    if u_var is None or v_var is None:
        return None
    return (u_var.om_name, v_var.om_name)


def _plan_variables(plugin, catalog, pairs: Iterable[tuple[str, str]]) -> _VarPlan:
    from ..sources.openmeteo.catalog import VariableKind  # noqa: PLC0415

    domains_by_var: dict[str, list[str]] = {}
    for var_id, domain in sorted(pairs):
        domains_by_var.setdefault(str(var_id), []).append(str(domain))

    by_var = _om_by_cartosky(catalog)
    instantaneous: dict[str, str] = {}
    accumulation: dict[str, str] = {}
    derived_wind: dict[str, tuple[str, str]] = {}
    fetch_names: list[str] = []

    for var_id in sorted(domains_by_var):
        variable = by_var.get(var_id)
        if variable is not None and not variable.component_only:
            if variable.kind is VariableKind.ACCUMULATION:
                accumulation[var_id] = variable.om_name
            else:
                instantaneous[var_id] = variable.om_name
            fetch_names.append(variable.om_name)
            continue
        if var_id in catalog.derived_variables:
            components = _wind_components(plugin, catalog, var_id)
            if components is None:
                logger.warning(
                    "fastpath: %s declares derived variable %r but its u/v components "
                    "are not resolvable — dropping it from this run",
                    catalog.model_id,
                    var_id,
                )
                continue
            derived_wind[var_id] = components
            fetch_names.extend(components)
            continue
        logger.warning(
            "fastpath: %s has no catalog entry able to produce %r — dropping it",
            catalog.model_id,
            var_id,
        )

    for var_id in list(domains_by_var):
        if var_id not in instantaneous and var_id not in accumulation and var_id not in derived_wind:
            domains_by_var.pop(var_id, None)

    ledger_names: tuple[str, ...] = ()
    if accumulation:
        ledger_names = tuple(
            variable.om_name for variable in catalog.accumulation_variables()
        )
        fetch_names.extend(ledger_names)

    return _VarPlan(
        instantaneous=instantaneous,
        accumulation=accumulation,
        derived_wind=derived_wind,
        fetch_names=tuple(dict.fromkeys(fetch_names)),
        domains_by_var={var_id: tuple(doms) for var_id, doms in domains_by_var.items()},
        accumulation_om_names=ledger_names,
    )


def _scheduled_fhs(plugin, var_id: str, cycle_hour: int) -> tuple[int, ...]:
    """The production publish ladder for one variable — the same source of
    truth the delayed scheduler uses (``_scheduled_targets_for_cycle``), so a
    fast-owned variable publishes exactly the fhs its delayed twin would."""
    if hasattr(plugin, "scheduled_fhs_for_var"):
        fhs = plugin.scheduled_fhs_for_var(var_id, cycle_hour)
    else:
        fhs = plugin.target_fhs(cycle_hour)
    return tuple(sorted({int(fh) for fh in fhs}))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _regrid(catalog, domain: str, values_1d: np.ndarray) -> np.ndarray:
    from ..sources.openmeteo import grids  # noqa: PLC0415

    target = TARGET_BY_DOMAIN.get(str(domain))
    if target is None:
        raise KeyError(f"fastpath: no regrid target declared for domain {domain!r}")
    sampler = grids.get_sampler(catalog.grid.value, target)
    return grids.apply_sampler(sampler, np.asarray(values_1d))


def _provenance(
    *,
    catalog,
    om_name: str | tuple[str, ...],
    run_object,
    generation: int,
    cadence_version: str,
) -> dict[str, Any]:
    """Additive per-frame provenance keys (design §8).

    Everything here is new; no existing sidecar key is removed or renamed, so
    consumers that do not know about the fast path are unaffected.
    """
    from ..sources.openmeteo.accumulation import SOURCE_ID  # noqa: PLC0415

    names = (om_name,) if isinstance(om_name, str) else tuple(om_name)
    payload: dict[str, Any] = {
        "source_id": SOURCE_ID,
        "source_resolution": catalog.source_resolution_label,
        "source_attribution": catalog.attribution,
        "source_variables": list(names),
        "cadence_version": cadence_version,
        "generation": int(generation),
    }
    if run_object is not None:
        payload["source_object_key"] = getattr(run_object, "key", None)
        etag = getattr(run_object, "etag", None)
        if etag:
            payload["source_object_etag"] = str(etag)
        last_modified = getattr(run_object, "last_modified", None)
        if isinstance(last_modified, datetime):
            payload["source_object_last_modified"] = _as_utc(last_modified).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
    return {key: value for key, value in payload.items() if value is not None}


def _write_fast_frame(
    *,
    plugin,
    model_id: str,
    domain: str,
    var_id: str,
    fh: int,
    run_dt: datetime,
    run_id: str,
    data_root: Path,
    values: np.ndarray,
    provenance: Mapping[str, Any],
) -> None:
    """One frame through the REAL writers, in the Phase 5 validated order."""
    from ..builder.colorize import float_to_rgba  # noqa: PLC0415
    from ..builder.pipeline import (  # noqa: PLC0415
        HOVER_VALUE_DOWNSAMPLE_FACTOR,
        _resolve_model_var_capability,
        _resolve_model_var_spec,
        _write_json_atomic,
        build_sidecar_json,
    )
    from ..builder.raster_grid import get_target_grid  # noqa: PLC0415
    from ..colormaps import get_color_map_spec  # noqa: PLC0415
    from ..grid import write_grid_frame_for_run_root  # noqa: PLC0415
    from .. import scheduler as scheduler_module  # noqa: PLC0415

    grid = get_target_grid(model_id, domain)
    staging_run_root = scheduler_module._staging_run_root(data_root, model_id, run_id, domain)
    staging_run_root.mkdir(parents=True, exist_ok=True)

    frame_values = np.asarray(values, dtype=np.float32)
    if (frame_values.shape[0], frame_values.shape[1]) != (grid.height, grid.width):
        raise IntegrityError(
            f"{var_id}/{domain} fh{fh:03d}: regridded shape {frame_values.shape} != "
            f"production target {(grid.height, grid.width)}"
        )

    write_grid_frame_for_run_root(
        run_root=staging_run_root,
        model=model_id,
        var=var_id,
        fh=fh,
        values=frame_values,
        transform=grid.transform,
        projection=grid.crs,
    )

    var_spec_model = _resolve_model_var_spec(model_id, var_id, plugin)
    var_capability = _resolve_model_var_capability(model_id, var_id, plugin)
    color_map_id = str(var_capability.color_map_id).strip()
    var_spec_colormap = get_color_map_spec(color_map_id)
    _, colorize_meta = float_to_rgba(frame_values, color_map_id, meta_var_key=var_id)

    sidecar = build_sidecar_json(
        model=model_id,
        region=domain,
        run_id=run_id,
        var_id=var_id,
        fh=fh,
        run_date=run_dt,
        colorize_meta=colorize_meta,
        var_spec=var_spec_colormap,
        var_spec_model=var_spec_model,
        value_downsample_factor=HOVER_VALUE_DOWNSAMPLE_FACTOR,
        quality="full",
        quality_flags=[],
        extra_metadata=dict(provenance),
    )
    sidecar_path = scheduler_module._frame_sidecar_path(
        data_root, model_id, run_id, var_id, fh, region=domain
    )
    _write_json_atomic(sidecar_path, sidecar)


def _publish_timestep(
    *,
    plugin,
    model_id: str,
    run_id: str,
    data_root: Path,
    domains: Sequence[str],
    manifest_targets: Sequence[tuple[str, str, int]],
    primary_vars: Sequence[str],
    promotion_fhs: Sequence[int],
    reason: str,
) -> list[str]:
    """Promote this timestep's domains through the SHARED promotion path.

    Delegates to ``scheduler._publish_run_snapshot_for`` — the same function
    the delayed catch-up loop's ``_publish_run_snapshot`` closure calls — so
    grid manifests, ``_promote_run``, the run manifest and the LATEST pointer
    all come from one implementation rather than a fast-path fork. It takes the
    scheduler's process-wide ``_PUBLISH_LOCK`` per domain internally, which is
    why nothing here holds it: the lock is not reentrant.

    ``strict_canonical=False`` is the fast path's policy (see that function's
    docstring): promote every domain actually written, every timestep, and let
    promotion readiness gate only the LATEST pointer. That is what makes a
    fast-built run become ``latest`` as soon as the existing promotion gates
    pass on the fast-owned surface variables — design §3 rule 4 already relies
    on per-variable manifest readiness to describe a run whose remaining
    variables are still building, exactly as during today's catch-up window.
    """
    from .. import scheduler as scheduler_module  # noqa: PLC0415

    return scheduler_module._publish_run_snapshot_for(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        plugin=plugin,
        primary_vars=list(primary_vars),
        promotion_fhs=list(promotion_fhs),
        targets=list(manifest_targets),
        reason=reason,
        domains=list(domains),
        canonical_region=scheduler_module.canonical_domain(model_id),
        strict_canonical=False,
    )


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def run_fastpath_pass(
    *,
    plugin,
    model_id: str,
    data_root: Path,
    run_dt: datetime | None = None,
    source: Any | None = None,
    now: datetime | None = None,
    max_steps: int | None = None,
    promote: bool = True,
    primary_vars: Sequence[str] | None = None,
) -> FastpathPassResult:
    """Advance the fast source for one run by up to ``max_steps`` objects.

    Returns a :class:`FastpathPassResult`; never raises for an upstream
    problem (a stalled bucket is a normal, recoverable state that the failover
    deadline handles) — only programming errors propagate.
    """
    from ..sources.openmeteo.accumulation import (  # noqa: PLC0415
        AccumulationError,
        AccumulationLedger,
        CheckpointError,
        cadence_version,
    )
    from .. import scheduler as scheduler_module  # noqa: PLC0415

    result = FastpathPassResult(model_id=str(model_id))
    if not fastpath_enabled(model_id):
        return result

    catalog = _catalog_for(model_id)
    om_source = source if source is not None else _default_source()

    if run_dt is None:
        run_dt = discover_fast_run(catalog, source=om_source, now=now)
    if run_dt is None:
        logger.info("fastpath: no run available yet for %s", model_id)
        return result
    run_dt = _as_utc(run_dt)
    run_id = scheduler_module._run_id_from_dt(run_dt)
    result.run_id = run_id
    cycle_hour = run_dt.hour

    state = FastpathRunState.load_or_create(
        data_root=data_root, model=model_id, run_id=run_id
    )
    configured = fast_owned_pairs(model_id)
    active = tuple(sorted(pair for pair in configured if not state.is_revoked(*pair)))
    result.active_pairs = active
    result.revoked_pairs = tuple(sorted(state.revoked_pairs() & configured))
    if not active:
        logger.info("fastpath: no active fast-owned pairs for %s %s", model_id, run_id)
        return result

    plan = _plan_variables(plugin, catalog, active)
    if not plan.fetch_names:
        return result

    unknown_domains = {
        domain for _var, domain in active if domain not in TARGET_BY_DOMAIN
    }
    if unknown_domains:
        logger.error(
            "fastpath: %s declares fast domain(s) %s with no regrid target — skipping them",
            model_id,
            sorted(unknown_domains),
        )

    try:
        objects = om_source.list_run_objects(catalog, run_dt)
    except Exception as exc:
        logger.warning("fastpath: LIST failed for %s %s (%s)", model_id, run_id, exc)
        result.note_stall(f"list_failed: {exc}")
        state.note_stall()
        state.save()
        return result
    result.objects_seen = len(objects)
    if not objects:
        result.note_stall("no_objects")
        return result

    checkpoint_dir = fastpath_namespace_dir(data_root, model_id)
    ledger = None
    if plan.accumulation_om_names:
        try:
            ledger = AccumulationLedger.load_checkpoint(
                checkpoint_dir,
                catalog,
                run_dt,
                var_names=plan.accumulation_om_names,
            )
            logger.info(
                "fastpath: resumed accumulation checkpoint %s through FH%03d",
                run_id,
                ledger.last_ingested_fh,
            )
        except CheckpointError:
            ledger = AccumulationLedger(
                catalog, run_dt, var_names=plan.accumulation_om_names
            )

    # Promotion inputs come from the scheduler's own resolvers so the fast run
    # is gated by exactly the criteria the delayed loop uses. ``run_scheduler``
    # threads its already-resolved primary vars in; the fallback keeps direct
    # callers (tests, one-shot tooling) honest rather than inventing a gate.
    resolved_primary = [str(item) for item in (primary_vars or ()) if str(item).strip()]
    if not resolved_primary:
        fallback = plugin.normalize_var_id(scheduler_module.DEFAULT_PRIMARY_VAR)
        resolved_primary = [fallback]
    promotion_fhs = list(
        scheduler_module._resolve_promotion_fhs(plugin, resolved_primary, cycle_hour)
    )

    version = cadence_version(catalog, cycle_hour)
    published_fhs_by_var = {
        var_id: _scheduled_fhs(plugin, var_id, cycle_hour)
        for var_id in plan.domains_by_var
    }
    manifest_targets_by_domain: dict[str, list[tuple[str, str, int]]] = {}
    for var_id, domains in plan.domains_by_var.items():
        for domain in domains:
            if domain not in TARGET_BY_DOMAIN:
                continue
            bucket = manifest_targets_by_domain.setdefault(domain, [])
            for fh in published_fhs_by_var[var_id]:
                bucket.append((domain, var_id, int(fh)))

    # Drain the promotion retry queue BEFORE ingesting anything new: a
    # timestep that staged but failed to promote must reach the published tree
    # before later frames pile on top of it (and the run's final timestep has
    # no successor to carry it along).
    if promote and not _drain_pending_promotions(
        plugin=plugin,
        model_id=model_id,
        run_id=run_id,
        data_root=data_root,
        state=state,
        manifest_targets_by_domain=manifest_targets_by_domain,
        published_fhs_by_var=published_fhs_by_var,
        primary_vars=resolved_primary,
        promotion_fhs=promotion_fhs,
        result=result,
    ):
        return result

    boundary_fhs = frozenset(ledger.published_fhs) if ledger is not None else frozenset()
    next_step = ledger.next_expected_fh() if ledger is not None else None

    steps_this_pass = 0
    for run_object in objects:
        fh = int(run_object.fh)
        if max_steps is not None and steps_this_pass >= max_steps:
            break

        needs_ingest = (
            ledger is not None
            and fh > 0
            and next_step is not None
            and fh == next_step
        )
        pending_writes = _pending_write_vars(
            state=state,
            plan=plan,
            published_fhs_by_var=published_fhs_by_var,
            boundary_fhs=boundary_fhs,
            fh=fh,
            has_ledger=ledger is not None,
        )
        if not needs_ingest and not pending_writes:
            continue

        # Generation guard: a failover that landed between passes (or in
        # another critical section) must stop this loop for revoked pairs
        # before it writes anything else (design §6).
        disk_state = FastpathRunState.load_or_create(
            data_root=data_root, model=model_id, run_id=run_id
        )
        newly_revoked = {
            pair for pair in active if disk_state.is_revoked(*pair)
        }
        if newly_revoked:
            logger.warning(
                "fastpath: stopping %s %s at FH%03d — ownership revoked for %s",
                model_id,
                run_id,
                fh,
                sorted(newly_revoked),
            )
            result.revoked_pairs = tuple(sorted(disk_state.revoked_pairs() & configured))
            result.note_stall("ownership_revoked")
            break
        state = disk_state

        wanted = _fetch_names_for(plan, needs_ingest=needs_ingest, write_vars=pending_writes)
        try:
            fields = om_source.fetch_frame_fields(catalog, run_object.url, wanted, "global")
        except Exception as exc:
            logger.warning(
                "fastpath: fetch failed for %s FH%03d (%s)", run_id, fh, exc
            )
            result.note_stall(f"fetch_failed_fh{fh:03d}: {exc}")
            state.note_stall()
            break

        clean: dict[str, np.ndarray] = {}
        failed: dict[str, str] = {}
        for name, values in fields.items():
            try:
                check_field_integrity(catalog, name, values)
            except IntegrityError as exc:
                failed[name] = str(exc)
                continue
            clean[name] = values

        if needs_ingest:
            missing = [
                name for name in plan.accumulation_om_names if name not in clean
            ]
            if missing:
                reason = "; ".join(
                    failed.get(name, f"{name}: absent from object") for name in missing
                )
                logger.error(
                    "fastpath: aborting %s at FH%03d — accumulation step unusable (%s)",
                    run_id,
                    fh,
                    reason,
                )
                result.note_stall(f"accumulation_integrity_fh{fh:03d}: {reason}")
                result.frames_skipped += 1
                state.note_stall()
                state.save()
                break
            try:
                ledger.ingest_step(
                    fh, {name: clean[name] for name in plan.accumulation_om_names}
                )
            except AccumulationError as exc:
                logger.error("fastpath: ledger refused FH%03d for %s (%s)", fh, run_id, exc)
                result.note_stall(f"ledger_fh{fh:03d}: {exc}")
                state.note_stall()
                state.save()
                break
            result.steps_ingested += 1
            state.note_progress()
            next_step = ledger.next_expected_fh()

        for name, reason in failed.items():
            logger.warning("fastpath: %s FH%03d integrity gate: %s", run_id, fh, reason)

        written_by_domain: dict[str, list[str]] = {}
        by_cartosky = _om_by_cartosky(catalog)
        for var_id in pending_writes:
            source_fields, om_names = _source_fields_for_var(
                plan=plan,
                var_id=var_id,
                fh=fh,
                clean=clean,
                ledger=ledger,
            )
            if source_fields is None:
                # FH000 carries no accumulation/gust fields (prototype §4
                # gotcha 4) — a legitimately absent frame, not a failure.
                logger.debug(
                    "fastpath: %s FH%03d has no source field for %s", run_id, fh, var_id
                )
                continue
            provenance = _provenance(
                catalog=catalog,
                om_name=om_names,
                run_object=run_object,
                generation=state.pair(var_id, plan.domains_by_var[var_id][0]).generation,
                cadence_version=version,
            )
            for domain in plan.domains_by_var[var_id]:
                if domain not in TARGET_BY_DOMAIN:
                    continue
                if fh in state.pair(var_id, domain).staged_fhs:
                    continue
                try:
                    converted = _target_values(
                        catalog=catalog,
                        plan=plan,
                        var_id=var_id,
                        domain=domain,
                        source_fields=source_fields,
                        om_variable=by_cartosky.get(var_id),
                    )
                    _write_fast_frame(
                        plugin=plugin,
                        model_id=model_id,
                        domain=domain,
                        var_id=var_id,
                        fh=fh,
                        run_dt=run_dt,
                        run_id=run_id,
                        data_root=data_root,
                        values=converted,
                        provenance=provenance,
                    )
                except Exception as exc:
                    logger.exception(
                        "fastpath: frame write failed %s %s/%s fh%03d (%s)",
                        run_id,
                        domain,
                        var_id,
                        fh,
                        exc,
                    )
                    result.frames_skipped += 1
                    continue
                state.record_staged(var_id, domain, fh)
                state.note_progress()
                written_by_domain.setdefault(domain, []).append(var_id)
                result.frames_written += 1

        # Consume-then-advance (WP2 retention contract): the boundary's frames
        # are on disk before anything past this step is ingested, and the
        # checkpoint is taken at the boundary so a restart resumes here.
        if ledger is not None and fh in boundary_fhs and fh == ledger.frontier_published_fh:
            try:
                ledger.save_checkpoint(checkpoint_dir)
            except Exception as exc:
                logger.warning("fastpath: checkpoint write failed at FH%03d (%s)", fh, exc)

        state.save()

        if written_by_domain and promote:
            _promote_staged_timestep(
                plugin=plugin,
                model_id=model_id,
                run_id=run_id,
                data_root=data_root,
                state=state,
                fh=fh,
                domains=sorted(written_by_domain),
                manifest_targets_by_domain=manifest_targets_by_domain,
                published_fhs_by_var=published_fhs_by_var,
                primary_vars=resolved_primary,
                promotion_fhs=promotion_fhs,
                result=result,
            )
        elif written_by_domain:
            # promote=False: frames stay staged on purpose (tests, dry runs).
            for domain, vars_written in written_by_domain.items():
                for var_id in vars_written:
                    state.record_published(
                        var_id, domain, fh, expected_fhs=published_fhs_by_var[var_id]
                    )
            state.save()

        steps_this_pass += 1

    # Completion: every active pair is at its own cycle-aware horizon. Recorded
    # on the run state so the failover deadline can skip a finished run outright
    # instead of judging it against a stall clock that will only ever grow
    # (design §4 "Complete"). Derived from the pair frontiers rather than the
    # object count because that is the same evidence the deadline acts on.
    completed = all(
        (state.pair(var_id, domain, create=False).ready_through_fh or -1)
        >= (published_fhs_by_var.get(var_id) or (0,))[-1]
        for var_id, domain in active
        if domain in TARGET_BY_DOMAIN and published_fhs_by_var.get(var_id)
    )
    if completed and not state.completed:
        from ..sources.openmeteo.catalog import expected_file_count  # noqa: PLC0415

        expected_objects = expected_file_count(catalog, cycle_hour)
        logger.info(
            "fastpath: run complete model=%s run=%s objects=%d/%d — the deadline "
            "will stop evaluating it (completed is not stalled)",
            model_id,
            run_id,
            result.objects_seen,
            expected_objects,
        )
        state.mark_completed()
        state.save()
    result.completed = bool(state.completed)

    if result.stalled:
        logger.warning(
            "fastpath_stalled model=%s run=%s objects=%d steps=%d frames=%d reasons=%s",
            model_id,
            run_id,
            result.objects_seen,
            result.steps_ingested,
            result.frames_written,
            "|".join(result.stall_reasons),
        )
    logger.info(
        "fastpath pass: model=%s run=%s objects=%d steps=%d frames=%d skipped=%d promoted=%d",
        model_id,
        run_id,
        result.objects_seen,
        result.steps_ingested,
        result.frames_written,
        result.frames_skipped,
        result.timesteps_promoted,
    )
    return result


def _promote_staged_timestep(
    *,
    plugin,
    model_id: str,
    run_id: str,
    data_root: Path,
    state: FastpathRunState,
    fh: int,
    domains: Sequence[str],
    manifest_targets_by_domain: Mapping[str, Sequence[tuple[str, str, int]]],
    published_fhs_by_var: Mapping[str, tuple[int, ...]],
    primary_vars: Sequence[str],
    promotion_fhs: Sequence[int],
    result: FastpathPassResult,
) -> bool:
    """Promote one timestep and record progress ONLY on success.

    The ordering here is the whole point: frames are staged, then promoted,
    and ``published_fhs`` (which drives readiness, the manifest frontier and
    the failover deadline) only advances once the promote returns. A promote
    that raises therefore leaves the timestep in ``pending_promotions()``, and
    :func:`_drain_pending_promotions` retries it on the next pass instead of
    the run silently ending up with staged-but-unpublished frames nobody ever
    looks at again.
    """
    targets = [
        target
        for domain in domains
        for target in manifest_targets_by_domain.get(domain, [])
    ]
    try:
        ready_regions = _publish_timestep(
            plugin=plugin,
            model_id=model_id,
            run_id=run_id,
            data_root=data_root,
            domains=list(domains),
            manifest_targets=targets,
            primary_vars=primary_vars,
            promotion_fhs=promotion_fhs,
            reason=f"fastpath_fh{fh:03d}",
        )
    except Exception as exc:
        logger.exception(
            "fastpath: publish failed %s fh%03d (%s) — frames stay staged and the "
            "next pass will retry before ingesting new steps",
            run_id,
            fh,
            exc,
        )
        result.promotion_retries_pending += 1
        state.save()
        return False

    for domain in domains:
        for var_id, expected in published_fhs_by_var.items():
            if fh not in state.pair(var_id, domain, create=False).staged_fhs:
                continue
            state.record_published(var_id, domain, fh, expected_fhs=expected)
    result.promotion_ready_regions = tuple(ready_regions)
    result.timesteps_promoted += 1
    state.note_progress()
    state.save()
    return True


def _drain_pending_promotions(
    *,
    plugin,
    model_id: str,
    run_id: str,
    data_root: Path,
    state: FastpathRunState,
    manifest_targets_by_domain: Mapping[str, Sequence[tuple[str, str, int]]],
    published_fhs_by_var: Mapping[str, tuple[int, ...]],
    primary_vars: Sequence[str],
    promotion_fhs: Sequence[int],
    result: FastpathPassResult,
) -> bool:
    """Retry staged-but-unpromoted timesteps, oldest first.

    Runs before any new step is ingested so the published tree stays in fh
    order, and so a run whose FINAL timestep failed to promote still gets
    retried on a later pass — that timestep has no successor to carry it.
    Returns False if a retry failed again (the caller stops rather than piling
    more staged frames on top of an unpublished one).
    """
    pending = state.pending_promotions()
    if not pending:
        return True
    logger.warning(
        "fastpath: retrying %d unpromoted timestep(s) for %s: %s",
        len(pending),
        run_id,
        sorted(pending),
    )
    for fh in sorted(pending):
        if not _promote_staged_timestep(
            plugin=plugin,
            model_id=model_id,
            run_id=run_id,
            data_root=data_root,
            state=state,
            fh=fh,
            domains=sorted(pending[fh]),
            manifest_targets_by_domain=manifest_targets_by_domain,
            published_fhs_by_var=published_fhs_by_var,
            primary_vars=primary_vars,
            promotion_fhs=promotion_fhs,
            result=result,
        ):
            result.note_stall(f"promotion_retry_failed_fh{fh:03d}")
            return False
        result.promotion_retries_succeeded += 1
    return True


def _pending_write_vars(
    *,
    state: FastpathRunState,
    plan: _VarPlan,
    published_fhs_by_var: Mapping[str, tuple[int, ...]],
    boundary_fhs: frozenset[int],
    fh: int,
    has_ledger: bool,
) -> tuple[str, ...]:
    """Variables that should publish a frame at ``fh`` and have not yet.

    Instantaneous variables publish on the production ladder (design Decision
    2 — hourly source steps below FH90 are fetched for the ledger but not
    written); accumulation variables additionally require ``fh`` to be a WP2
    window boundary.
    """
    pending: list[str] = []
    for var_id, domains in plan.domains_by_var.items():
        if fh not in published_fhs_by_var.get(var_id, ()):
            continue
        if var_id in plan.accumulation and not (has_ledger and fh in boundary_fhs):
            continue
        # Staged, not published: a frame already written to staging must not be
        # rewritten just because its promote is still pending.
        if any(fh not in state.pair(var_id, domain).staged_fhs for domain in domains):
            pending.append(var_id)
    return tuple(sorted(pending))


def _fetch_names_for(
    plan: _VarPlan, *, needs_ingest: bool, write_vars: Sequence[str]
) -> tuple[str, ...]:
    names: list[str] = []
    if needs_ingest:
        names.extend(plan.accumulation_om_names)
    for var_id in write_vars:
        if var_id in plan.instantaneous:
            names.append(plan.instantaneous[var_id])
        elif var_id in plan.derived_wind:
            names.extend(plan.derived_wind[var_id])
        elif var_id in plan.accumulation:
            names.append(plan.accumulation[var_id])
    return tuple(dict.fromkeys(names))


def _source_fields_for_var(
    *,
    plan: _VarPlan,
    var_id: str,
    fh: int,
    clean: Mapping[str, np.ndarray],
    ledger,
) -> tuple[tuple[np.ndarray, ...] | None, tuple[str, ...]]:
    """Source-unit, source-grid inputs for one variable at ``fh``.

    One array for a direct variable, two (u, v) for a derived wind. ``None``
    means the inputs are not present — FH000 legitimately carries no
    accumulation or gust fields (prototype §4 gotcha 4), so the caller simply
    does not write that frame.
    """
    if var_id in plan.instantaneous:
        name = plan.instantaneous[var_id]
        values = clean.get(name)
        return ((values,) if values is not None else None), (name,)
    if var_id in plan.derived_wind:
        u_name, v_name = plan.derived_wind[var_id]
        u_values = clean.get(u_name)
        v_values = clean.get(v_name)
        if u_values is None or v_values is None:
            return None, (u_name, v_name)
        return (u_values, v_values), (u_name, v_name)
    if var_id in plan.accumulation and ledger is not None:
        name = plan.accumulation[var_id]
        try:
            return (ledger.cumulative_at(fh)[name],), (name,)
        except Exception as exc:
            logger.warning(
                "fastpath: cumulative unavailable for %s FH%03d (%s)", var_id, fh, exc
            )
            return None, (name,)
    return None, ()


def _target_values(
    *,
    catalog,
    plan: _VarPlan,
    var_id: str,
    domain: str,
    source_fields: Sequence[np.ndarray],
    om_variable,
) -> np.ndarray:
    """Regrid onto ``domain``'s production grid and convert to storage units.

    Linear conversions commute with bilinear resampling, so converting after
    the regrid is numerically identical to converting before, and touches far
    fewer cells than the full-globe source field.

    The derived wind reproduces ``derive._derive_wspd10m`` exactly: components
    are warped onto the target grid *first*, then ``hypot``, then the
    ``ms_to_mph`` conversion the production VarSpec's conversion entry applies
    (``ecmwf.py:1159``).
    """
    from ..sources.openmeteo.catalog import OM_UNIT_CONVERTERS, OmConversion  # noqa: PLC0415

    if var_id in plan.derived_wind:
        u_target = _regrid(catalog, domain, source_fields[0])
        v_target = _regrid(catalog, domain, source_fields[1])
        speed_ms = np.hypot(u_target, v_target, dtype=np.float32)
        converter = OM_UNIT_CONVERTERS[OmConversion.MS_TO_MPH]
        return np.asarray(converter(speed_ms), dtype=np.float32)

    regridded = _regrid(catalog, domain, source_fields[0])
    if om_variable is not None:
        return np.asarray(om_variable.convert(regridded), dtype=np.float32)
    return np.asarray(regridded, dtype=np.float32)


# ---------------------------------------------------------------------------
# Failover (design §6)
# ---------------------------------------------------------------------------


def enforce_failover_deadline(
    *,
    plugin,
    model_id: str,
    run_dt: datetime,
    data_root: Path,
    delayed_available: bool,
    publish_lock: threading.Lock | None = None,
    now: datetime | None = None,
    grace_seconds: float | None = None,
) -> frozenset[tuple[str, str]]:
    """Revoke fast ownership for pairs whose fast source has STALLED.

    Three conditions, all required (design §6):

    1. the delayed source verifiably has this run (the caller's re-probe);
    2. the pair's ``ready_through_fh`` trails its expected horizon;
    3. **the fast source has made no progress on the run for the grace
       window.**

    Condition 3 is not optional garnish — without it, condition 2 is trivially
    true for every run the instant it is discovered, and a scheduler that cold
    starts mid-cycle revokes every pair before the fast pass has run once. That
    happened on the first live WP5 run: a restart at 12:47Z resolved back to
    the 06z run with a genuine probe hit and immediately handed all 12 pairs to
    a full Herbie build, while the bucket held every 06z object and the fast
    path could have built the run in seconds. *Trailing is not stalled.*

    The clock lives on the run state (``created_at`` / ``last_progress_at``) so
    it survives restarts, and this function persists a freshly created state
    even when it revokes nothing — otherwise every poll would mint a new
    ``created_at`` and no run could ever age into a stall.

    Accumulation pairs additionally get ``delayed_rebuild_required`` and have
    their fast frames removed from staging, so the delayed path rebuilds the
    whole series from its own GRIB values. Two sources are never mixed inside
    one accumulation series; an instantaneous variable may legitimately carry a
    9 km→0.25° seam, which is exactly what the per-frame provenance records
    and the §8 resolution label explains.

    Returns the pairs revoked by this call (empty when nothing changed).
    """
    if not fastpath_enabled(model_id) or not delayed_available:
        return frozenset()

    from .. import scheduler as scheduler_module  # noqa: PLC0415

    run_id = scheduler_module._run_id_from_dt(_as_utc(run_dt))
    cycle_hour = _as_utc(run_dt).hour
    configured = fast_owned_pairs(model_id)
    if not configured:
        return frozenset()

    catalog = _catalog_for(model_id)
    accumulation_vars = {
        str(variable.cartosky_var)
        for variable in catalog.accumulation_variables()
        if variable.cartosky_var
    }

    grace = failover_grace_seconds() if grace_seconds is None else float(grace_seconds)
    lock = publish_lock if publish_lock is not None else scheduler_module._PUBLISH_LOCK
    revoked: set[tuple[str, str]] = set()
    with lock:
        state = FastpathRunState.load_or_create(
            data_root=data_root, model=model_id, run_id=run_id
        )
        idle_seconds = state.seconds_since_progress(now)

        # A finished run has nothing left to make progress ON. Evaluating the
        # stall clock against it warns every poll forever after the cycle ends
        # (WP5 saw "no fast progress for 2235s" repeating once 06z completed)
        # and would inflate stall_count into a false alarm at every cycle end.
        # COMPLETED IS NOT STALLED.
        if state.completed:
            logger.debug(
                "fastpath: deadline skipped for %s %s — the fast source completed "
                "this run (idle %.0fs is expected)",
                model_id,
                run_id,
                idle_seconds,
            )
            return frozenset()

        # Which pairs could this deadline actually act on? Anything already
        # revoked, blocked, or finished is not a candidate. Computing this
        # BEFORE looking at the clock is what keeps the warning honest: the old
        # order announced "deadline firing" and then revoked nothing, which is
        # a confusing ops signal and the noisiest possible way to say "fine".
        revocable: list[tuple[str, str, bool]] = []
        for var_id, domain in sorted(configured):
            entry = state.pair(var_id, domain, create=False)
            if entry.revoked or entry.blocked:
                continue
            # Per-variable horizon from the model's own cycle-aware ladder, so
            # a short 06z/18z cycle is judged against FH144, never FH360.
            expected = _scheduled_fhs(plugin, var_id, cycle_hour)
            if not expected:
                continue
            if entry.ready_through_fh is not None and entry.ready_through_fh >= expected[-1]:
                continue  # complete — the fast source finished this pair
            revocable.append((var_id, domain, var_id in accumulation_vars))

        if not revocable:
            # Nothing trails: the run is done (or already handed over). Persist
            # the clock anchor if this is the first sighting, say nothing above
            # DEBUG, and leave stall_count alone.
            if not state.path.exists():
                state.save()
            logger.debug(
                "fastpath: deadline quiet for %s %s — no trailing pairs to revoke "
                "(idle %.0fs)",
                model_id,
                run_id,
                idle_seconds,
            )
            return frozenset()

        if not state.is_stalled(grace_seconds=grace, now=now):
            # Start (or keep) the clock. A run observed for the first time here
            # MUST have its created_at persisted, or the grace window restarts
            # every poll and the deadline can never fire.
            if not state.path.exists():
                state.save()
            logger.info(
                "fastpath: deadline held for %s %s — fast source active %.0fs ago "
                "(grace %.0fs); trailing is not stalled",
                model_id,
                run_id,
                idle_seconds,
                grace,
            )
            return frozenset()

        logger.warning(
            "fastpath: failover deadline firing for %s %s — no fast progress for "
            "%.0fs (grace %.0fs), the delayed source has the run, and %d pair(s) trail",
            model_id,
            run_id,
            idle_seconds,
            grace,
            len(revocable),
        )
        for var_id, domain, rebuild_required in revocable:
            entry = state.revoke(
                var_id,
                domain,
                reason="delayed_source_available",
                rebuild_required=rebuild_required,
            )
            revoked.add((var_id, domain))
            if rebuild_required:
                # Ownership only transfers once the pair is verifiably clean.
                # A failed purge leaves it BLOCKED: fast has stopped by
                # generation, and the delayed path must not build into a
                # series that still holds fast frames (design §6).
                if not _purge_fast_frames(
                    data_root=data_root,
                    model_id=model_id,
                    run_id=run_id,
                    var_id=var_id,
                    domain=domain,
                ):
                    entry.blocked = True
                    entry.revoke_reason = "purge_failed"
                    logger.error(
                        "fastpath_blocked model=%s run=%s pair=%s|%s — accumulation "
                        "frames could not be cleared; NO source will build this pair "
                        "until an operator clears %s",
                        model_id,
                        run_id,
                        var_id,
                        domain,
                        scheduler_module._staging_run_root(
                            data_root, model_id, run_id, domain
                        ),
                    )
        if revoked:
            state.note_stall()
            state.save()
            logger.warning(
                "fastpath_stalled model=%s run=%s failover revoked=%s",
                model_id,
                run_id,
                sorted(revoked),
            )
    return frozenset(revoked)


def _purge_fast_frames(
    *, data_root: Path, model_id: str, run_id: str, var_id: str, domain: str
) -> bool:
    """Quarantine a revoked accumulation pair's staged fast frames.

    The delayed catch-up loop advances past any fh whose staging artifacts
    already exist, so leaving fast frames in place would produce exactly the
    mixed-source accumulation series design §6 forbids. Clearing them makes the
    delayed path rebuild the pair from its own GRIB values, start to finish.

    This has to be **all or nothing**. A best-effort ``rmtree(ignore_errors=
    True)`` can leave some frames behind, and the delayed rebuild would then
    skip exactly those fhs — silently producing the mixed series, with no
    signal that it happened. So:

    1. move the variable directory aside with a single ``os.rename`` into the
       ``_fastpath`` quarantine (atomic, same filesystem by construction —
       both live under ``staging/{model}``);
    2. if the rename is impossible, fall back to a ``rmtree`` that **raises**;
    3. verify afterwards that no complete frame artifact survives.

    Returns ``True`` only when the pair is verifiably clean. A ``False`` means
    the caller must keep the pair revoked *and blocked*: the fast path has
    stopped, and the delayed path must not build into a dirty series either.
    Quarantined frames are left on disk deliberately — they age out with the
    namespace sweep and are evidence for whoever investigates.
    """
    import shutil  # noqa: PLC0415

    from .. import scheduler as scheduler_module  # noqa: PLC0415

    staging_run_root = scheduler_module._staging_run_root(
        data_root, model_id, run_id, domain
    )
    runtime_var_id = scheduler_module._runtime_var_id(
        scheduler_module.MODEL_REGISTRY.get(model_id), var_id
    )
    var_dir = staging_run_root / runtime_var_id
    if not var_dir.exists():
        return True

    quarantine_root = fastpath_namespace_dir(data_root, model_id) / "quarantine"
    quarantine_dir = quarantine_root / f"{run_id}.{domain}.{runtime_var_id}.{uuid.uuid4().hex[:8]}"
    try:
        quarantine_root.mkdir(parents=True, exist_ok=True)
        os.rename(var_dir, quarantine_dir)
    except OSError as exc:
        logger.warning(
            "fastpath: could not quarantine %s (%s); falling back to a strict delete",
            var_dir,
            exc,
        )
        try:
            shutil.rmtree(var_dir)
        except OSError:
            logger.exception(
                "fastpath: PURGE FAILED for %s %s/%s — the delayed path must NOT "
                "rebuild this pair, the series would mix sources",
                run_id,
                domain,
                var_id,
            )
            return False

    if _fast_frames_remain(var_dir):
        logger.error(
            "fastpath: PURGE INCOMPLETE for %s %s/%s — frame artifacts survive at %s; "
            "leaving the pair blocked so no source builds into a dirty series",
            run_id,
            domain,
            var_id,
            var_dir,
        )
        return False

    logger.warning(
        "fastpath: quarantined staged fast frames for %s %s/%s -> %s (delayed rebuild)",
        run_id,
        domain,
        var_id,
        quarantine_dir.name,
    )
    return True


def _fast_frames_remain(var_dir: Path) -> bool:
    """Whether any complete frame artifact survives under ``var_dir``.

    ``_frame_artifacts_exist`` treats a frame as built when both its sidecar
    and its grid meta are present, so either one surviving is enough to make
    the delayed loop skip an fh.
    """
    if not var_dir.exists():
        return False
    try:
        for path in var_dir.rglob("fh*.json"):
            if path.is_file():
                return True
        for path in var_dir.rglob("fh*.meta.json"):
            if path.is_file():
                return True
    except OSError:
        # Cannot prove it is clean -> must assume it is not.
        return True
    return False
