#!/usr/bin/env python3
"""Ensemble Member Sizing Spike — Phase 1 of docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md.

Measurement-only, throwaway-adjacent. Publishes GEFS ``tmp2m`` member frames
(m01–m30 + control, slim grid-binary profile) into a canary directory — NEVER
under ``data/published/`` or the staging tree — and records the plan Section 7
measurements into ``results.json`` + a plain-text log.

This script makes ZERO modifications to production code: it imports production
primitives read-only (fetch, warp, sanity gate, encode, path helpers, binary
sampler) and locally reimplements only what cannot be reused unmodified:

  * the slim frame writer — ``write_grid_frame_for_run_root`` is not
    profile-aware (it unconditionally applies display prep and env-gated
    sidecars), so the ``.bin`` + meta write is re-implemented here with the
    exact same encode call, atomic tmp+rename, and meta schema;
  * member→packing suffix normalization (``tmp2m__m01``/``tmp2m__control`` →
    the ``("gefs", "tmp2m__mean")`` packing entry) — a Phase 2 production
    deliverable, done locally here on purpose;
  * a measurement-only 3× upscale mirroring the continuous display-prep
    branch of ``prepare_grid_display_values`` (tmp2m has no display-prep
    config, so the production primitive cannot be invoked for it without a
    config change).

The obsolete value-COG-era ``backend/scripts/phase3_sizing_spike.py`` is NOT
reused (plan Section 7 warning).

=======================================================================
RUNBOOK (Brian executes all prod commands; deploy via git push + pull)
=======================================================================

All commands run on the prod server as the usual ops user, from the repo
root, with the reduced-priority prefix. The script is fully non-interactive.

Gate A — dry run (2 members × 3 fhs = 6 frames, ~2–5 min)::

    nice -n 10 ionice -c2 -n7 /opt/cartosky/.venv/bin/python3 \
        backend/scripts/ensemble_member_sizing_spike.py \
        --data-root /opt/cartosky/data \
        --canary-root /opt/cartosky/canary/gefs_members \
        --members m01,m02 --fhs 0,6,12 --parallel 2

    Return to the agent: {canary_root}/{run_id}/results.json and spike.log.

Gate B — full spike (31 members × 65 fhs ≈ 2,015 fetches; expect roughly
45–120 min wall depending on upstream; scratch usage ≈ 2–3 GB)::

    nice -n 10 ionice -c2 -n7 /opt/cartosky/.venv/bin/python3 \
        backend/scripts/ensemble_member_sizing_spike.py \
        --data-root /opt/cartosky/data \
        --canary-root /opt/cartosky/canary/gefs_members \
        --parallel 2 --resume

    (--resume is safe on a fresh tree; after any interruption re-run the
    exact same command and completed frames are skipped.)

Optional memory-cap bonus (measurement 3): wrap the same command in a scope
with the GEFS caps and afterwards record the cgroup high-throttle counter::

    systemd-run --scope -p MemoryHigh=3G -p MemoryMax=3500M -- \
        nice -n 10 ionice -c2 -n7 /opt/cartosky/.venv/bin/python3 ... (as above)
    # after completion: grep . /sys/fs/cgroup/system.slice/<scope>/memory.events

Monitoring::

    tail -f /opt/cartosky/canary/gefs_members/<run_id>/spike.log

Abort safely: Ctrl-C or ``kill -INT <pid>`` — the script finishes the frame
in flight, writes a partial results.json, and exits 130. Re-run with
--resume to continue. It also self-aborts (partial results written) if free
disk on the data volume drops below --disk-floor-gb (default 100) or its own
RSS exceeds --rss-limit-gb (default 3).

Scheduler-delay evidence (measurement 2, second half): results.json records
the spike window (spike_window_utc). After the run, capture GEFS mean build
latency across that window for comparison against recent norm, e.g.::

    journalctl -u csky-gefs-scheduler --since "<start>" --until "<end>" \
        | grep -Ei "publish|build complete" > gefs_scheduler_window.txt

Cleanup (only AFTER the spike doc is authored and measurements recorded)::

    /opt/cartosky/.venv/bin/python3 \
        backend/scripts/ensemble_member_sizing_spike.py \
        --data-root /opt/cartosky/data \
        --canary-root /opt/cartosky/canary/gefs_members \
        --cleanup --run <run_id>
    # records tree sizes to cleanup.json, then deletes the member trees
    # (results.json / logs are kept).

Local verification only (no network, no prod paths)::

    python3 backend/scripts/ensemble_member_sizing_spike.py --selftest

Exit codes::

  0   success (all requested work done; individual fetch failures are
      recorded in results.json, not fatal — rerun with --resume)
  1   usage / environment error
  2   no eligible target run (newest retained run with full mean coverage)
  3   aborted by disk-floor or RSS guard (partial results written)
  130 SIGINT (partial results written)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Path setup (mirrors canary_binary_sampler.py) ───────────────────
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Parse --data-root early so it is set before any app module reads env.
_early_parser = argparse.ArgumentParser(add_help=False)
_early_parser.add_argument(
    "--data-root",
    default=os.environ.get("CARTOSKY_DATA_ROOT", "./data"),
    help="CartoSky data root (default: $CARTOSKY_DATA_ROOT or ./data)",
)
_early_args, _ = _early_parser.parse_known_args()
os.environ.setdefault(
    "CARTOSKY_DATA_ROOT",
    str(Path(_early_args.data_root).expanduser().resolve()),
)

# Safe to import app modules now (read-only reuse; no production writes).
import numpy as np
from rasterio.transform import Affine, array_bounds, from_bounds
from scipy.ndimage import zoom as ndimage_zoom

from app.models.registry import MODEL_REGISTRY
from app.services.builder.cog_writer import warp_to_target_grid
from app.services.builder.fetch import (
    HerbieTransientUnavailableError,
    _fetch_range_bytes,
    _inventory_line_from_row,
    _inventory_row_byte_range,
    _inventory_search,
    _priority_candidates,
    _priority_normalized,
    _quiet_herbie_kwargs,
    _read_grib_raster,
    convert_units,
    fetch_variable,
)
from app.services.builder.pipeline import (
    _get_search_patterns,
    _resolve_model_var_capability,
    _resolve_model_var_spec,
    _warp_resampling_for_variable,
    check_pre_encode_value_sanity,
)
from app.services.colormaps import get_color_map_spec
from app.services.grid import (
    GRID_DTYPE_UINT8,
    GRID_FRAME_FORMAT_VERSION,
    GRID_LEVEL,
    GRID_PROJECTION,
    _PACKING_BY_MODEL_VAR,
    _decode_values,
    _encode_values,
    expected_grid_frame_size_bytes,
    grid_dtype,
    grid_frame_meta_path_for_run_root,
    grid_frame_path_for_run_root,
    write_grid_brotli_sidecar,
    write_grid_gzip_sidecar,
    write_json_atomic,
)
from app.services.grid_display_prep import grid_display_prep_config
from app.services.sampling import read_binary_sample_value

logger = logging.getLogger("member_spike")

# ── Constants ────────────────────────────────────────────────────────
MODEL = "gefs"
VAR_BASE = "tmp2m"                 # build-target var key (normalized)
MEAN_VAR = "tmp2m__mean"           # published runtime id whose packing members share
PRODUCT = "atmos.5"
RUN_ID_RE = re.compile(r"^\d{8}_\d{2}z$")
MEMBER_SUFFIX_RE = re.compile(r"^(?P<base>.+)__(?:m\d{2}|control)$")

PERTURBED_MEMBERS = [f"m{i:02d}" for i in range(1, 31)]
CONTROL_MEMBER = "control"
ALL_MEMBERS = PERTURBED_MEMBERS + [CONTROL_MEMBER]

# Control-member Herbie kwarg candidates, tried in order (open decision #3).
# herbie's gefs template (verified locally, herbie 2024.8.0): member=0 -> "c00",
# member=int 1..30 -> "pNN", member="mean" -> "avg".
CONTROL_MEMBER_KWARG_CANDIDATES: list[Any] = [0, "c00"]

# Comparison sets (subset only; per-frame bytes extrapolate linearly).
COMPARISON_MEMBERS = [f"m{i:02d}" for i in range(1, 6)]
COMPARISON_FHS = [0, 48, 96, 192, 384]
MEASUREMENT_UPSCALE_FACTOR = 3

# Script-level fetch retry backoff (seconds) after the production
# fetch_variable retry machinery has itself given up on an attempt.
FETCH_BACKOFF_SCHEDULE = [5.0, 15.0, 45.0]

DEFAULT_DISK_FLOOR_GB = 100.0
DEFAULT_RSS_LIMIT_GB = 3.0
DEFAULT_STATS_THRESHOLD = 32.0  # arbitrary; measures RSS/wall, not product output

STATS_PERCENTILES = [10, 25, 50, 75, 90]

_HTTP_STATUS_RE = re.compile(r"\b(4\d{2}|5\d{2})\b")


# ── Errors / control flow ───────────────────────────────────────────
class SpikeAbort(RuntimeError):
    """Raised when a guard (disk floor / RSS ceiling) trips."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FetchFailed(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


# ── Packing suffix normalization (LOCAL — Phase 2 will productionize) ─
def resolve_member_packing_var(var_id: str) -> str:
    """Map a member runtime id to the packing-twin ``__mean`` id.

    ``tmp2m__m07`` / ``tmp2m__control`` -> ``tmp2m__mean``. Ids without a
    member suffix are returned unchanged. Members and mean MUST share packing
    constants (plan Section 3.4); this helper exists only inside the spike —
    do NOT add a fallback to grid.py from here.
    """
    match = MEMBER_SUFFIX_RE.match(str(var_id).strip().lower())
    if match:
        return f"{match.group('base')}__mean"
    return str(var_id).strip().lower()


def member_packing(model: str, var_id: str) -> dict[str, Any]:
    packing_var = resolve_member_packing_var(var_id)
    packing = _PACKING_BY_MODEL_VAR.get((str(model).strip().lower(), packing_var))
    if packing is None:
        raise ValueError(f"No packing entry for {model}/{packing_var} (from {var_id})")
    return packing


def member_var_id(member: str) -> str:
    if member == CONTROL_MEMBER:
        return f"{VAR_BASE}__control"
    return f"{VAR_BASE}__{member}"


def member_herbie_kwarg(member: str, *, control_kwarg: Any) -> Any:
    if member == CONTROL_MEMBER:
        return control_kwarg
    return int(member[1:])


# ── RSS / disk guards ────────────────────────────────────────────────
def _current_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore[import-untyped]

        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass
    status_path = Path("/proc/self/status")
    if status_path.is_file():
        try:
            for line in status_path.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except Exception:
            return None
    return None


def _peak_rss_bytes() -> int | None:
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # ru_maxrss is bytes on macOS, kilobytes on Linux.
        return peak if sys.platform == "darwin" else peak * 1024
    except Exception:
        return None


class RssTracker:
    """Samples current RSS on a background thread; keeps the observed max."""

    def __init__(self, interval_s: float = 0.5) -> None:
        self._interval = interval_s
        self._max_sampled = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if _current_rss_bytes() is None:
            logger.info("RSS sampling unavailable on this platform; relying on ru_maxrss")
            return
        self._thread = threading.Thread(target=self._loop, name="rss-sampler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            rss = _current_rss_bytes()
            if rss is not None and rss > self._max_sampled:
                self._max_sampled = rss

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def max_sampled_bytes(self) -> int:
        return self._max_sampled

    def peak_bytes(self) -> int | None:
        candidates = [b for b in (self._max_sampled or None, _peak_rss_bytes()) if b]
        return max(candidates) if candidates else None


@dataclass
class Guards:
    data_root: Path
    canary_root: Path
    disk_floor_bytes: int
    rss_limit_bytes: int
    enabled: bool = True

    def check(self) -> None:
        if not self.enabled:
            return
        for label, path in (("data volume", self.data_root), ("canary volume", self.canary_root)):
            probe = path if path.exists() else path.parent
            try:
                free = shutil.disk_usage(probe).free
            except OSError:
                continue
            if free < self.disk_floor_bytes:
                raise SpikeAbort(
                    f"free disk on {label} ({probe}) is {free / 1e9:.1f} GB, "
                    f"below the {self.disk_floor_bytes / 1e9:.0f} GB abort floor"
                )
        rss = _current_rss_bytes()
        if rss is not None and rss > self.rss_limit_bytes:
            raise SpikeAbort(
                f"script RSS {rss / 1e9:.2f} GB exceeds the "
                f"{self.rss_limit_bytes / 1e9:.1f} GB abort ceiling"
            )


# ── Logging ──────────────────────────────────────────────────────────
def _setup_logging(log_path: Path | None, verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    handlers.append(stream)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"))
        handlers.append(file_handler)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        handlers=handlers, force=True)


# ── Target-run selection ─────────────────────────────────────────────
def _cycle_hour_from_run_id(run_id: str) -> int:
    return int(run_id[9:11])


def _run_date_from_run_id(run_id: str) -> datetime:
    return datetime.strptime(run_id, "%Y%m%d_%Hz")


def _mean_frame_paths(published_root: Path, run_id: str, fh: int) -> tuple[Path, Path]:
    run_root = published_root / MODEL / run_id
    packing = _PACKING_BY_MODEL_VAR[(MODEL, MEAN_VAR)]
    dtype = grid_dtype(str(packing.get("dtype") or ""))
    frame = grid_frame_path_for_run_root(run_root, MEAN_VAR, fh, dtype=dtype)
    meta = grid_frame_meta_path_for_run_root(run_root, MEAN_VAR, fh)
    return frame, meta


def select_target_run(published_root: Path, *, requested_run: str | None) -> tuple[str, list[int]]:
    """Newest retained run with full mean coverage for tmp2m__mean.

    Plan rule: newest, never oldest — oldest risks retention eviction
    mid-spike. Returns (run_id, scheduled_fhs).
    """
    plugin = MODEL_REGISTRY.get(MODEL)
    if plugin is None:
        raise RuntimeError(f"Model plugin not found: {MODEL}")

    model_dir = published_root / MODEL
    if requested_run:
        candidates = [requested_run]
    else:
        if not model_dir.is_dir():
            raise FileNotFoundError(f"Published model dir not found: {model_dir}")
        candidates = sorted(
            (d.name for d in model_dir.iterdir() if d.is_dir() and RUN_ID_RE.match(d.name)),
            reverse=True,
        )
    if not candidates:
        raise RuntimeError(f"No retained {MODEL} runs under {model_dir}")

    for run_id in candidates:
        if not RUN_ID_RE.match(run_id):
            logger.warning("Skipping invalid run id: %s", run_id)
            continue
        cycle_hour = _cycle_hour_from_run_id(run_id)
        scheduled = [int(fh) for fh in plugin.scheduled_fhs_for_var(MEAN_VAR, cycle_hour)]
        missing = []
        for fh in scheduled:
            frame, meta = _mean_frame_paths(published_root, run_id, fh)
            if not frame.is_file() or not meta.is_file():
                missing.append(fh)
        if not missing:
            logger.info(
                "Target run selected: %s (%d scheduled frames, full %s coverage verified)",
                run_id, len(scheduled), MEAN_VAR,
            )
            return run_id, scheduled
        logger.warning(
            "Run %s missing %d/%d %s frames (e.g. fh%03d) — skipping",
            run_id, len(missing), len(scheduled), MEAN_VAR, missing[0],
        )
    raise RuntimeError(
        f"No retained {MODEL} run has full {MEAN_VAR} coverage "
        f"(checked: {', '.join(candidates)})"
    )


# ── Slim frame writer (LOCAL minimal reimplementation) ───────────────
def write_slim_member_frame(
    *,
    run_root: Path,
    model: str,
    var_id: str,
    fh: int,
    values: np.ndarray,
    transform: Any,
    projection: str = GRID_PROJECTION,
    display_prep_meta: dict[str, Any] | None = None,
    pre_upscale_shape: tuple[int, int] | None = None,
    compression_sidecars: bool = False,
) -> dict[str, Any]:
    """Write ``fh{NNN}.l0.u16.bin`` (atomic tmp+rename) + production-schema meta.

    Mirrors ``write_grid_frame_for_run_root`` minus display prep and env-gated
    sidecars (that function is not profile-aware — plan Section 3.2). The
    bounds are computed from the PRE-upscale dims when an upscale was applied,
    and the effective transform from the post-upscale shape — the same math as
    production. Packing constants come from the member's ``__mean`` twin via
    local suffix normalization.
    """
    packing = member_packing(model, var_id)
    packing_dtype = grid_dtype(str(packing.get("dtype") or ""))
    values_array = np.asarray(values, dtype=np.float32)

    bounds_h, bounds_w = pre_upscale_shape or values_array.shape[:2]
    left, bottom, right, top = array_bounds(bounds_h, bounds_w, transform)
    bounds = [float(left), float(bottom), float(right), float(top)]

    encoded = _encode_values(
        values_array,
        scale=float(packing["scale"]),
        offset=float(packing["offset"]),
        nodata=int(packing["nodata"]),
        dtype=packing_dtype,
    )
    height, width = encoded.shape
    if packing_dtype == GRID_DTYPE_UINT8:
        encoded_bytes = encoded.astype(np.uint8, copy=False).tobytes(order="C")
    else:
        encoded_bytes = encoded.astype("<u2", copy=False).tobytes(order="C")

    out_path = grid_frame_path_for_run_root(run_root, var_id, fh, dtype=packing_dtype)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(encoded_bytes)
    tmp_path.replace(out_path)
    if compression_sidecars:
        write_grid_gzip_sidecar(out_path, encoded_bytes)
        write_grid_brotli_sidecar(out_path, encoded_bytes)

    effective_transform = from_bounds(*bounds, width, height)
    frame_meta = {
        "format_version": GRID_FRAME_FORMAT_VERSION,
        "fh": int(fh),
        "level": int(GRID_LEVEL),
        "file": out_path.name,
        "width": width,
        "height": height,
        "bbox": bounds,
        "transform": [
            effective_transform.a,
            effective_transform.b,
            effective_transform.c,
            effective_transform.d,
            effective_transform.e,
            effective_transform.f,
        ],
        "projection": str(projection or GRID_PROJECTION),
    }
    if display_prep_meta:
        frame_meta["display_prep"] = display_prep_meta
    write_json_atomic(grid_frame_meta_path_for_run_root(run_root, var_id, fh), frame_meta)
    return frame_meta


def slim_frame_is_complete(run_root: Path, model: str, var_id: str, fh: int) -> bool:
    """Resume check: .bin exists with the size the meta promises + meta parses."""
    packing = member_packing(model, var_id)
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
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return expected > 0 and frame_path.stat().st_size == expected


# ── Measurement-only 3× upscale ──────────────────────────────────────
def measurement_upscale(values: np.ndarray, factor: int = MEASUREMENT_UPSCALE_FACTOR) -> np.ndarray:
    """Continuous-branch equivalent of ``prepare_grid_display_values`` upscale.

    tmp2m has no display-prep config, so the production primitive cannot be
    invoked for it without a config change; this mirrors its continuous path
    (order-1 zoom of NaN-filled values + order-0 zoom of the finite mask) and
    is clearly labeled measurement-only. Feeds the Tier 3 extrapolation row.
    """
    values_f32 = np.asarray(values, dtype=np.float32)
    finite_mask = np.isfinite(values_f32)
    upscaled = ndimage_zoom(
        np.where(finite_mask, values_f32, 0.0).astype(np.float32, copy=False),
        zoom=(factor, factor), order=1, mode="nearest", prefilter=False,
    ).astype(np.float32, copy=False)
    mask_up = ndimage_zoom(
        finite_mask.astype(np.float32, copy=False),
        zoom=(factor, factor), order=0, mode="nearest", prefilter=False,
    ) > 0.5
    upscaled[~mask_up] = np.nan
    return upscaled


# ── Fetch ────────────────────────────────────────────────────────────
@dataclass
class FetchStats:
    requests_attempted: int = 0
    requests_succeeded: int = 0
    retries: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def asdict(self) -> dict[str, Any]:
        return {
            "requests_attempted": self.requests_attempted,
            "requests_succeeded": self.requests_succeeded,
            "retries": self.retries,
            "failure_count": len(self.failures),
            "failures": self.failures,
        }


def _status_code_from_error(exc: Exception) -> int | None:
    match = _HTTP_STATUS_RE.search(str(exc))
    return int(match.group(1)) if match else None


@dataclass
class SpikeContext:
    plugin: Any
    region: str
    run_id: str
    run_date: datetime
    capability: Any
    var_spec: Any
    colormap_spec: dict[str, Any]
    resampling: str
    search_patterns: list[str]
    guards: Guards
    fetch_stats: FetchStats
    stop_event: threading.Event
    control_kwarg_resolved: Any = None
    control_kwarg_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def build_spike_context(run_id: str, guards: Guards, stop_event: threading.Event) -> SpikeContext:
    plugin = MODEL_REGISTRY.get(MODEL)
    if plugin is None:
        raise RuntimeError(f"Model plugin not found: {MODEL}")
    capabilities = getattr(plugin, "capabilities", None)
    region = str(getattr(capabilities, "canonical_region", "") or "").strip()
    if not region:
        raise RuntimeError(f"Model {MODEL} has no canonical_region in capabilities")
    var_spec = _resolve_model_var_spec(MODEL, VAR_BASE, plugin)
    capability = _resolve_model_var_capability(MODEL, VAR_BASE, plugin)
    colormap_spec = get_color_map_spec(str(getattr(capability, "color_map_id", "")).strip())
    kind = str(getattr(capability, "kind", None) or getattr(var_spec, "kind", "") or "continuous")
    resampling = _warp_resampling_for_variable(model_id=MODEL, var_key=VAR_BASE, kind=kind)
    search_patterns = _get_search_patterns(
        var_spec, model_plugin=plugin, var_key=VAR_BASE, fh=None, product=PRODUCT,
    )
    prep_config = grid_display_prep_config(MODEL, MEAN_VAR)
    logger.info(
        "Context: region=%s resampling=%s search_patterns=%s display_prep(%s)=%s",
        region, resampling, search_patterns, MEAN_VAR,
        "none (slim members are native 1x, matching production)" if prep_config is None
        else prep_config.id,
    )
    return SpikeContext(
        plugin=plugin,
        region=region,
        run_id=run_id,
        run_date=_run_date_from_run_id(run_id),
        capability=capability,
        var_spec=var_spec,
        colormap_spec=colormap_spec,
        resampling=resampling,
        search_patterns=search_patterns,
        guards=guards,
        fetch_stats=FetchStats(),
        stop_event=stop_event,
    )


def _herbie_kwargs_for_member(ctx: SpikeContext, member: str, fh: int, *, control_kwarg: Any) -> dict[str, Any]:
    request = ctx.plugin.herbie_request(
        product=PRODUCT,
        var_key=VAR_BASE,
        ensemble_view="mean",
        run_date=ctx.run_date,
        fh=fh,
        search_pattern=ctx.search_patterns[0],
    )
    kwargs = dict(request.herbie_kwargs)
    kwargs["member"] = member_herbie_kwarg(member, control_kwarg=control_kwarg)
    return kwargs


def fetch_member_field(
    ctx: SpikeContext, member: str, fh: int,
) -> tuple[np.ndarray, Any, Any, list[dict[str, Any]]]:
    """Fetch one member field via the production fetch path with backoff.

    Returns (raw_data, src_crs, src_transform, attempt_records). For the
    control member, resolves the Herbie member kwarg once by trying the
    candidates in order and records which one worked.
    """
    attempts: list[dict[str, Any]] = []

    control_candidates: list[Any]
    if member == CONTROL_MEMBER and ctx.control_kwarg_resolved is None:
        control_candidates = list(CONTROL_MEMBER_KWARG_CANDIDATES)
    elif member == CONTROL_MEMBER:
        control_candidates = [ctx.control_kwarg_resolved]
    else:
        control_candidates = [None]

    last_exc: Exception | None = None
    for candidate in control_candidates:
        delays = [0.0] + FETCH_BACKOFF_SCHEDULE
        for attempt_idx, delay in enumerate(delays, start=1):
            if delay > 0:
                logger.info(
                    "Backoff %.0fs before retry %d for %s fh%03d",
                    delay, attempt_idx, member, fh,
                )
                time.sleep(delay)
            if ctx.stop_event.is_set():
                raise FetchFailed(f"interrupted before fetch of {member} fh{fh:03d}", attempts)
            with ctx.fetch_stats.lock:
                ctx.fetch_stats.requests_attempted += 1
                if attempt_idx > 1:
                    ctx.fetch_stats.retries += 1
            started = time.perf_counter()
            try:
                kwargs = _herbie_kwargs_for_member(ctx, member, fh, control_kwarg=candidate)
                last_pattern_exc: Exception | None = None
                for pattern in ctx.search_patterns:
                    try:
                        raw, crs, transform = fetch_variable(
                            model_id=MODEL,
                            product=PRODUCT,
                            search_pattern=pattern,
                            run_date=ctx.run_date,
                            fh=fh,
                            herbie_kwargs=kwargs,
                        )
                        break
                    except (HerbieTransientUnavailableError, RuntimeError) as exc:
                        last_pattern_exc = exc
                else:
                    raise last_pattern_exc or RuntimeError(
                        f"no usable search pattern for {member} fh{fh:03d}"
                    )
                with ctx.fetch_stats.lock:
                    ctx.fetch_stats.requests_succeeded += 1
                if member == CONTROL_MEMBER and ctx.control_kwarg_resolved is None:
                    with ctx.control_kwarg_lock:
                        if ctx.control_kwarg_resolved is None:
                            ctx.control_kwarg_resolved = candidate
                            logger.info("Control member Herbie kwarg resolved: member=%r", candidate)
                return raw, crs, transform, attempts
            except Exception as exc:  # noqa: BLE001 — every failure is recorded
                last_exc = exc
                record = {
                    "member": member,
                    "fh": int(fh),
                    "attempt": attempt_idx,
                    "control_kwarg_candidate": repr(candidate) if member == CONTROL_MEMBER else None,
                    "elapsed_s": round(time.perf_counter() - started, 3),
                    "status_code": _status_code_from_error(exc),
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
                attempts.append(record)
                logger.warning(
                    "Fetch attempt %d failed for %s fh%03d (status=%s): %s",
                    attempt_idx, member, fh, record["status_code"], record["error"],
                )
                # A client-side invalid-member error will never succeed on
                # retry — move straight to the next control candidate.
                if member == CONTROL_MEMBER and isinstance(exc, ValueError):
                    break
    raise FetchFailed(
        f"fetch failed for {member} fh{fh:03d} after {len(attempts)} attempt(s)",
        attempts,
    ) from last_exc


# ── Frame build ──────────────────────────────────────────────────────
@dataclass
class FrameResult:
    member: str
    fh: int
    status: str  # written | resumed | gate_failed | fetch_failed | skipped_interrupt
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None


def build_member_frame(ctx: SpikeContext, slim_root: Path, member: str, fh: int) -> FrameResult:
    var_id = member_var_id(member)
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    try:
        raw, src_crs, src_transform, _ = fetch_member_field(ctx, member, fh)
    except FetchFailed as exc:
        with ctx.fetch_stats.lock:
            ctx.fetch_stats.failures.extend(exc.attempts)
        return FrameResult(member=member, fh=fh, status="fetch_failed", error=str(exc))
    timings["fetch_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    converted = convert_units(raw, var_key=VAR_BASE, model_id=MODEL, var_capability=ctx.capability)
    warped, dst_transform = warp_to_target_grid(
        converted,
        src_crs,
        src_transform,
        model=MODEL,
        region=ctx.region,
        resampling=ctx.resampling,
        src_nodata=None,
        dst_nodata=float("nan"),
    )
    timings["warp_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    gate_ok = check_pre_encode_value_sanity(
        warped,
        ctx.colormap_spec,
        var_spec_model=ctx.var_spec,
        var_capability=ctx.capability,
        label=f"{MODEL}/{var_id}/fh{fh:03d} (member spike)",
    )
    timings["gate_s"] = time.perf_counter() - t0
    if not gate_ok:
        logger.error(
            "PRE-ENCODE SANITY GATE FAILED — frame NOT written: %s/%s/fh%03d "
            "(one bad member poisons downstream stats; the gate is never skipped)",
            MODEL, var_id, fh,
        )
        return FrameResult(member=member, fh=fh, status="gate_failed",
                           timings=timings, error="pre-encode sanity gate failed")

    t0 = time.perf_counter()
    write_slim_member_frame(
        run_root=slim_root,
        model=MODEL,
        var_id=var_id,
        fh=fh,
        values=warped,
        transform=dst_transform,
    )
    timings["encode_write_s"] = time.perf_counter() - t0
    timings["total_s"] = sum(v for k, v in timings.items() if k != "total_s")
    return FrameResult(member=member, fh=fh, status="written", timings=timings)


def run_main_pass(
    ctx: SpikeContext,
    slim_root: Path,
    members: list[str],
    fhs: list[int],
    *,
    parallel: int,
    resume: bool,
) -> dict[str, Any]:
    """The core measurement: slim member publish across members × fhs."""
    tasks: list[tuple[str, int]] = [(member, fh) for member in members for fh in fhs]
    results: list[FrameResult] = []
    results_lock = threading.Lock()
    batch_started = time.perf_counter()

    def _one(task: tuple[str, int]) -> FrameResult:
        member, fh = task
        if ctx.stop_event.is_set():
            return FrameResult(member=member, fh=fh, status="skipped_interrupt")
        ctx.guards.check()
        var_id = member_var_id(member)
        if resume and slim_frame_is_complete(slim_root, MODEL, var_id, fh):
            logger.info("Resume: %s fh%03d already complete — skipping", var_id, fh)
            return FrameResult(member=member, fh=fh, status="resumed")
        try:
            result = build_member_frame(ctx, slim_root, member, fh)
        except SpikeAbort:
            raise
        except Exception as exc:  # noqa: BLE001 — recorded; retried on --resume
            logger.exception("Unexpected error building %s fh%03d", var_id, fh)
            return FrameResult(member=member, fh=fh, status="error",
                               error=f"{type(exc).__name__}: {exc}"[:500])
        logger.info(
            "Frame %s fh%03d: %s (%s)",
            var_id, fh, result.status,
            ", ".join(f"{k}={v:.2f}s" for k, v in result.timings.items()) or "-",
        )
        return result

    # Tasks are member-major, so control frames run consecutively at the end;
    # the first successful control fetch pins the control kwarg (lock-guarded),
    # and any concurrent control fetches just re-verify the same candidate.
    ordered_tasks = tasks
    abort: SpikeAbort | None = None
    if parallel <= 1:
        for task in ordered_tasks:
            try:
                results.append(_one(task))
            except SpikeAbort as exc:
                abort = exc
                break
    else:
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="member-fetch") as pool:
            futures = {pool.submit(_one, task): task for task in ordered_tasks}
            for future in as_completed(futures):
                try:
                    result = future.result()
                except SpikeAbort as exc:
                    abort = exc
                    ctx.stop_event.set()
                    continue
                with results_lock:
                    results.append(result)

    batch_wall_s = time.perf_counter() - batch_started

    by_status: dict[str, int] = {}
    for result in results:
        by_status[result.status] = by_status.get(result.status, 0) + 1
    per_member: dict[str, dict[str, Any]] = {}
    stage_totals: dict[str, float] = {}
    for member in members:
        member_times = [r.timings.get("total_s", 0.0) for r in results
                        if r.member == member and r.status == "written"]
        if member_times:
            per_member[member_var_id(member)] = {
                "frames_written": len(member_times),
                "frame_time_mean_s": round(statistics.mean(member_times), 3),
                "frame_time_p95_s": round(_percentile(member_times, 95), 3),
            }
    for result in results:
        for key, value in result.timings.items():
            if key != "total_s":
                stage_totals[key] = stage_totals.get(key, 0.0) + value

    gate_failures = [
        {"member": r.member, "fh": r.fh, "error": r.error}
        for r in results if r.status == "gate_failed"
    ]
    fetch_failures = [
        {"member": r.member, "fh": r.fh, "error": r.error}
        for r in results if r.status == "fetch_failed"
    ]
    summary = {
        "expected_frames": len(tasks),
        "frames_by_status": by_status,
        "batch_wall_s": round(batch_wall_s, 2),
        "stage_totals_s": {k: round(v, 2) for k, v in sorted(stage_totals.items())},
        "per_member_frame_times": per_member,
        "gate_failures": gate_failures,
        "fetch_failures": fetch_failures,
        "aborted": abort.reason if abort else None,
    }
    return summary


# ── Byte / file-count measurement ────────────────────────────────────
def measure_tree(root: Path) -> dict[str, Any]:
    total_bytes = 0
    file_count = 0
    by_suffix: dict[str, dict[str, int]] = {}
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            size = path.stat().st_size
            total_bytes += size
            file_count += 1
            suffix = "".join(path.suffixes[-2:]) or path.suffix or "(none)"
            bucket = by_suffix.setdefault(suffix, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += size
    frame_count = by_suffix.get(".u16.bin", {}).get("files", 0)
    return {
        "path": str(root),
        "total_bytes": total_bytes,
        "total_mib": round(total_bytes / (1024 * 1024), 2),
        "file_count": file_count,
        "by_suffix": by_suffix,
        "bytes_per_frame": round(total_bytes / frame_count, 1) if frame_count else None,
        "frame_count": frame_count,
    }


# ── Comparison sets ──────────────────────────────────────────────────
def run_comparison_sets(
    ctx: SpikeContext,
    slim_root: Path,
    canary_run_dir: Path,
    members: list[str],
    fhs: list[int],
) -> dict[str, Any]:
    """Full-ish (gz+br sidecars) and 3× display-prep subsets from slim frames.

    Both sets are derived from the already-written slim binaries (decode →
    re-encode), so no re-fetch is needed: .bin sizes are deterministic in
    W×H×2 and sidecar sizes compress the identical encoded bytes. The 3× set
    quantizes the decoded values a second time — irrelevant for byte
    measurement, noted for the spike doc.
    """
    full_root = canary_run_dir / "comparison_full"
    up_root = canary_run_dir / "comparison_3x"
    packing_dtype = grid_dtype(str(member_packing(MODEL, member_var_id(members[0])).get("dtype") or ""))

    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    compress_wall_s = 0.0
    upscale_wall_s = 0.0

    for member in members:
        var_id = member_var_id(member)
        for fh in fhs:
            ctx.guards.check()
            frame_path = grid_frame_path_for_run_root(slim_root, var_id, fh, dtype=packing_dtype)
            meta_path = grid_frame_meta_path_for_run_root(slim_root, var_id, fh)
            if not frame_path.is_file() or not meta_path.is_file():
                skipped.append({"member": member, "fh": fh, "reason": "slim frame missing"})
                continue
            meta = json.loads(meta_path.read_text())
            encoded_bytes = frame_path.read_bytes()

            # (a) full-ish profile: same .bin + gzip + brotli sidecars.
            t0 = time.perf_counter()
            out_path = grid_frame_path_for_run_root(full_root, var_id, fh, dtype=packing_dtype)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp_path.write_bytes(encoded_bytes)
            tmp_path.replace(out_path)
            write_grid_gzip_sidecar(out_path, encoded_bytes)
            write_grid_brotli_sidecar(out_path, encoded_bytes)
            write_json_atomic(grid_frame_meta_path_for_run_root(full_root, var_id, fh), meta)
            compress_wall_s += time.perf_counter() - t0

            # (b) 3× display-prep equivalent, slim write.
            t0 = time.perf_counter()
            encoded = np.frombuffer(encoded_bytes, dtype="<u2").reshape(
                int(meta["height"]), int(meta["width"]))
            decoded = _decode_values(encoded, model=MODEL, var=resolve_member_packing_var(var_id))
            upscaled = measurement_upscale(decoded)
            transform_values = [float(v) for v in meta["transform"]]
            write_slim_member_frame(
                run_root=up_root,
                model=MODEL,
                var_id=var_id,
                fh=fh,
                values=upscaled,
                transform=Affine(*transform_values),
                projection=str(meta.get("projection") or GRID_PROJECTION),
                display_prep_meta={
                    "id": "spike_measurement_upscale_3x",
                    "upscale_factor": MEASUREMENT_UPSCALE_FACTOR,
                    "smooth_sigma": 0.0,
                    "note": "measurement-only equivalent of the continuous display-prep branch",
                },
                pre_upscale_shape=(int(meta["height"]), int(meta["width"])),
            )
            upscale_wall_s += time.perf_counter() - t0
            processed.append({"member": member, "fh": fh})

    return {
        "frames_requested": len(members) * len(fhs),
        "frames_processed": len(processed),
        "frames_skipped": skipped,
        "full_profile_subset": measure_tree(full_root),
        "upscaled_3x_subset": measure_tree(up_root),
        "compress_wall_s": round(compress_wall_s, 2),
        "upscale_wall_s": round(upscale_wall_s, 2),
        "note": (
            "derived from written slim binaries (decode → re-encode); "
            "sidecars compress identical encoded bytes; 3x set values are "
            "double-quantized (byte sizes unaffected)"
        ),
    }


# ── Promote + retention sweep simulation ─────────────────────────────
def run_promote_retention_sim(canary_run_dir: Path, slim_root: Path, guards: Guards) -> dict[str, Any]:
    """Time an atomic rename round-trip and a walk+delete of a COPY.

    Operates entirely inside the canary run dir; never touches the production
    published tree.
    """
    guards.check()
    scratch_dir = canary_run_dir / "scratch_promote"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    promoted_path = scratch_dir / slim_root.name

    t0 = time.perf_counter()
    os.rename(slim_root, promoted_path)
    rename_out_s = time.perf_counter() - t0
    try:
        t0 = time.perf_counter()
        os.rename(promoted_path, slim_root)
        rename_back_s = time.perf_counter() - t0
    except OSError:
        # Never leave the slim tree stranded in scratch.
        if promoted_path.exists() and not slim_root.exists():
            shutil.move(str(promoted_path), str(slim_root))
        raise

    copy_dir = canary_run_dir / "scratch_retention_copy"
    if copy_dir.exists():
        shutil.rmtree(copy_dir)
    guards.check()
    shutil.copytree(slim_root, copy_dir)
    copied = measure_tree(copy_dir)

    t0 = time.perf_counter()
    deleted_files = 0
    for dirpath, dirnames, filenames in os.walk(copy_dir, topdown=False):
        for name in filenames:
            os.unlink(os.path.join(dirpath, name))
            deleted_files += 1
        for name in dirnames:
            os.rmdir(os.path.join(dirpath, name))
    os.rmdir(copy_dir)
    sweep_s = time.perf_counter() - t0
    try:
        scratch_dir.rmdir()
    except OSError:
        pass

    return {
        "promote_rename_out_s": round(rename_out_s, 4),
        "promote_rename_back_s": round(rename_back_s, 4),
        "retention_sweep_files_deleted": deleted_files,
        "retention_sweep_bytes_deleted": copied["total_bytes"],
        "retention_sweep_wall_s": round(sweep_s, 3),
        "note": "sweep timed on a copy of the slim tree; production published tree untouched",
    }


# ── Stats-pass prototype (measurement 7) ─────────────────────────────
def run_stats_prototype(
    slim_root: Path,
    members: list[str],
    fhs: list[int],
    *,
    threshold: float,
    expected_member_count: int | None = None,
) -> dict[str, Any]:
    """Decode all member frames for one fh; percentiles + P(>threshold).

    Enforces the completeness gate (plan Section 3.3): asserts the full
    expected member set is present for the chosen fh before computing.
    """
    expected = expected_member_count if expected_member_count is not None else len(members)
    packing_dtype = grid_dtype(str(member_packing(MODEL, member_var_id(members[0])).get("dtype") or ""))

    chosen_fh: int | None = None
    for fh in fhs:
        complete = all(
            slim_frame_is_complete(slim_root, MODEL, member_var_id(m), fh) for m in members
        )
        if complete:
            chosen_fh = fh
            break
    if chosen_fh is None:
        return {
            "skipped": True,
            "reason": f"no fh has all {expected} member frames present (completeness gate)",
        }

    present = sum(
        1 for m in members if slim_frame_is_complete(slim_root, MODEL, member_var_id(m), chosen_fh)
    )
    assert present == expected, (
        f"completeness gate: fh{chosen_fh:03d} has {present}/{expected} member frames"
    )

    rss_before = _current_rss_bytes()
    t0 = time.perf_counter()
    stack: list[np.ndarray] = []
    for member in members:
        var_id = member_var_id(member)
        frame_path = grid_frame_path_for_run_root(slim_root, var_id, chosen_fh, dtype=packing_dtype)
        meta_path = grid_frame_meta_path_for_run_root(slim_root, var_id, chosen_fh)
        meta = json.loads(meta_path.read_text())
        encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(
            int(meta["height"]), int(meta["width"]))
        stack.append(_decode_values(encoded, model=MODEL, var=resolve_member_packing_var(var_id)))
    member_axis = np.stack(stack, axis=0)
    decode_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    percentiles = np.nanpercentile(member_axis, STATS_PERCENTILES, axis=0)
    finite_counts = np.sum(np.isfinite(member_axis), axis=0)
    exceed_counts = np.nansum(member_axis > threshold, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        prob = np.where(finite_counts > 0, exceed_counts / finite_counts, np.nan)
    compute_s = time.perf_counter() - t0
    rss_after = _current_rss_bytes()

    result = {
        "fh": chosen_fh,
        "member_count": expected,
        "grid_shape": list(member_axis.shape[1:]),
        "stack_mib": round(member_axis.nbytes / (1024 * 1024), 2),
        "percentiles_computed": STATS_PERCENTILES,
        "threshold": threshold,
        "prob_gt_threshold_mean": round(float(np.nanmean(prob)), 4),
        "p50_field_mean": round(float(np.nanmean(percentiles[STATS_PERCENTILES.index(50)])), 3),
        "decode_wall_s": round(decode_s, 3),
        "compute_wall_s": round(compute_s, 3),
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "rss_delta_mib": (
            round((rss_after - rss_before) / (1024 * 1024), 1)
            if rss_before is not None and rss_after is not None else None
        ),
    }
    del member_axis, stack, percentiles, prob
    return result


# ── Binary-sampler spot check ────────────────────────────────────────
def sampler_spot_check(slim_root: Path, members: list[str], fhs: list[int]) -> dict[str, Any]:
    """Sample member frames at interior / near-edge / out-of-coverage points.

    Points are generated from the model's region bbox (plan Section 3.7 — no
    hardcoded CONUS lists). Out-of-coverage must register as expected-missing
    (no_data=True), not an error. The sampler's packing lookup uses the
    ``__mean`` twin via the local suffix normalization.
    """
    plugin = MODEL_REGISTRY.get(MODEL)
    region_id = str(getattr(getattr(plugin, "capabilities", None), "canonical_region", "") or "")
    region_spec = plugin.get_region(region_id)
    west, south, east, north = region_spec.bbox_wgs84
    points = {
        "interior": ((south + north) / 2.0, (west + east) / 2.0),
        "near_edge": (south + 1.0, west + 1.0),
        "out_of_coverage": (
            max(-89.0, min(89.0, (south + north) / 2.0)),
            east + 30.0 if east + 30.0 < 180.0 else east - 360.0 + 30.0,
        ),
    }
    packing_dtype = grid_dtype(str(member_packing(MODEL, member_var_id(members[0])).get("dtype") or ""))

    # Three distinct frames spread across members/fhs where available.
    frame_choices: list[tuple[str, int]] = []
    for member, fh in zip(
        [members[0], members[len(members) // 2], members[-1]],
        [fhs[0], fhs[len(fhs) // 2], fhs[-1]],
    ):
        if slim_frame_is_complete(slim_root, MODEL, member_var_id(member), fh):
            frame_choices.append((member, fh))

    checks: list[dict[str, Any]] = []
    ok = True
    for member, fh in frame_choices:
        var_id = member_var_id(member)
        frame_path = grid_frame_path_for_run_root(slim_root, var_id, fh, dtype=packing_dtype)
        meta_path = grid_frame_meta_path_for_run_root(slim_root, var_id, fh)
        for label, (lat, lon) in points.items():
            record: dict[str, Any] = {
                "frame": f"{var_id}/fh{fh:03d}", "point": label,
                "lat": round(lat, 4), "lon": round(lon, 4),
            }
            try:
                value, no_data = read_binary_sample_value(
                    frame_path, meta_path,
                    model=MODEL, var=resolve_member_packing_var(var_id),
                    lat=lat, lon=lon,
                )
                record["value"] = None if value is None else round(float(value), 2)
                record["no_data"] = bool(no_data)
                if label == "out_of_coverage":
                    record["pass"] = bool(no_data)
                else:
                    record["pass"] = not no_data and value is not None
            except Exception as exc:  # noqa: BLE001 — a sampler error is a finding
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["pass"] = False
            ok = ok and bool(record.get("pass"))
            checks.append(record)
    return {"frames_checked": len(frame_choices), "checks": checks, "all_passed": ok}


# ── EPS mini-checks (measurements 4 + 6) ─────────────────────────────
def _eps_herbie_handle(run_date: datetime, fh: int) -> tuple[Any, str, str, str]:
    """Herbie handle for the EPS (ifs/enfo) upstream at the given cycle."""
    from herbie.core import Herbie  # lazy — matches production fetch style

    eps_plugin = MODEL_REGISTRY.get("eps")
    if eps_plugin is None:
        raise RuntimeError("EPS plugin not found")
    request = eps_plugin.herbie_request(
        product=None, var_key=VAR_BASE, ensemble_view="mean",
        run_date=run_date, fh=fh, search_pattern=None,
    )
    kwargs = dict(request.herbie_kwargs)
    kwargs.pop("_cartosky_fetch_aggregation", None)
    priorities = [
        _priority_normalized(item) for item in _priority_candidates(kwargs) if str(item).strip()
    ] or ["azure"]
    priority = priorities[0]
    run_kwargs = _quiet_herbie_kwargs({
        "model": request.model, "product": request.product, "fxx": int(fh), **kwargs,
    })
    run_kwargs["priority"] = priority
    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    return Herbie(herbie_date, **run_kwargs), request.model, request.product, priority


def _newest_published_run(published_root: Path, model: str) -> str | None:
    model_dir = published_root / model
    if not model_dir.is_dir():
        return None
    runs = sorted(
        (d.name for d in model_dir.iterdir() if d.is_dir() and RUN_ID_RE.match(d.name)),
        reverse=True,
    )
    return runs[0] if runs else None


def eps_control_minicheck(published_root: Path, canary_run_dir: Path, *, fh: int = 0) -> dict[str, Any]:
    """Fetch ONE EPS control (cf) field via inventory-row selection.

    Confirms the type == "cf" selection mechanism and records the member
    identifier (open decision #2). No EPS publishing of any kind.
    """
    run_id = _newest_published_run(published_root, "eps")
    if run_id is None:
        return {"skipped": True, "reason": "no retained eps run found for a valid cycle date"}
    run_date = _run_date_from_run_id(run_id)

    eps_plugin = MODEL_REGISTRY.get("eps")
    eps_var_spec = _resolve_model_var_spec("eps", VAR_BASE, eps_plugin)
    patterns = _get_search_patterns(
        eps_var_spec, model_plugin=eps_plugin, var_key=VAR_BASE, fh=fh, product=None,
    )
    H, model_id, product, priority = _eps_herbie_handle(run_date, fh)
    inv_result = _inventory_search(
        H, search_pattern=patterns[0], priority=priority,
        model_id=model_id, run_date=run_date, product=product, fh=fh,
    )
    inventory = inv_result.inventory
    if inv_result.reason != "ok" or inventory is None or len(inventory) == 0:
        return {
            "skipped": False, "ok": False, "eps_run": run_id, "fh": fh,
            "reason": f"inventory unavailable: {inv_result.reason}",
        }

    if "type" not in inventory.columns:
        return {"ok": False, "eps_run": run_id, "reason": "inventory has no 'type' column"}
    type_series = inventory["type"].astype(str).str.strip().str.lower()
    cf_rows = inventory.loc[type_series == "cf"]
    pf_rows = inventory.loc[type_series == "pf"]
    if len(cf_rows) == 0:
        return {
            "ok": False, "eps_run": run_id, "fh": fh,
            "pf_row_count": int(len(pf_rows)),
            "reason": "no cf row in inventory for pattern " + patterns[0],
        }

    cf_row = cf_rows.iloc[0]
    member_number = str(cf_row.get("number", "")).strip()
    inventory_line = _inventory_line_from_row(cf_row)

    # Byte-range fetch of just the cf row. Deliberately NOT via
    # _download_subset_with_inventory_rows: that path may consult (and on a
    # miss, populate/evict) the EPS full-file cache — far too heavy a side
    # effect for a one-row mini-check.
    byte_range = _inventory_row_byte_range(cf_row)
    if byte_range is None:
        return {
            "ok": False, "eps_run": run_id, "fh": fh,
            "inventory_line": inventory_line,
            "reason": "cf inventory row has no byte range",
        }
    source_url = str(getattr(H, "grib", "") or "")
    payload = _fetch_range_bytes(
        source=priority, source_url=source_url,
        model_id=model_id, run_date=run_date, fh=fh,
        start_byte=byte_range[0], end_byte=byte_range[1],
        bundle_fetch_cache=None, require_grib_payload=True,
    )
    subset_path = canary_run_dir / "eps_control_check" / f"eps_cf_fh{fh:03d}.grib2"
    subset_path.parent.mkdir(parents=True, exist_ok=True)
    subset_path.write_bytes(payload)
    data, _crs, _transform = _read_grib_raster(subset_path)
    field_shape: list[int] | None = list(data.shape)

    return {
        "ok": bool(payload) and field_shape is not None,
        "eps_run": run_id,
        "fh": fh,
        "search_pattern": patterns[0],
        "selection": 'inventory rows filtered to type == "cf"',
        "cf_row_count": int(len(cf_rows)),
        "pf_row_count": int(len(pf_rows)),
        "control_member_identifier": {"number": member_number, "type": "cf"},
        "inventory_line": inventory_line,
        "field_shape": field_shape,
        "subset_bytes": subset_path.stat().st_size if subset_path.is_file() else None,
    }


EPS_SNOWFALL_PATTERN = r":(sf|asn|sd|sde|csnow|snowc|tsnowp|snow[a-z]*):"


def eps_snowfall_inventory_check(published_root: Path, *, fh: int = 24) -> dict[str, Any]:
    """Inventory-level check only: does ECMWF enfo expose a direct snowfall
    field usable for EPS, or is derivation required? (Open decision #4.)
    Documents findings; implements nothing."""
    run_id = _newest_published_run(published_root, "eps")
    if run_id is None:
        return {"skipped": True, "reason": "no retained eps run found for a valid cycle date"}
    run_date = _run_date_from_run_id(run_id)

    H, model_id, product, priority = _eps_herbie_handle(run_date, fh)
    inv_result = _inventory_search(
        H, search_pattern=EPS_SNOWFALL_PATTERN, priority=priority,
        model_id=model_id, run_date=run_date, product=product, fh=fh,
    )
    inventory = inv_result.inventory
    if inventory is None:
        return {
            "ok": False, "eps_run": run_id, "fh": fh,
            "reason": f"inventory unavailable: {inv_result.reason}",
        }

    matches: list[dict[str, Any]] = []
    params_by_type: dict[str, dict[str, int]] = {}
    for row_index in range(len(inventory)):
        row = inventory.iloc[row_index]
        param = str(row.get("param", "")).strip()
        row_type = str(row.get("type", "")).strip().lower()
        params_by_type.setdefault(param, {}).setdefault(row_type, 0)
        params_by_type[param][row_type] += 1
        if len(matches) < 8:
            matches.append({
                "param": param, "type": row_type,
                "number": str(row.get("number", "")).strip(),
                "line": _inventory_line_from_row(row),
            })
    return {
        "ok": True,
        "eps_run": run_id,
        "fh": fh,
        "pattern": EPS_SNOWFALL_PATTERN,
        "match_count": int(len(inventory)),
        "params_by_type": params_by_type,
        "sample_matches": matches,
        "note": "inventory-level only; derivation-vs-direct decision documented in the spike doc",
    }


# ── Utilities ────────────────────────────────────────────────────────
def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    ordered = sorted(data)
    idx = (p / 100.0) * (len(ordered) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_results(canary_run_dir: Path, results: dict[str, Any]) -> Path:
    path = canary_run_dir / "results.json"
    canary_run_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(results, indent=2, default=str) + "\n")
    tmp.replace(path)
    logger.info("Results written: %s", path)
    return path


def _environment_report() -> dict[str, Any]:
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "argv": sys.argv,
        "pid": os.getpid(),
    }
    try:
        import herbie

        report["herbie_version"] = str(getattr(herbie, "__version__", "unknown"))
    except Exception:
        report["herbie_version"] = None
    return report


def _parse_members(raw: str) -> list[str]:
    if raw.strip().lower() == "all":
        return list(ALL_MEMBERS)
    members: list[str] = []
    for item in raw.split(","):
        token = item.strip().lower()
        if not token:
            continue
        if token not in ALL_MEMBERS:
            raise ValueError(
                f"unknown member {token!r} (expected m01..m30, control, or 'all')"
            )
        if token not in members:
            members.append(token)
    if not members:
        raise ValueError("--members contained no member names")
    return members


def _parse_fhs(raw: str, scheduled: list[int]) -> list[int]:
    if raw.strip().lower() == "all":
        return list(scheduled)
    fhs: list[int] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        fh = int(token)
        if fh not in scheduled:
            raise ValueError(f"fh {fh} is not in the scheduled set for this cycle")
        if fh not in fhs:
            fhs.append(fh)
    if not fhs:
        raise ValueError("--fhs contained no forecast hours")
    return sorted(fhs)


# ── Cleanup mode ─────────────────────────────────────────────────────
def run_cleanup(canary_run_dir: Path) -> int:
    """Record member-tree sizes, then delete them (results/logs kept)."""
    if not canary_run_dir.is_dir():
        logger.error("Canary run dir not found: %s", canary_run_dir)
        return 1
    targets = ["slim", "comparison_full", "comparison_3x", "eps_control_check",
               "scratch_promote", "scratch_retention_copy"]
    record: dict[str, Any] = {"cleaned_at_utc": _utc_now_iso(), "trees": {}}
    for name in targets:
        tree = canary_run_dir / name
        if tree.exists():
            record["trees"][name] = measure_tree(tree)
    cleanup_path = canary_run_dir / "cleanup.json"
    cleanup_path.write_text(json.dumps(record, indent=2) + "\n")
    for name in targets:
        tree = canary_run_dir / name
        if tree.exists():
            shutil.rmtree(tree)
            logger.info("Deleted %s", tree)
    logger.info("Cleanup record written: %s", cleanup_path)
    print(json.dumps(record, indent=2))
    return 0


# ── Selftest (no network, no prod paths) ─────────────────────────────
def run_selftest(canary_root: Path | None, verbose: bool) -> int:
    """Exercise write/measure/resume/stats/sampler/results plumbing against
    synthetic data. Local Gate A verification only."""
    base = canary_root or Path(tempfile.mkdtemp(prefix="member_spike_selftest_"))
    run_dir = base / "selftest_run"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    _setup_logging(run_dir / "spike.log", verbose)
    logger.info("Selftest starting in %s", run_dir)
    failures: list[str] = []

    def _check(name: str, condition: bool, detail: str = "") -> None:
        if condition:
            logger.info("selftest PASS: %s", name)
        else:
            failures.append(name)
            logger.error("selftest FAIL: %s %s", name, detail)

    # 1. Suffix normalization.
    _check("suffix m01", resolve_member_packing_var("tmp2m__m01") == "tmp2m__mean")
    _check("suffix m30", resolve_member_packing_var("tmp2m__m30") == "tmp2m__mean")
    _check("suffix control", resolve_member_packing_var("tmp2m__control") == "tmp2m__mean")
    _check("suffix passthrough", resolve_member_packing_var("tmp2m__mean") == "tmp2m__mean")
    _check("packing resolves", member_packing(MODEL, "tmp2m__m05") is _PACKING_BY_MODEL_VAR[(MODEL, MEAN_VAR)])

    # Synthetic grid: EPSG:3857 extent covering the model's full region bbox
    # (lon -178..-25, lat 5..82 for GEFS "na"), so the sampler spot-check's
    # region-derived interior/near-edge points land inside the grid and the
    # out-of-coverage point lands outside it — just like the prod grid.
    height, width = 120, 100
    bounds = (-19_820_000.0, 550_000.0, -2_780_000.0, 17_000_000.0)
    transform = from_bounds(*bounds, width, height)
    rng_values = (
        np.linspace(0.0, 80.0, height * width, dtype=np.float32).reshape(height, width)
    )
    rng_values[:3, :] = np.nan  # a little nodata fringe

    members = ["m01", "m02", "control"]
    fhs = [0, 6, 12]
    slim_root = run_dir / "slim"

    # 2. Slim writes with production-schema meta.
    for i, member in enumerate(members):
        for fh in fhs:
            meta = write_slim_member_frame(
                run_root=slim_root, model=MODEL, var_id=member_var_id(member),
                fh=fh, values=rng_values + i, transform=transform,
            )
            _check(
                f"meta schema {member} fh{fh}",
                all(k in meta for k in ("format_version", "width", "height",
                                        "bbox", "transform", "projection", "file", "fh", "level")),
            )
    slim_measured = measure_tree(slim_root)
    _check("slim file count", slim_measured["file_count"] == len(members) * len(fhs) * 2,
           f"got {slim_measured['file_count']}")
    _check("slim frame bytes", slim_measured["by_suffix"][".u16.bin"]["bytes"]
           == len(members) * len(fhs) * height * width * 2)

    # 3. Resume detection.
    _check("resume complete", slim_frame_is_complete(slim_root, MODEL, "tmp2m__m01", 0))
    _check("resume missing", not slim_frame_is_complete(slim_root, MODEL, "tmp2m__m09", 0))
    truncated = grid_frame_path_for_run_root(slim_root, "tmp2m__m01", 6, dtype="uint16")
    payload = truncated.read_bytes()
    truncated.write_bytes(payload[:100])
    _check("resume size-insane", not slim_frame_is_complete(slim_root, MODEL, "tmp2m__m01", 6))
    truncated.write_bytes(payload)
    _check("resume restored", slim_frame_is_complete(slim_root, MODEL, "tmp2m__m01", 6))

    # 4. Pre-encode gate: good array passes, all-NaN array fails (not written).
    plugin = MODEL_REGISTRY.get(MODEL)
    var_spec = _resolve_model_var_spec(MODEL, VAR_BASE, plugin)
    capability = _resolve_model_var_capability(MODEL, VAR_BASE, plugin)
    colormap_spec = get_color_map_spec(str(getattr(capability, "color_map_id", "")))
    _check("gate passes good array", check_pre_encode_value_sanity(
        rng_values, colormap_spec, var_spec_model=var_spec,
        var_capability=capability, label="selftest good"))
    _check("gate rejects all-NaN", not check_pre_encode_value_sanity(
        np.full((height, width), np.nan, dtype=np.float32), colormap_spec,
        var_spec_model=var_spec, var_capability=capability, label="selftest bad"))

    # 5. Encode/decode round trip within packing scale.
    packing = member_packing(MODEL, "tmp2m__m01")
    encoded = _encode_values(rng_values, scale=float(packing["scale"]),
                             offset=float(packing["offset"]),
                             nodata=int(packing["nodata"]),
                             dtype=grid_dtype(str(packing.get("dtype") or "")))
    decoded = _decode_values(encoded, model=MODEL, var=MEAN_VAR)
    finite = np.isfinite(rng_values)
    _check("encode/decode round trip",
           bool(np.all(np.abs(decoded[finite] - rng_values[finite]) <= float(packing["scale"]) / 2 + 1e-4)))
    _check("nan survives as nodata", bool(np.all(np.isnan(decoded[~finite]))))

    # 6. Comparison sets (no fetch — derived from slim bins).
    guards = Guards(data_root=run_dir, canary_root=run_dir,
                    disk_floor_bytes=0, rss_limit_bytes=1 << 62)
    ctx_stub = SpikeContext(
        plugin=plugin, region="na", run_id="selftest", run_date=datetime(2026, 1, 1),
        capability=capability, var_spec=var_spec, colormap_spec=colormap_spec,
        resampling="bilinear", search_patterns=[":TMP:"], guards=guards,
        fetch_stats=FetchStats(), stop_event=threading.Event(),
    )
    comparison = run_comparison_sets(ctx_stub, slim_root, run_dir, ["m01", "m02"], [0, 6])
    _check("comparison processed", comparison["frames_processed"] == 4)
    _check("comparison gz+br present",
           comparison["full_profile_subset"]["by_suffix"].get(".bin.gz", {}).get("files") == 4
           and comparison["full_profile_subset"]["by_suffix"].get(".bin.br", {}).get("files") == 4)
    up_bytes = comparison["upscaled_3x_subset"]["by_suffix"][".u16.bin"]["bytes"]
    _check("3x is 9x pixels", up_bytes == 4 * (height * 3) * (width * 3) * 2,
           f"got {up_bytes}")

    # 7. Promote + retention sim.
    promote = run_promote_retention_sim(run_dir, slim_root, guards)
    _check("promote sim ran", promote["retention_sweep_files_deleted"] == slim_measured["file_count"])
    _check("slim tree back in place", slim_root.is_dir()
           and measure_tree(slim_root)["file_count"] == slim_measured["file_count"])

    # 8. Stats prototype with completeness gate.
    stats = run_stats_prototype(slim_root, members, fhs, threshold=32.0,
                                expected_member_count=len(members))
    _check("stats ran", not stats.get("skipped") and stats.get("member_count") == 3, str(stats))
    incomplete = run_stats_prototype(slim_root, members + ["m09"], fhs, threshold=32.0,
                                     expected_member_count=4)
    _check("stats completeness gate blocks", bool(incomplete.get("skipped")), str(incomplete))

    # 9. Sampler spot check (interior / near-edge / out-of-coverage).
    sampler = sampler_spot_check(slim_root, members, fhs)
    _check("sampler all passed", bool(sampler.get("all_passed")), json.dumps(sampler, indent=2))

    # 10. results.json plumbing: all seven measurement sections present.
    results = {
        "mode": "selftest",
        "environment": _environment_report(),
        "measurements": {
            "1_bytes": {"slim": slim_measured, "comparison": comparison},
            "2_wall_time": {"batch_wall_s": 0.0, "note": "selftest"},
            "3_rss": {"peak_rss_bytes": _peak_rss_bytes()},
            "4_fetch": FetchStats().asdict(),
            "5_promote_retention": promote,
            "6_eps_snowfall": {"skipped": True, "reason": "selftest (no network)"},
            "7_stats_pass": stats,
        },
        "sampler_check": sampler,
    }
    results_path = write_results(run_dir, results)
    reloaded = json.loads(results_path.read_text())
    _check("results.json sections", all(
        key in reloaded["measurements"]
        for key in ["1_bytes", "2_wall_time", "3_rss", "4_fetch",
                    "5_promote_retention", "6_eps_snowfall", "7_stats_pass"]))

    if failures:
        logger.error("Selftest FAILED: %d check(s): %s", len(failures), ", ".join(failures))
        print(f"SELFTEST FAILED ({len(failures)} checks): {', '.join(failures)}")
        return 1
    logger.info("Selftest passed (artifacts in %s)", run_dir)
    print(f"SELFTEST PASSED — artifacts in {run_dir}")
    return 0


# ── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ensemble member sizing spike (Phase 1, GEFS tmp2m, binary artifacts).",
        parents=[_early_parser],
    )
    parser.add_argument("--canary-root", default="/opt/cartosky/canary/gefs_members",
                        help="Canary output root (default: /opt/cartosky/canary/gefs_members)")
    parser.add_argument("--run", default=None,
                        help="Target run id override (default: newest retained run with full mean coverage)")
    parser.add_argument("--members", default="all",
                        help="Comma-separated members (m01..m30, control) or 'all'")
    parser.add_argument("--fhs", default="all",
                        help="Comma-separated forecast hours or 'all' (must be in the cycle's schedule)")
    parser.add_argument("--parallel", nargs="?", type=int, const=2, default=1,
                        help="Fetch parallelism (sequential by default; bare flag = 2; max 4)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip (member, fh) frames whose slim .bin + meta already exist and are size-sane")
    parser.add_argument("--disk-floor-gb", type=float, default=DEFAULT_DISK_FLOOR_GB,
                        help="Abort if free disk drops below this (default: 100)")
    parser.add_argument("--rss-limit-gb", type=float, default=DEFAULT_RSS_LIMIT_GB,
                        help="Abort if script RSS exceeds this (default: 3)")
    parser.add_argument("--stats-threshold", type=float, default=DEFAULT_STATS_THRESHOLD,
                        help="P(value > threshold) threshold for the stats prototype (default: 32.0)")
    parser.add_argument("--skip-comparison-sets", action="store_true")
    parser.add_argument("--skip-promote-sim", action="store_true")
    parser.add_argument("--skip-eps-checks", action="store_true")
    parser.add_argument("--cleanup", action="store_true",
                        help="Record member-tree sizes to cleanup.json then delete them (requires --run)")
    parser.add_argument("--selftest", action="store_true",
                        help="Run against synthetic data only (no network, no prod paths)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        canary_override = Path(args.canary_root) if args.canary_root != "/opt/cartosky/canary/gefs_members" else None
        return run_selftest(canary_override, args.verbose)

    data_root = Path(args.data_root).expanduser().resolve()
    published_root = data_root / "published"
    canary_root = Path(args.canary_root).expanduser()

    if args.cleanup:
        if not args.run:
            print("--cleanup requires --run <run_id>", file=sys.stderr)
            return 1
        _setup_logging(None, args.verbose)
        return run_cleanup(canary_root / args.run)

    if not published_root.is_dir():
        print(f"Published root not found: {published_root}", file=sys.stderr)
        return 1
    parallel = max(1, min(4, int(args.parallel)))

    # Target-run selection (before logging file exists — run id names the dir).
    _setup_logging(None, args.verbose)
    try:
        run_id, scheduled_fhs = select_target_run(published_root, requested_run=args.run)
    except (RuntimeError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 2

    canary_run_dir = canary_root / run_id
    canary_run_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(canary_run_dir / "spike.log", args.verbose)
    logger.info("Ensemble member sizing spike starting: run=%s canary_dir=%s", run_id, canary_run_dir)

    try:
        members = _parse_members(args.members)
        fhs = _parse_fhs(args.fhs, scheduled_fhs)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    guards = Guards(
        data_root=data_root,
        canary_root=canary_run_dir,
        disk_floor_bytes=int(args.disk_floor_gb * 1e9),
        rss_limit_bytes=int(args.rss_limit_gb * 1e9),
    )
    stop_event = threading.Event()
    interrupted = {"flag": False}

    def _on_sigint(signum: int, frame: Any) -> None:  # noqa: ARG001
        if interrupted["flag"]:
            logger.warning("Second SIGINT — exiting immediately")
            raise SystemExit(130)
        interrupted["flag"] = True
        stop_event.set()
        logger.warning("SIGINT received — finishing current frame, then writing partial results")

    signal.signal(signal.SIGINT, _on_sigint)

    rss_tracker = RssTracker()
    rss_tracker.start()

    slim_root = canary_run_dir / "slim"
    started_utc = _utc_now_iso()
    started = time.perf_counter()
    exit_code = 0
    abort_reason: str | None = None

    results: dict[str, Any] = {
        "mode": "spike",
        "model": MODEL,
        "var": VAR_BASE,
        "mean_var": MEAN_VAR,
        "run": run_id,
        "members_requested": members,
        "fhs_requested": fhs,
        "parallel": parallel,
        "resume": bool(args.resume),
        "environment": _environment_report(),
        "measurements": {},
    }

    ctx = build_spike_context(run_id, guards, stop_event)
    main_summary: dict[str, Any] = {}
    try:
        guards.check()
        main_summary = run_main_pass(
            ctx, slim_root, members, fhs, parallel=parallel, resume=args.resume,
        )
        if main_summary.get("aborted"):
            abort_reason = str(main_summary["aborted"])
            logger.error("ABORTED: %s", abort_reason)
            exit_code = 3
    except SpikeAbort as exc:
        abort_reason = exc.reason
        logger.error("ABORTED: %s", exc.reason)
        exit_code = 3

    if interrupted["flag"] and exit_code == 0:
        exit_code = 130

    # Post-pass measurements run only on a normal (non-aborted) pass; on an
    # abort or interrupt we still write partial results below.
    comparison: dict[str, Any] = {"skipped": True}
    promote: dict[str, Any] = {"skipped": True}
    stats_pass: dict[str, Any] = {"skipped": True}
    sampler: dict[str, Any] = {"skipped": True}
    eps_control: dict[str, Any] = {"skipped": True}
    eps_snowfall: dict[str, Any] = {"skipped": True}

    if exit_code == 0:
        try:
            if not args.skip_comparison_sets:
                comparison = run_comparison_sets(
                    ctx, slim_root, canary_run_dir,
                    [m for m in COMPARISON_MEMBERS if m in members],
                    [fh for fh in COMPARISON_FHS if fh in fhs],
                )
            if not args.skip_promote_sim:
                promote = run_promote_retention_sim(canary_run_dir, slim_root, guards)
            stats_pass = run_stats_prototype(
                slim_root, members, fhs,
                threshold=args.stats_threshold,
                expected_member_count=len(members),
            )
            sampler = sampler_spot_check(slim_root, members, fhs)
            if not args.skip_eps_checks:
                try:
                    eps_control = eps_control_minicheck(published_root, canary_run_dir)
                except Exception as exc:  # noqa: BLE001 — recorded, non-fatal
                    eps_control = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                try:
                    eps_snowfall = eps_snowfall_inventory_check(published_root)
                except Exception as exc:  # noqa: BLE001 — recorded, non-fatal
                    eps_snowfall = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        except SpikeAbort as exc:
            abort_reason = exc.reason
            logger.error("ABORTED during post-pass measurements: %s", exc.reason)
            exit_code = 3

    rss_tracker.stop()
    total_wall_s = time.perf_counter() - started
    ended_utc = _utc_now_iso()
    peak_rss = rss_tracker.peak_bytes()
    gefs_memory_high = 3 * 1024**3
    gefs_memory_max = 3500 * 1024**2

    results["measurements"] = {
        "1_bytes": {
            "slim_tree": measure_tree(slim_root),
            "comparison_sets": comparison,
        },
        "2_wall_time": {
            "spike_window_utc": {"start": started_utc, "end": ended_utc},
            "total_wall_s": round(total_wall_s, 2),
            "member_batch": main_summary,
            "scheduler_delay_note": (
                "cross-check GEFS mean publish latency across spike_window_utc "
                "against recent norm via scheduler logs (see runbook)"
            ),
        },
        "3_rss": {
            "peak_rss_bytes": peak_rss,
            "peak_rss_mib": round(peak_rss / (1024**2), 1) if peak_rss else None,
            "sampled_max_bytes": rss_tracker.max_sampled_bytes or None,
            "gefs_caps": {"MemoryHigh_bytes": gefs_memory_high, "MemoryMax_bytes": gefs_memory_max},
            "predicted_delta_vs_MemoryHigh_mib": (
                round((gefs_memory_high - peak_rss) / (1024**2), 1) if peak_rss else None
            ),
            "predicted_delta_vs_MemoryMax_mib": (
                round((gefs_memory_max - peak_rss) / (1024**2), 1) if peak_rss else None
            ),
            "cgroup_throttle_note": (
                "if run under systemd-run --scope with the GEFS caps, record "
                "memory.events 'high' counter per the runbook"
            ),
        },
        "4_fetch": {
            **ctx.fetch_stats.asdict(),
            "members_with_written_frames": len(main_summary.get("per_member_frame_times", {})),
            "control_member_kwarg": repr(ctx.control_kwarg_resolved),
            "member_kwarg_scheme": "int 1..30 -> gepNN; control candidates tried in order: "
                                   + ", ".join(repr(c) for c in CONTROL_MEMBER_KWARG_CANDIDATES),
            "eps_control_minicheck": eps_control,
        },
        "5_promote_retention": promote,
        "6_eps_snowfall": eps_snowfall,
        "7_stats_pass": stats_pass,
    }
    results["sampler_check"] = sampler
    results["aborted"] = abort_reason
    results["interrupted"] = interrupted["flag"]
    results["partial"] = exit_code != 0
    write_results(canary_run_dir, results)

    slim_measured = results["measurements"]["1_bytes"]["slim_tree"]
    print(
        "\n=== ensemble member sizing spike summary ===\n"
        f"run: {run_id}   members: {len(members)}   fhs: {len(fhs)}\n"
        f"frames: {main_summary.get('frames_by_status', {})}\n"
        f"slim tree: {slim_measured['total_mib']} MiB in {slim_measured['file_count']} files "
        f"({slim_measured['bytes_per_frame']} B/frame)\n"
        f"wall: {total_wall_s:.0f}s   peak RSS: "
        f"{(peak_rss or 0) / (1024**2):.0f} MiB\n"
        f"fetch: {ctx.fetch_stats.requests_succeeded}/{ctx.fetch_stats.requests_attempted} ok, "
        f"{ctx.fetch_stats.retries} retries, {len(ctx.fetch_stats.failures)} failures\n"
        f"status: {'ABORTED: ' + abort_reason if abort_reason else ('INTERRUPTED (partial)' if interrupted['flag'] else 'complete')}\n"
        f"results: {canary_run_dir / 'results.json'}\n"
        f"log: {canary_run_dir / 'spike.log'}\n"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
