"""Ensemble stats publish pass (member pipeline Phase 6 — Tier 2).

Third in-scheduler pass (stats design D-B): after a run's member pass has
completed AND promoted, compute percentile (``{var}__p{NN}``) and
probability-of-exceedance (``{var}__prob_gt_{thr}``) grids from the
published member binaries and publish them as ordinary full-profile map
variables through the normal writer, into STAGING (the scheduler's hook
owns manifests + promote, exactly like the member pass).

INVARIANT (stats design §5): stats may only consume member frames after
those frames have been PROMOTED and are manifest-visible in the published
tree. Members are promote-gated as complete sets, which is what makes
published a safe input root — never read staging members.

Unit of work = ``(base_var, fh)``: decode the full member roster once, one
sort serves every percentile and the shared valid-count serves every
probability threshold (:mod:`stats_math`; benchmarked 67× vs
``np.nanpercentile``). The COMPLETENESS GATE is hard (plan §3.3 LOCKED):
if ANY roster member's frame is missing for the fh, the unit is skipped
(``skipped_incomplete``) and retried on a later pass — a percentile from a
partial member set is silent corruption. This also makes the pending scan
self-consistent for D8-backfilled runs: units only count as pending where
the full roster exists, so a mean-coverage-capped run converges instead of
looping.

Stats frames publish at the member grid's native resolution — deliberately
NO display-prep inheritance (the exact-keyed display-prep lookup missing on
stats ids is correct): the plan §6 Tier 2 disk estimate (+0.5–1 GB/run) is
sized on raw-resolution frames, and a 3× upscale would multiply that ~9×.

There is deliberately NO persistent completion marker (stats design §5):
pending/complete derives from filesystem presence, the single source of
truth shared with the member pass. Observability lives in the summary log
line (per-status counts, skipped units, wall, RSS peak).
"""

from __future__ import annotations

import logging
import resource
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from rasterio.transform import Affine

from ...models.base import (
    ensemble_member_descriptors,
    ensemble_member_ids,
    ensemble_stats_descriptors,
    ensemble_stats_product_ids,
    format_prob_threshold,
)
from ..colormaps import get_color_map_spec
from ..grid import write_grid_frames_for_run_root
from .members import (
    STATUS_ERROR,
    STATUS_GATE_FAILED,
    STATUS_PREEMPTED,
    STATUS_RESUMED,
    STATUS_WRITTEN,
    _cycle_hour_from_run_id,
    _decode_member_frame,
    _run_date_from_run_id,
    member_frame_is_complete,
    member_var_id,
)
from .pipeline import (
    _resolve_model_var_capability,
    _resolve_model_var_spec,
    _write_json_atomic,
    build_sidecar_json,
    check_pre_encode_value_sanity,
)

logger = logging.getLogger(__name__)

STATUS_SKIPPED_INCOMPLETE = "skipped_incomplete"

PROBABILITY_COLOR_MAP_ID = "ensemble_probability"


@dataclass
class _StatsVarContext:
    base_var: str
    var_spec: Any
    capability: Any
    base_colormap_spec: dict[str, Any]
    member_ids: list[str]
    percentiles: list[int]
    thresholds: list[float]  # exceedance (prob_gt)
    lt_thresholds: list[float]  # non-exceedance (prob_lt, B2)
    products: dict[str, str]  # product_key -> runtime var id


@dataclass
class _StatsPlan:
    model_id: str
    region: str
    run_id: str
    contexts: dict[str, _StatsVarContext]
    fhs_by_var: dict[str, list[int]]

    @property
    def stats_var_ids(self) -> list[str]:
        return [
            var_id
            for ctx in self.contexts.values()
            for var_id in ctx.products.values()
        ]


def build_stats_plan(plugin: Any, model_id: str, run_id: str, region: str) -> _StatsPlan | None:
    stats_descriptors = ensemble_stats_descriptors(plugin)
    if not stats_descriptors:
        return None
    member_descriptors = ensemble_member_descriptors(plugin)

    cycle_hour = _cycle_hour_from_run_id(run_id)
    contexts: dict[str, _StatsVarContext] = {}
    fhs_by_var: dict[str, list[int]] = {}
    for base_var, descriptor in stats_descriptors.items():
        member_descriptor = member_descriptors.get(base_var)
        if not member_descriptor:
            # Stats without members is a registration bug, not a runtime
            # condition — fail the plan loudly rather than publish nothing.
            raise ValueError(
                f"ensemble.stats on {model_id}/{base_var} has no enabled "
                "ensemble.members descriptor to compute from"
            )
        var_spec = _resolve_model_var_spec(model_id, base_var, plugin)
        capability = _resolve_model_var_capability(model_id, base_var, plugin)
        color_map_id = str(getattr(capability, "color_map_id", "") or "").strip()
        contexts[base_var] = _StatsVarContext(
            base_var=base_var,
            var_spec=var_spec,
            capability=capability,
            base_colormap_spec=get_color_map_spec(color_map_id),
            member_ids=ensemble_member_ids(member_descriptor),
            percentiles=sorted(int(q) for q in (descriptor.get("percentiles") or [])),
            thresholds=sorted(float(t) for t in (descriptor.get("prob_thresholds") or [])),
            lt_thresholds=sorted(
                float(t) for t in (descriptor.get("prob_lt_thresholds") or [])
            ),
            products=ensemble_stats_product_ids(base_var, descriptor),
        )
        fhs = sorted({int(fh) for fh in plugin.scheduled_fhs_for_var(base_var, cycle_hour)})
        if bool(getattr(var_spec, "derived", False)):
            # Mirror the member plan: derived cumulative vars have no fh-0
            # product, so members never exist there.
            fhs = [fh for fh in fhs if fh > 0]
        fhs_by_var[base_var] = fhs

    return _StatsPlan(
        model_id=model_id, region=region, run_id=run_id,
        contexts=contexts, fhs_by_var=fhs_by_var,
    )


def _roster_complete(
    published_run_root: Path, model_id: str, ctx: _StatsVarContext, fh: int,
) -> bool:
    return all(
        member_frame_is_complete(
            published_run_root, model_id, member_var_id(ctx.base_var, member), fh,
        )
        for member in ctx.member_ids
    )


def _iter_expected_stats_frames(
    plugin: Any, model_id: str, run_id: str, *, data_root: Path,
):
    """Expected (stats_var_id, fh) pairs — capped to fhs where the FULL
    member roster is published (the completeness gate's own criterion), so a
    run whose members cover only part of the schedule converges instead of
    reading as forever-pending."""
    plan = build_stats_plan(plugin, model_id, run_id, region="")
    if plan is None:
        return
    published_run_root = data_root / "published" / model_id / run_id
    for base_var, ctx in plan.contexts.items():
        for fh in plan.fhs_by_var[base_var]:
            if not _roster_complete(published_run_root, model_id, ctx, fh):
                continue
            for var_id in ctx.products.values():
                yield var_id, fh


def stats_pass_pending(*, plugin: Any, model_id: str, run_id: str, data_root: Path) -> bool:
    staging_run_root = data_root / "staging" / model_id / run_id
    for var_id, fh in _iter_expected_stats_frames(plugin, model_id, run_id, data_root=data_root):
        if not member_frame_is_complete(staging_run_root, model_id, var_id, fh):
            return True
    return False


def stats_promote_pending(*, plugin: Any, model_id: str, run_id: str, data_root: Path) -> bool:
    staging_run_root = data_root / "staging" / model_id / run_id
    published_run_root = data_root / "published" / model_id / run_id
    for var_id, fh in _iter_expected_stats_frames(plugin, model_id, run_id, data_root=data_root):
        if member_frame_is_complete(
            staging_run_root, model_id, var_id, fh,
        ) and not member_frame_is_complete(published_run_root, model_id, var_id, fh):
            return True
    return False


@dataclass
class StatsPassSummary:
    run_id: str
    model_id: str
    counts: dict[str, int] = field(default_factory=dict)
    stats_var_ids: list[str] = field(default_factory=list)
    unit_times_s: list[float] = field(default_factory=list)
    wall_s: float = 0.0
    rss_peak_mb: float = 0.0
    preempted: bool = False

    @property
    def complete(self) -> bool:
        pending = (
            self.counts.get(STATUS_GATE_FAILED, 0)
            + self.counts.get(STATUS_ERROR, 0)
            + self.counts.get(STATUS_PREEMPTED, 0)
        )
        # skipped_incomplete is NOT failure: those units become pending once
        # their member roster arrives, and the pending scan already excludes
        # them until then.
        return pending == 0


def _rss_peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is KiB on Linux, bytes on macOS.
    return peak / 1024.0 if sys.platform != "darwin" else peak / (1024.0 * 1024.0)


def _process_stats_unit(
    *,
    plan: _StatsPlan,
    ctx: _StatsVarContext,
    fh: int,
    data_root: Path,
    staging_run_root: Path,
    record: Callable[[str], None],
) -> None:
    from .stats_math import prob_exceedance, prob_non_exceedance, sorted_nanpercentile

    product_ids = list(ctx.products.items())
    missing = [
        (key, var_id) for key, var_id in product_ids
        if not member_frame_is_complete(staging_run_root, plan.model_id, var_id, fh)
    ]
    for _ in range(len(product_ids) - len(missing)):
        record(STATUS_RESUMED)
    if not missing:
        return

    published_run_root = data_root / "published" / plan.model_id / plan.run_id
    if not _roster_complete(published_run_root, plan.model_id, ctx, fh):
        # Hard completeness gate (plan §3.3): a partial member set must never
        # produce a stat. Retried once the roster is published.
        for _ in missing:
            record(STATUS_SKIPPED_INCOMPLETE)
        return

    try:
        frames = [
            _decode_member_frame(
                published_run_root, plan.model_id,
                member_var_id(ctx.base_var, member), fh,
            )
            for member in ctx.member_ids
        ]
        stack = np.stack([values for values, _meta in frames]).astype(np.float32, copy=False)
        meta = frames[0][1]
        transform = Affine(*[float(v) for v in meta["transform"]])
        projection = str(meta.get("projection") or "").strip()

        percentile_values = sorted_nanpercentile(stack, ctx.percentiles)
        prob_values = prob_exceedance(stack, ctx.thresholds)
        prob_lt_values = prob_non_exceedance(stack, ctx.lt_thresholds)
        by_key: dict[str, np.ndarray] = {}
        for i, q in enumerate(ctx.percentiles):
            by_key[f"p{q:02d}"] = percentile_values[i]
        for i, threshold in enumerate(ctx.thresholds):
            key = next(
                k for k in ctx.products
                if k.startswith("prob_gt_") and abs(
                    float(k[len("prob_gt_"):].replace("p", ".")) - threshold
                ) < 1e-9
            )
            by_key[key] = prob_values[i]
        for i, threshold in enumerate(ctx.lt_thresholds):
            # Same token grammar as ensemble_stats_product_ids, so the key
            # always exists in ctx.products.
            by_key[f"prob_lt_{format_prob_threshold(threshold)}"] = prob_lt_values[i]

        prob_colormap = get_color_map_spec(PROBABILITY_COLOR_MAP_ID)
        for key, var_id in missing:
            values = by_key[key]
            is_prob = key.startswith("prob_")
            ok = check_pre_encode_value_sanity(
                values,
                prob_colormap if is_prob else ctx.base_colormap_spec,
                var_spec_model=None if is_prob else ctx.var_spec,
                var_capability=None if is_prob else ctx.capability,
                label=f"{plan.model_id}/{var_id}/fh{fh:03d} (stats pass)",
            )
            if not ok:
                logger.error(
                    "PRE-ENCODE SANITY GATE FAILED — stat frame NOT written: %s/%s/fh%03d",
                    plan.model_id, var_id, fh,
                )
                record(STATUS_GATE_FAILED)
                continue
            write_grid_frames_for_run_root(
                run_root=staging_run_root,
                model=plan.model_id,
                var=var_id,
                fh=fh,
                values=values,
                transform=transform,
                projection=projection,
            )
            # Frame sidecar (legend/units/valid_time): what makes stat vars
            # FIRST-CLASS run-manifest variables — the viewer's scrubber,
            # legend, and tooltips, and the meteogram's manifest_frame_entries
            # all consume the run manifest, whose frames require this file.
            colormap_spec = prob_colormap if is_prob else ctx.base_colormap_spec
            finite = values[np.isfinite(values)]
            sidecar = build_sidecar_json(
                model=plan.model_id,
                run_id=plan.run_id,
                var_id=var_id,
                fh=fh,
                run_date=_run_date_from_run_id(plan.run_id),
                colorize_meta={
                    "kind": str(colormap_spec.get("type") or "continuous"),
                    "units": str(colormap_spec.get("units") or ""),
                    "min": float(finite.min()) if finite.size else None,
                    "max": float(finite.max()) if finite.size else None,
                },
                var_spec=colormap_spec,
            )
            _write_json_atomic(
                staging_run_root / var_id / f"fh{fh:03d}.json", sidecar,
            )
            record(STATUS_WRITTEN)
    except Exception:
        logger.exception(
            "Stats unit failed: %s/%s/fh%03d", plan.model_id, ctx.base_var, fh,
        )
        for key, _var_id in missing:
            record(STATUS_ERROR)


def run_stats_pass(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
    region: str,
    should_stop: Callable[[], bool] | None = None,
) -> StatsPassSummary:
    """Run the stats publish pass for one run; idempotent and preemptible.

    Sequential units (the wall is publish-bound, not compute-bound — stats
    design §5); writes to STAGING only, the scheduler hook owns manifests
    and promotion.
    """
    stop = should_stop or (lambda: False)
    summary = StatsPassSummary(run_id=run_id, model_id=model_id)

    try:
        plan = build_stats_plan(plugin, model_id, run_id, region)
    except Exception:
        logger.exception("Stats pass: failed building plan model=%s run=%s", model_id, run_id)
        summary.counts[STATUS_ERROR] = summary.counts.get(STATUS_ERROR, 0) + 1
        return summary
    if plan is None:
        return summary
    summary.stats_var_ids = plan.stats_var_ids

    staging_run_root = data_root / "staging" / model_id / run_id
    started = time.perf_counter()

    def _record(status: str) -> None:
        summary.counts[status] = summary.counts.get(status, 0) + 1

    for base_var, ctx in plan.contexts.items():
        for fh in plan.fhs_by_var[base_var]:
            if stop():
                summary.preempted = True
                # Remaining frames simply stay pending; no per-frame
                # preempted bookkeeping is needed beyond the flag.
                summary.counts[STATUS_PREEMPTED] = summary.counts.get(STATUS_PREEMPTED, 0) + 1
                break
            unit_started = time.perf_counter()
            before = dict(summary.counts)
            _process_stats_unit(
                plan=plan, ctx=ctx, fh=fh, data_root=data_root,
                staging_run_root=staging_run_root, record=_record,
            )
            wrote = summary.counts.get(STATUS_WRITTEN, 0) - before.get(STATUS_WRITTEN, 0)
            if wrote > 0:
                summary.unit_times_s.append(time.perf_counter() - unit_started)
        if summary.preempted:
            break

    summary.wall_s = time.perf_counter() - started
    summary.rss_peak_mb = _rss_peak_mb()
    logger.info(
        "Stats pass summary: run=%s model=%s %s wall=%.1fs unit_mean=%.2fs rss_peak_mb=%.0f complete=%s preempted=%s",
        run_id,
        model_id,
        " ".join(f"{k}={v}" for k, v in sorted(summary.counts.items())) or "no_work",
        summary.wall_s,
        statistics.mean(summary.unit_times_s) if summary.unit_times_s else 0.0,
        summary.rss_peak_mb,
        summary.complete,
        summary.preempted,
    )
    return summary
