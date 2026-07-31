"""Parallel execution of the per-frame sounding thermodynamics (Phase 5).

Normative: ``docs/SKEWT_DESIGN_2026-07-30.md`` §7 Phase 5.

## Why a process pool

A 49-frame sounding response runs 49 **independent** MetPy passes: the Phase 4
parcel ascent / CAPE-CIN integrals (~0.9 s total) and the Phase 5 wet-bulb
profile (~1.3 s total). Both are pure-Python + SciPy ODE work, so they are
GIL-bound — threads buy nothing. Batching does not help either: measured
2026-07-31, ``metpy.wet_bulb_temperature`` costs ~660-1060 µs *per element*
(one ``solve_ivp`` moist-lapse integration each) and concatenating every frame
into one call was **slower** than 49 sequential calls, because per-element cost
degrades as the array grows. Independent frames across processes is the only
lever that does not change the response shape or the thermo library.

## Contract

:func:`compute_frames` is a drop-in replacement for looping
:func:`app.services.sounding_indices.compute_frame_indices` over the frames. It
returns one result dict per input, in order, and **never raises**: per-frame
containment is unchanged (a frame that fails yields all-null, and the other 48
are still served).

## Safety

* **spawn, never fork.** The caller is an async API process with helper threads
  and open sockets; forking that is unsafe. ``spawn`` costs a fresh interpreter
  per worker but is the only correct choice here.
* The worker entry point is ``sounding_indices.compute_frame_indices``, whose
  module imports only ``numpy`` (MetPy is imported lazily inside it). A worker
  never imports ``app.main``, FastAPI, or anything else heavy.
* Everything crossing the process boundary is plain lists / dicts / floats.

## Degradation

If the pool cannot start, or a submit fails, or the pool breaks mid-flight, the
whole request transparently falls back to the serial path and the pool is
disabled for the life of the process (logged once). A sandbox that forbids
subprocesses therefore behaves exactly like Phase 4 did, only slower than a
healthy box.

## Ops escape hatch

``CARTOSKY_SOUNDING_THERMO_WORKERS`` overrides the worker count:

* ``0`` (or ``1``) — force the serial path, no subprocesses at all.
* ``N`` — use N workers instead of the default.
* unset — ``min(4, cpu_count // 2 or 1)``.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from threading import Lock
from typing import Any, Mapping, Sequence

from . import sounding_indices

logger = logging.getLogger(__name__)

#: Ops override for the worker count; ``0`` forces the serial path.
WORKERS_ENV_VAR = "CARTOSKY_SOUNDING_THERMO_WORKERS"

#: Never take more than half the box, and never more than this.
MAX_WORKERS_CAP = 4

#: Below this many frames the spawn/pickle overhead outweighs the parallelism.
MIN_FRAMES_FOR_POOL = 4

_EMPTY_RESULT: dict[str, Any] = {"indices": None, "parcel": None, "profiles": None}

_pool_lock = Lock()
_pool: ProcessPoolExecutor | None = None
_pool_disabled = False


def configured_workers() -> int:
    """Worker count for this process. ``<= 1`` means "run serially"."""
    raw = os.environ.get(WORKERS_ENV_VAR)
    if raw is not None and raw.strip() != "":
        try:
            return max(0, int(raw.strip()))
        except ValueError:
            logger.warning("%s is not an integer: %r; using the default", WORKERS_ENV_VAR, raw)

    cpu_count = os.cpu_count() or 1
    return max(1, min(MAX_WORKERS_CAP, cpu_count // 2))


def _disable_pool(reason: str, *, exc_info: bool = True) -> None:
    """Shut the pool down and never try again in this process."""
    global _pool, _pool_disabled
    with _pool_lock:
        _pool_disabled = True
        pool, _pool = _pool, None
    logger.warning("Sounding thermo pool disabled (%s); falling back to serial", reason,
                   exc_info=exc_info)
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - shutting down must never raise
            logger.debug("Sounding thermo pool shutdown failed", exc_info=True)


def _get_pool(workers: int) -> ProcessPoolExecutor | None:
    """The lazily created, process-lifetime pool, or ``None`` if unusable.

    Workers are spawned on first submit, and each pays the MetPy import
    (~1.8 s) once on its first task — a first-request cost that later requests,
    and every cached request, never see again.
    """
    global _pool, _pool_disabled
    with _pool_lock:
        if _pool_disabled:
            return None
        if _pool is not None:
            return _pool
        try:
            _pool = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
            )
        except Exception:  # noqa: BLE001 - sandboxes can forbid subprocesses
            _pool = None
            _pool_disabled = True
            logger.warning(
                "Sounding thermo pool could not start; falling back to serial", exc_info=True
            )
            return None
        return _pool


def shutdown_pool() -> None:
    """Release the workers (tests; also a hook for an app shutdown handler)."""
    global _pool, _pool_disabled
    with _pool_lock:
        pool, _pool = _pool, None
        _pool_disabled = False
    if pool is not None:
        try:
            pool.shutdown(wait=True)
        except Exception:  # noqa: BLE001
            logger.debug("Sounding thermo pool shutdown failed", exc_info=True)


def _compute_serial(frames: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        try:
            results.append(sounding_indices.compute_frame_indices(**frame))
        except Exception:  # noqa: BLE001 - the module guards too; belt and braces
            logger.exception("Sounding thermo failed for frame %d", index)
            results.append(dict(_EMPTY_RESULT))
    return results


def compute_frames(frames: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``compute_frame_indices`` for every frame, in parallel where possible.

    Each mapping is the keyword arguments for one frame
    (``levels_hpa`` / ``t`` / ``td`` / ``u`` / ``v`` / ``surface``). Results come
    back in the same order.
    """
    if not frames:
        return []

    workers = configured_workers()
    if workers <= 1 or len(frames) < MIN_FRAMES_FOR_POOL:
        return _compute_serial(frames)

    pool = _get_pool(workers)
    if pool is None:
        return _compute_serial(frames)

    try:
        futures = [pool.submit(sounding_indices.compute_frame_indices, **frame) for frame in frames]
    except Exception:  # noqa: BLE001 - pool died between the check and the submit
        _disable_pool("submit failed")
        return _compute_serial(frames)

    results: list[dict[str, Any]] = []
    for index, future in enumerate(futures):
        try:
            results.append(future.result())
        except BrokenExecutor:
            # The pool itself is gone: redo the WHOLE request serially rather
            # than serving a response that is null from this frame onward.
            _disable_pool("executor broke mid-request")
            return _compute_serial(frames)
        except Exception:  # noqa: BLE001 - one frame's worker blew up
            logger.exception("Sounding thermo worker failed for frame %d", index)
            results.append(dict(_EMPTY_RESULT))
    return results
