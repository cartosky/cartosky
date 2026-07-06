"""Decoupled ensemble-member publish pass (member pipeline plan Phase 3).

Publishes slim member grid binaries (``{var}__m{NN}`` / ``{var}__control``)
into the STAGING run root, strictly after the run's mean catchup completes.
The scheduler owns scheduling and promotion; this module owns the per-frame
pipeline and the pass loop.

Per-frame pipeline (Phase 2 design R2 — the spike-validated shape):

    fetch (member kwarg) -> convert_units -> warp_to_target_grid ->
    check_pre_encode_value_sanity (ENFORCED; a failure means the frame is
    NOT written and is loudly logged) -> write_slim_grid_frame_for_run_root

Pass semantics (design R4):
  * resumable/idempotent — a frame whose slim ``.bin`` + meta already exist
    and are size-sane is skipped, so the pass re-enters cleanly on every
    scheduler poll until complete;
  * preemptible — ``should_stop`` is consulted between every (member, fh)
    frame and during backoff waits, so a newer run's mean build always wins;
  * fetch failures are recorded and retried on the next pass, never fatal.

GEFS member identity -> Herbie kwarg (spike-confirmed, herbie 2026.3.0):
``member=1..30`` -> gepNN, ``member=0`` -> gec00. EPS is NOT built through
this pass (its members interleave with the mean bundle build — Phase 4) and
has no control member upstream (plan Section 2.2 correction).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ...models.base import ensemble_member_descriptors, ensemble_member_ids
from ..colormaps import get_color_map_spec
from ..grid import (
    _packing_config,
    expected_grid_frame_size_bytes,
    grid_dtype,
    grid_frame_meta_path_for_run_root,
    grid_frame_path_for_run_root,
    write_slim_grid_frame_for_run_root,
)
from .cog_writer import warp_to_target_grid
from .fetch import HerbieTransientUnavailableError, convert_units, fetch_variable
from .pipeline import (
    _get_search_patterns,
    _resolve_model_var_capability,
    _resolve_model_var_spec,
    _warp_resampling_for_variable,
    check_pre_encode_value_sanity,
)

logger = logging.getLogger(__name__)

# Script-level backoff between fetch attempts, after fetch_variable's own
# internal retry/priority machinery has given up (spike-validated schedule;
# the spike observed zero retries needed across 2,009 requests).
FETCH_BACKOFF_SCHEDULE: tuple[float, ...] = (5.0, 15.0, 45.0)

DEFAULT_MEMBER_FETCH_WORKERS = 2
MAX_MEMBER_FETCH_WORKERS = 4
ENV_MEMBER_FETCH_WORKERS = "CARTOSKY_MEMBER_FETCH_WORKERS"

STATUS_WRITTEN = "written"
STATUS_RESUMED = "resumed"
STATUS_GATE_FAILED = "gate_failed"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_ERROR = "error"
STATUS_PREEMPTED = "preempted"


def member_fetch_workers() -> int:
    raw = str(os.getenv(ENV_MEMBER_FETCH_WORKERS, "") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MEMBER_FETCH_WORKERS
    except ValueError:
        value = DEFAULT_MEMBER_FETCH_WORKERS
    return max(1, min(MAX_MEMBER_FETCH_WORKERS, value))


def member_var_id(base_var: str, member: str) -> str:
    """``tmp2m`` + ``m07`` -> ``tmp2m__m07``; ``control`` -> ``tmp2m__control``."""
    return f"{str(base_var).strip().lower()}__{str(member).strip().lower()}"


def member_herbie_kwarg(member: str) -> int:
    """Herbie ``member`` kwarg: perturbation number, 0 for control (gec00)."""
    normalized = str(member).strip().lower()
    if normalized == "control":
        return 0
    return int(normalized.lstrip("mp"))


def member_frame_is_complete(run_root: Path, model: str, var_id: str, fh: int) -> bool:
    """Resume check: slim ``.bin`` exists with the size its meta promises."""
    packing = _packing_config(model, var_id)
    if packing is None:
        return False
    packing_dtype = grid_dtype(str(packing.get("dtype") or ""))
    frame_path = grid_frame_path_for_run_root(run_root, var_id, fh, dtype=packing_dtype)
    meta_path = grid_frame_meta_path_for_run_root(run_root, var_id, fh)
    if not frame_path.is_file() or not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text())
        expected = expected_grid_frame_size_bytes(
            width=int(meta["width"]), height=int(meta["height"]), dtype=packing_dtype,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return expected > 0 and frame_path.stat().st_size == expected


@dataclass
class _MemberVarContext:
    """Resolved per-variable build inputs, mirroring build_frame's resolution."""

    base_var: str
    member_ids: list[str]
    var_spec: Any
    capability: Any
    colormap_spec: dict[str, Any]
    resampling: str
    search_patterns: list[str]
    product: str


def _resolve_member_var_context(plugin: Any, model_id: str, base_var: str, descriptor: dict[str, Any]) -> _MemberVarContext:
    var_spec = _resolve_model_var_spec(model_id, base_var, plugin)
    capability = _resolve_model_var_capability(model_id, base_var, plugin)
    color_map_id = str(getattr(capability, "color_map_id", "") or "").strip()
    colormap_spec = get_color_map_spec(color_map_id)
    kind = str(getattr(capability, "kind", None) or getattr(var_spec, "kind", "") or "continuous")
    resampling = _warp_resampling_for_variable(model_id=model_id, var_key=base_var, kind=kind)
    selectors = getattr(var_spec, "selectors", None)
    hints = getattr(selectors, "hints", {}) if selectors is not None else {}
    default_product = str(getattr(plugin, "product", "") or "sfc")
    product = str((hints or {}).get("product") or default_product).strip() or default_product
    search_patterns = _get_search_patterns(
        var_spec, model_plugin=plugin, var_key=base_var, fh=None, product=product,
    )
    return _MemberVarContext(
        base_var=base_var,
        member_ids=ensemble_member_ids(descriptor),
        var_spec=var_spec,
        capability=capability,
        colormap_spec=colormap_spec,
        resampling=resampling,
        search_patterns=search_patterns,
        product=product,
    )


def _stop_aware_wait(delay_s: float, should_stop: Callable[[], bool]) -> bool:
    """Sleep up to ``delay_s``, polling ``should_stop``. True when stopped."""
    deadline = time.monotonic() + delay_s
    while time.monotonic() < deadline:
        if should_stop():
            return True
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return should_stop()


class MemberFetchError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


def _fetch_member_field(
    *,
    plugin: Any,
    model_id: str,
    ctx: _MemberVarContext,
    member: str,
    fh: int,
    run_date: datetime,
    should_stop: Callable[[], bool],
) -> tuple[np.ndarray, Any, Any, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    last_exc: Exception | None = None
    delays = (0.0, *FETCH_BACKOFF_SCHEDULE)
    for attempt_idx, delay in enumerate(delays, start=1):
        if delay > 0:
            logger.info(
                "Member fetch backoff %.0fs before retry %d: %s/%s fh%03d",
                delay, attempt_idx, model_id, member_var_id(ctx.base_var, member), fh,
            )
            if _stop_aware_wait(delay, should_stop):
                raise MemberFetchError(
                    f"preempted during backoff for {member} fh{fh:03d}", attempts,
                )
        if should_stop():
            raise MemberFetchError(f"preempted before fetch of {member} fh{fh:03d}", attempts)
        started = time.perf_counter()
        try:
            request = plugin.herbie_request(
                product=ctx.product,
                var_key=ctx.base_var,
                ensemble_view="mean",
                run_date=run_date,
                fh=fh,
                search_pattern=ctx.search_patterns[0],
            )
            herbie_kwargs = dict(request.herbie_kwargs)
            herbie_kwargs["member"] = member_herbie_kwarg(member)
            last_pattern_exc: Exception | None = None
            for pattern in ctx.search_patterns:
                try:
                    raw, crs, transform = fetch_variable(
                        model_id=request.model,
                        product=request.product,
                        search_pattern=pattern,
                        run_date=run_date,
                        fh=fh,
                        herbie_kwargs=herbie_kwargs,
                    )
                    return raw, crs, transform, attempts
                except (HerbieTransientUnavailableError, RuntimeError) as exc:
                    last_pattern_exc = exc
            raise last_pattern_exc or RuntimeError(
                f"no usable search pattern for {member} fh{fh:03d}"
            )
        except Exception as exc:  # noqa: BLE001 — recorded; retried next pass
            last_exc = exc
            attempts.append({
                "member": member,
                "fh": int(fh),
                "attempt": attempt_idx,
                "elapsed_s": round(time.perf_counter() - started, 3),
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
            logger.warning(
                "Member fetch attempt %d failed: %s/%s fh%03d: %s",
                attempt_idx, model_id, member_var_id(ctx.base_var, member), fh,
                attempts[-1]["error"],
            )
    raise MemberFetchError(
        f"fetch failed for {member} fh{fh:03d} after {len(attempts)} attempt(s)",
        attempts,
    ) from last_exc


def build_member_frame(
    *,
    plugin: Any,
    model_id: str,
    region: str,
    ctx: _MemberVarContext,
    member: str,
    fh: int,
    run_date: datetime,
    staging_run_root: Path,
    should_stop: Callable[[], bool],
) -> str:
    """Build one slim member frame; returns a STATUS_* string."""
    var_id = member_var_id(ctx.base_var, member)
    try:
        raw, src_crs, src_transform, _attempts = _fetch_member_field(
            plugin=plugin, model_id=model_id, ctx=ctx,
            member=member, fh=fh, run_date=run_date, should_stop=should_stop,
        )
    except MemberFetchError as exc:
        if "preempted" in str(exc):
            return STATUS_PREEMPTED
        return STATUS_FETCH_FAILED

    converted = convert_units(
        raw, var_key=ctx.base_var, model_id=model_id, var_capability=ctx.capability,
    )
    warped, dst_transform = warp_to_target_grid(
        converted,
        src_crs,
        src_transform,
        model=model_id,
        region=region,
        resampling=ctx.resampling,
        src_nodata=None,
        dst_nodata=float("nan"),
    )

    if not check_pre_encode_value_sanity(
        warped,
        ctx.colormap_spec,
        var_spec_model=ctx.var_spec,
        var_capability=ctx.capability,
        label=f"{model_id}/{var_id}/fh{fh:03d} (member pass)",
    ):
        logger.error(
            "PRE-ENCODE SANITY GATE FAILED — member frame NOT written: %s/%s/fh%03d "
            "(a silently-bad member poisons downstream member products; the gate is never bypassed)",
            model_id, var_id, fh,
        )
        return STATUS_GATE_FAILED

    write_slim_grid_frame_for_run_root(
        run_root=staging_run_root,
        model=model_id,
        var=var_id,
        fh=fh,
        values=warped,
        transform=dst_transform,
    )
    return STATUS_WRITTEN


def member_pass_pending(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
) -> bool:
    """Cheap presence scan: does any enabled (member, fh) frame remain unwritten?

    Gate-failed frames stay "pending" by this definition and are re-attempted
    on later passes; a persistent gate failure is loudly logged each pass
    rather than silently forgotten.
    """
    descriptors = ensemble_member_descriptors(plugin)
    if not descriptors:
        return False
    staging_run_root = data_root / "staging" / model_id / run_id
    cycle_hour = _cycle_hour_from_run_id(run_id)
    for base_var, descriptor in descriptors.items():
        fhs = [int(fh) for fh in plugin.scheduled_fhs_for_var(base_var, cycle_hour)]
        for member in ensemble_member_ids(descriptor):
            var_id = member_var_id(base_var, member)
            for fh in fhs:
                if not member_frame_is_complete(staging_run_root, model_id, var_id, fh):
                    return True
    return False


def member_promote_pending(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
) -> bool:
    """Any member frame complete in STAGING but not in PUBLISHED?

    Covers the crash window between a completed pass and its promote: the
    next pass would find no build work, but the staged frames still need to
    ride a promote or they'd be stranded until the run ages out.
    """
    descriptors = ensemble_member_descriptors(plugin)
    if not descriptors:
        return False
    staging_run_root = data_root / "staging" / model_id / run_id
    published_run_root = data_root / "published" / model_id / run_id
    cycle_hour = _cycle_hour_from_run_id(run_id)
    for base_var, descriptor in descriptors.items():
        fhs = [int(fh) for fh in plugin.scheduled_fhs_for_var(base_var, cycle_hour)]
        for member in ensemble_member_ids(descriptor):
            var_id = member_var_id(base_var, member)
            for fh in fhs:
                if member_frame_is_complete(
                    staging_run_root, model_id, var_id, fh,
                ) and not member_frame_is_complete(published_run_root, model_id, var_id, fh):
                    return True
    return False


def _cycle_hour_from_run_id(run_id: str) -> int:
    return int(str(run_id)[9:11])


def _run_date_from_run_id(run_id: str) -> datetime:
    return datetime.strptime(str(run_id), "%Y%m%d_%Hz")


@dataclass
class MemberPassSummary:
    run_id: str
    model_id: str
    counts: dict[str, int] = field(default_factory=dict)
    member_var_ids: list[str] = field(default_factory=list)
    frame_times_s: list[float] = field(default_factory=list)
    wall_s: float = 0.0
    preempted: bool = False

    @property
    def complete(self) -> bool:
        pending = (
            self.counts.get(STATUS_FETCH_FAILED, 0)
            + self.counts.get(STATUS_GATE_FAILED, 0)
            + self.counts.get(STATUS_ERROR, 0)
            + self.counts.get(STATUS_PREEMPTED, 0)
        )
        return pending == 0

    def asdict(self) -> dict[str, Any]:
        return {
            "run": self.run_id,
            "model": self.model_id,
            "counts": dict(self.counts),
            "member_var_count": len(self.member_var_ids),
            "frame_time_mean_s": (
                round(statistics.mean(self.frame_times_s), 3) if self.frame_times_s else None
            ),
            "wall_s": round(self.wall_s, 2),
            "preempted": self.preempted,
            "complete": self.complete,
        }


def run_member_pass(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
    region: str,
    workers: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> MemberPassSummary:
    """Run the member publish pass for one run; idempotent and preemptible.

    Writes slim frames into the STAGING run root only — the caller (the
    scheduler) is responsible for member manifest builds and promotion.
    """
    resolved_workers = workers if workers is not None else member_fetch_workers()
    resolved_workers = max(1, min(MAX_MEMBER_FETCH_WORKERS, int(resolved_workers)))
    stop = should_stop or (lambda: False)
    summary = MemberPassSummary(run_id=run_id, model_id=model_id)
    descriptors = ensemble_member_descriptors(plugin)
    if not descriptors:
        logger.info("Member pass: no enabled member descriptors for model=%s", model_id)
        return summary

    staging_run_root = data_root / "staging" / model_id / run_id
    run_date = _run_date_from_run_id(run_id)
    cycle_hour = _cycle_hour_from_run_id(run_id)
    started = time.perf_counter()
    counts_lock = threading.Lock()

    def _record(status: str, frame_time_s: float | None = None) -> None:
        with counts_lock:
            summary.counts[status] = summary.counts.get(status, 0) + 1
            if frame_time_s is not None:
                summary.frame_times_s.append(frame_time_s)

    for base_var, descriptor in descriptors.items():
        if stop():
            summary.preempted = True
            break
        try:
            ctx = _resolve_member_var_context(plugin, model_id, base_var, descriptor)
        except Exception:
            logger.exception(
                "Member pass: failed resolving var context model=%s var=%s", model_id, base_var,
            )
            _record(STATUS_ERROR)
            continue
        summary.member_var_ids.extend(member_var_id(base_var, m) for m in ctx.member_ids)
        fhs = [int(fh) for fh in plugin.scheduled_fhs_for_var(base_var, cycle_hour)]
        tasks = [(member, fh) for member in ctx.member_ids for fh in fhs]

        def _one(task: tuple[str, int]) -> None:
            member, fh = task
            if stop():
                _record(STATUS_PREEMPTED)
                return
            var_id = member_var_id(ctx.base_var, member)
            if member_frame_is_complete(staging_run_root, model_id, var_id, fh):
                _record(STATUS_RESUMED)
                return
            frame_started = time.perf_counter()
            try:
                status = build_member_frame(
                    plugin=plugin,
                    model_id=model_id,
                    region=region,
                    ctx=ctx,
                    member=member,
                    fh=fh,
                    run_date=run_date,
                    staging_run_root=staging_run_root,
                    should_stop=stop,
                )
            except Exception:  # noqa: BLE001 — recorded; retried next pass
                logger.exception(
                    "Member pass: unexpected error building %s/%s fh%03d",
                    model_id, var_id, fh,
                )
                _record(STATUS_ERROR)
                return
            _record(status, time.perf_counter() - frame_started if status == STATUS_WRITTEN else None)

        if resolved_workers <= 1:
            for task in tasks:
                _one(task)
        else:
            with ThreadPoolExecutor(
                max_workers=resolved_workers, thread_name_prefix="member-pass",
            ) as pool:
                futures = [pool.submit(_one, task) for task in tasks]
                for future in as_completed(futures):
                    future.result()

    summary.wall_s = time.perf_counter() - started
    if summary.counts.get(STATUS_PREEMPTED, 0) > 0:
        summary.preempted = True
    logger.info(
        "Member pass summary: run=%s model=%s %s wall=%.1fs workers=%d complete=%s preempted=%s",
        run_id,
        model_id,
        " ".join(f"{key}={value}" for key, value in sorted(summary.counts.items())) or "no_work",
        summary.wall_s,
        resolved_workers,
        summary.complete,
        summary.preempted,
    )
    return summary
