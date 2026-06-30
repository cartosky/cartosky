"""COG point-sampling helpers.

Extracted from ``app.main`` (Phase 1A of the Model Guidance plan) so that the
meteogram service (``app.services.forecast_page.get_forecast_meteogram``) can
sample published artifacts directly, without HTTP round-trips and without
importing ``app.main`` at module load time (which would be circular, since
``app.main`` imports ``forecast_page``).

The genuinely self-contained COG readers live here in full. Run / manifest /
runtime-var resolution remains the responsibility of ``app.main`` (it is tied
to the capabilities + run-discovery machinery); the two ``_resolve_*`` helpers
reach back into ``app.main`` via a lazy import at call time. ``app.main``
re-imports the names defined here so existing call sites and tests are
unchanged.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine
from rasterio.windows import Window

from .grid import (
    GRID_DTYPE,
    GRID_DTYPE_UINT8,
    GRID_FRAME_FORMAT_VERSION,
    _decode_values,
    _packing_config,
    expected_grid_frame_size_bytes,
    grid_dtype,
    resolved_grid_frame_meta_path_for_run_root,
)
from .run_ids import parse_run_id_datetime

logger = logging.getLogger(__name__)

# When a manifest variable carries no `expected_frames` completion marker and the
# plugin can't supply a scheduled count, treat a run as usable only once it has
# published more than a trivial handful of frames (avoids picking a run that has
# just the first few hours). Real manifests always carry `expected_frames`, so
# this fallback is rarely exercised.
_MIN_USABLE_FRAMES_FALLBACK = 6

# ── Open dataset cache ────────────────────────────────────────────────────
_ds_cache: dict[str, rasterio.DatasetReader] = {}
_ds_cache_lock = threading.Lock()
_DS_CACHE_MAX = 16


def _get_cached_dataset(path: Path) -> rasterio.DatasetReader:
    key = str(path)
    with _ds_cache_lock:
        ds = _ds_cache.get(key)
        if ds is not None and not ds.closed:
            return ds
        if len(_ds_cache) >= _DS_CACHE_MAX:
            evict_key = next(iter(_ds_cache))
            try:
                _ds_cache.pop(evict_key).close()
            except Exception:
                _ds_cache.pop(evict_key, None)
        ds = rasterio.open(path)
        _ds_cache[key] = ds
        return ds


# ── Point sampling primitives ─────────────────────────────────────────────
@lru_cache(maxsize=16)
def _sample_transformer(dst_crs: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)


def _sample_dataset_xy(ds: rasterio.DatasetReader, *, lon: float, lat: float) -> tuple[float, float]:
    ds_crs = ds.crs
    if ds_crs is None:
        raise ValueError(f"Sample dataset missing CRS: {ds.name}")
    dst_crs = ds_crs.to_string()
    if dst_crs == "EPSG:4326":
        return float(lon), float(lat)
    return _sample_transformer(dst_crs).transform(lon, lat)


def _sample_dataset_index(ds: rasterio.DatasetReader, *, lon: float, lat: float) -> tuple[int, int]:
    x, y = _sample_dataset_xy(ds, lon=lon, lat=lat)
    row, col = ds.index(x, y)
    return row, col


def _read_sample_value(
    ds: rasterio.DatasetReader,
    *,
    row: int,
    col: int,
    masked: bool,
) -> tuple[float | None, bool]:
    if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
        return None, True

    window = Window(col, row, 1, 1)  # type: ignore[call-arg]
    pixel = ds.read(1, window=window, masked=masked)
    raw_value = pixel[0, 0]
    if np.ma.is_masked(raw_value):
        return None, True

    value = float(raw_value)
    if np.isnan(value):
        return None, True
    return value, False


def _sample_batch_values(
    ds: rasterio.DatasetReader,
    *,
    points: list[Any],
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for point in points:
        row, col = _sample_dataset_index(ds, lon=point.lon, lat=point.lat)
        value, no_data = _read_sample_value(ds, row=row, col=col, masked=True)
        values[point.id] = None if no_data or value is None else round(float(value), 1)
    return values


# ── Binary point sampling primitives ──────────────────────────────────────
def _binary_encoded_dtype(model: str, var: str) -> tuple[str, np.dtype[Any]]:
    packing = _packing_config(model, var)
    if packing is None:
        raise ValueError(f"Unsupported grid pack target: {model}/{var}")
    resolved_dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
    encoded_dtype: np.dtype[Any] = np.dtype(np.uint8 if resolved_dtype == GRID_DTYPE_UINT8 else "<u2")
    return resolved_dtype, encoded_dtype


def _load_binary_frame_meta(meta_path: Path) -> dict[str, Any]:
    try:
        meta = json.loads(Path(meta_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable grid frame metadata: {meta_path}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"Invalid grid frame metadata payload: {meta_path}")

    format_version = meta.get("format_version")
    try:
        format_version_int = int(format_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Unsupported grid frame format_version {format_version!r}: {meta_path}"
        ) from exc
    if format_version_int != GRID_FRAME_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported grid frame format_version {format_version!r}: {meta_path}"
        )

    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    transform = meta.get("transform")
    projection = str(meta.get("projection") or "").strip()
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid grid frame dimensions in metadata: {meta_path}")
    if not isinstance(transform, list) or len(transform) != 6:
        raise ValueError(f"Grid frame metadata missing affine transform: {meta_path}")
    if not projection:
        raise ValueError(f"Grid frame metadata missing projection: {meta_path}")

    return meta


def _sample_binary_frame_index(meta: dict[str, Any], *, lon: float, lat: float) -> tuple[int, int]:
    projection = str(meta.get("projection") or "").strip()
    if projection.upper() == "EPSG:4326":
        x, y = float(lon), float(lat)
    else:
        x, y = _sample_transformer(projection).transform(lon, lat)

    transform_values = [float(value) for value in meta["transform"]]
    col_f, row_f = ~Affine(*transform_values) * (x, y)
    return int(np.floor(row_f)), int(np.floor(col_f))


def _read_binary_frame_values(frame_path: Path, meta: dict[str, Any], *, model: str, var: str) -> np.ndarray:
    width = int(meta["width"])
    height = int(meta["height"])
    resolved_dtype, encoded_dtype = _binary_encoded_dtype(model, var)
    expected_size = expected_grid_frame_size_bytes(width=width, height=height, dtype=resolved_dtype)
    payload = Path(frame_path).read_bytes()
    if len(payload) != expected_size:
        raise ValueError(
            f"Grid frame byte size mismatch: {frame_path} "
            f"actual={len(payload)} expected={expected_size}"
        )
    encoded = np.frombuffer(payload, dtype=encoded_dtype).reshape(height, width)
    return _decode_values(encoded, model=model, var=var)


def read_binary_sample_value(
    frame_path: Path,
    meta_path: Path,
    *,
    model: str,
    var: str,
    lat: float,
    lon: float,
) -> tuple[float | None, bool]:
    """Sample one point from an already-resolved grid binary frame.

    Returns ``(value, no_data)`` where ``no_data`` is true for out-of-bounds,
    nodata, or NaN pixels. This intentionally reads the whole frame with a plain
    file read; Phase D benchmarking will decide whether a cache/mmap strategy is
    warranted.
    """
    meta = _load_binary_frame_meta(meta_path)
    row, col = _sample_binary_frame_index(meta, lon=lon, lat=lat)
    height = int(meta["height"])
    width = int(meta["width"])
    if row < 0 or row >= height or col < 0 or col >= width:
        return None, True

    values = _read_binary_frame_values(frame_path, meta, model=model, var=var)
    value = float(values[row, col])
    if np.isnan(value):
        return None, True
    return value, False


def sample_binary_point_value(
    frame_path: Path,
    meta_path: Path,
    *,
    model: str,
    var: str,
    lat: float,
    lon: float,
) -> float | None:
    """Sample a point from a grid binary frame, rounded like the COG helpers."""
    value, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model=model,
        var=var,
        lat=lat,
        lon=lon,
    )
    if no_data or value is None:
        return None
    return round(float(value), 1)


# ── Artifact resolution ───────────────────────────────────────────────────
# These delegate run / runtime-var / path resolution to ``app.main`` (lazy
# import to avoid a load-time cycle). The published value COG already stores
# display units (conversion happens at build time), so callers get the same
# values served by ``/api/v4/sample``.
def _resolve_val_cog(
    model: str,
    run: str,
    var: str,
    fh: int,
    *,
    ensemble_view: str | None = None,
    region: str | None = None,
) -> Path | None:
    del region
    from .. import main as _main

    resolved = _main._resolve_run(model, run) or run
    runtime_var = _main._runtime_var_id_for_request(model, var, ensemble_view)
    candidate = _main._published_var_dir(model, resolved, runtime_var) / f"fh{fh:03d}.val.cog.tif"
    if candidate.is_file():
        return candidate
    return None


def _resolve_sidecar(
    model: str,
    run: str,
    var: str,
    fh: int,
    *,
    ensemble_view: str | None = None,
    region: str | None = None,
) -> dict | None:
    del region
    from .. import main as _main

    resolved = _main._resolve_run(model, run) or run
    runtime_var = _main._runtime_var_id_for_request(model, var, ensemble_view)
    candidate = _main._published_var_dir(model, resolved, runtime_var) / f"fh{fh:03d}.json"
    if candidate.is_file():
        return _main._load_json_cached(candidate, _main._sidecar_cache)
    return None


def _resolve_binary_grid_frame(
    model: str,
    run: str,
    var: str,
    fh: int,
    *,
    ensemble_view: str | None = None,
    region: str | None = None,
) -> tuple[Path, Path] | None:
    del region
    from .. import main as _main

    resolved = _main._resolve_run(model, run) or run
    runtime_var = _main._runtime_var_id_for_request(model, var, ensemble_view)
    var_dir = _main._published_var_dir(model, resolved, runtime_var)
    meta_path = resolved_grid_frame_meta_path_for_run_root(var_dir.parent, runtime_var, fh)
    if not meta_path.is_file():
        return None
    try:
        meta = _load_binary_frame_meta(meta_path)
    except ValueError:
        logger.exception("Grid frame metadata resolution failed: %s/%s/%s/fh%03d", model, run, var, fh)
        return None
    filename = Path(str(meta.get("file") or "")).name
    if not filename:
        return None
    frame_path = meta_path.parent / filename
    if frame_path.is_file():
        return frame_path, meta_path
    return None


# ── Meteogram-facing helpers ──────────────────────────────────────────────
# Higher-level helpers used by ``get_forecast_meteogram`` so that the service
# layer never imports ``app.main`` directly.
def resolve_run(model: str, run: str, *, region: str | None = None) -> str | None:
    """Resolve a requested run (or ``"latest"``) to a concrete run id."""
    from .. import main as _main

    return _main._resolve_run(model, run, region=region)


def _scheduled_frame_count(plugin: Any, var: str, run_id: str) -> int | None:
    """Authoritative frame target for ``var`` in ``run_id`` from the plugin."""
    if plugin is None or not hasattr(plugin, "scheduled_fhs_for_var"):
        return None
    run_dt = parse_run_id_datetime(run_id)
    if run_dt is None:
        return None
    try:
        fhs = plugin.scheduled_fhs_for_var(var, run_dt.hour)
    except Exception:
        return None
    return len(fhs) if fhs else None


def _variable_run_complete(plugin: Any, var_entry: dict[str, Any], var: str, run_id: str) -> bool:
    """Whether ``var`` is fully published in this run.

    Completion marker (preferred): manifest ``available_frames >= expected_frames``
    (same signal as ``main._manifest_run_complete``). Falls back to the plugin's
    scheduled frame count, then to a small minimum frame threshold.
    """
    from .. import main as _main

    available = _main._manifest_var_available_frames(var_entry)
    if available <= 0:
        return False

    expected_raw = var_entry.get("expected_frames")
    if isinstance(expected_raw, int) and expected_raw > 0:
        return available >= expected_raw

    scheduled = _scheduled_frame_count(plugin, var, run_id)
    if scheduled is not None and scheduled > 0:
        return available >= scheduled

    return available >= _MIN_USABLE_FRAMES_FALLBACK


def _run_manifest_complete(
    plugin: Any,
    variables_map: dict[str, Any],
    variables: list[str],
    run_id: str,
) -> bool:
    """Whether ``run_id``'s manifest is complete for the requested variables.

    Usable means at least one requested variable is present and complete, and no
    present variable is still building. A variable absent from the manifest is
    ignored (not disqualifying).
    """
    saw_complete = False
    for var in variables:
        canonical = var
        if plugin is not None and hasattr(plugin, "normalize_var_id"):
            try:
                canonical = plugin.normalize_var_id(var)
            except Exception:
                canonical = var
        entry = variables_map.get(canonical)
        if not isinstance(entry, dict):
            entry = variables_map.get(var)
        if not isinstance(entry, dict):
            continue  # variable absent in this run -> ignore (don't disqualify)
        if _variable_run_complete(plugin, entry, canonical, run_id):
            saw_complete = True
        else:
            return False  # present but still building -> not usable
    return saw_complete


def run_complete_for_variables(
    model: str,
    run_id: str,
    variables: list[str],
    *,
    region: str | None = None,
) -> bool:
    """Whether a specific ``run_id`` is complete/usable for the variables.

    Same completion semantics as :func:`resolve_latest_complete_run`'s per-run
    check, exposed for validating an explicitly pinned run before sampling it.
    """
    from .. import main as _main
    from ..models.registry import get_model

    manifest = _main._load_manifest(model, run_id, region=region)
    if not isinstance(manifest, dict):
        return False
    variables_map = manifest.get("variables")
    if not isinstance(variables_map, dict):
        return False
    try:
        plugin = get_model(model)
    except Exception:
        plugin = None
    return _run_manifest_complete(plugin, variables_map, variables, run_id)


def resolve_latest_complete_run(
    model: str,
    variables: list[str],
    *,
    region: str | None = None,
) -> str | None:
    """Newest published run that is *complete* for the requested variable(s).

    Fixes the building-run bug: ``latest_per_model`` must mean the latest
    complete usable run, not the latest discovered run (which may still be
    publishing frames). Scans runs newest-first and returns the first where every
    requested variable present in the manifest is complete and at least one
    requested variable is present and complete. Returns ``None`` when no run
    qualifies (caller maps that to ``unavailable``).
    """
    from .. import main as _main
    from ..models.registry import get_model

    try:
        candidates = _main._scan_manifest_runs(model, region=region)
    except Exception:
        logger.exception("Meteogram run scan failed for %s", model)
        return None
    if not candidates:
        return None

    try:
        plugin = get_model(model)
    except Exception:
        plugin = None

    for run_id in candidates:
        manifest = _main._load_manifest(model, run_id, region=region)
        if not isinstance(manifest, dict):
            continue
        variables_map = manifest.get("variables")
        if not isinstance(variables_map, dict):
            continue
        if _run_manifest_complete(plugin, variables_map, variables, run_id):
            return run_id

    return None


def manifest_frame_entries(
    model: str, run: str, var: str, *, region: str | None = None
) -> tuple[list[tuple[int, str | None]], str | None]:
    """Return ``([(fh, valid_time), ...], units)`` for ``var`` in ``run``.

    Reads the run manifest once (it is ``_load_json_cached``). The publish
    pipeline writes per-frame ``valid_time`` and the variable's ``units`` into
    the manifest, so the meteogram can source both here and skip a per-frame
    sidecar read. Frames are sorted and de-duplicated by fh.
    """
    from .. import main as _main
    from ..models.registry import get_model

    manifest = _main._load_manifest(model, run, region=region)
    if not isinstance(manifest, dict):
        return [], None
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return [], None

    canonical_var = var
    try:
        plugin = get_model(model)
        if hasattr(plugin, "normalize_var_id"):
            canonical_var = plugin.normalize_var_id(var)
    except Exception:
        canonical_var = var

    entry = variables.get(canonical_var)
    if not isinstance(entry, dict):
        entry = variables.get(var)
    if not isinstance(entry, dict):
        return [], None

    frames = entry.get("frames")
    if not isinstance(frames, list):
        return [], None

    by_fh: dict[int, str | None] = {}
    for item in frames:
        if not isinstance(item, dict) or not isinstance(item.get("fh"), int):
            continue
        vt = item.get("valid_time")
        by_fh[int(item["fh"])] = vt if isinstance(vt, str) and vt else None

    units_raw = entry.get("units")
    units = str(units_raw) if isinstance(units_raw, str) and units_raw else None
    return sorted(by_fh.items()), units


def manifest_frame_hours(model: str, run: str, var: str, *, region: str | None = None) -> list[int]:
    """Return the sorted forecast hours published for ``var`` in ``run``.

    Mirrors the frame source used by ``/api/v4/{model}/{run}/{var}/frames``:
    the manifest ``variables[<canonical_var>].frames[].fh`` list.
    """
    entries, _units = manifest_frame_entries(model, run, var, region=region)
    return [fh for fh, _vt in entries]


def sample_point_value(cog_path: Path, *, lat: float, lon: float) -> float | None:
    """Sample a single point from an already-resolved value COG.

    Returns the value rounded to 1 decimal (matching ``/api/v4/sample``), or
    ``None`` for out-of-bounds / nodata / NaN pixels.
    """
    ds = _get_cached_dataset(cog_path)
    row, col = _sample_dataset_index(ds, lon=lon, lat=lat)
    value, no_data = _read_sample_value(ds, row=row, col=col, masked=True)
    if no_data or value is None:
        return None
    return round(float(value), 1)


# Concurrency for the meteogram frame fan-out. Threads overlap COG opens when
# the workload is I/O-bound (cold page cache / remote storage); when the COGs
# are hot in page cache the opens are GIL-bound and threads don't help — see the
# note on ``sample_values_parallel`` for the process-pool escape hatch.
_METEOGRAM_SAMPLE_WORKERS = 16

# (model, run_id, var, fh) — one frame to sample.
SampleTask = tuple[str, str, str, int]


def sample_value(
    model: str,
    run_id: str,
    var: str,
    fh: int,
    *,
    lat: float,
    lon: float,
    region: str | None = None,
) -> tuple[bool, float | None]:
    """Sample one frame's value COG. Thread-safe: opens and closes its own
    dataset (not the shared ``_ds_cache``, whose LRU eviction closes handles
    other threads may still be reading).

    Returns ``(present, value)``: ``present`` is False when the value COG is
    absent (the caller omits the frame, matching the prior behavior); ``value``
    is None for nodata / out-of-bounds / read errors on a present COG. No sidecar
    is read here — ``valid_time`` and ``units`` come from the run manifest.
    """
    cog = _resolve_val_cog(model, run_id, var, fh, region=region)
    if cog is None:
        return (False, None)
    try:
        with rasterio.open(cog) as ds:
            row, col = _sample_dataset_index(ds, lon=lon, lat=lat)
            raw, no_data = _read_sample_value(ds, row=row, col=col, masked=True)
            return (True, None if (no_data or raw is None) else round(float(raw), 1))
    except Exception:
        logger.exception("Meteogram sample failed: %s/%s/%s/fh%03d", model, run_id, var, fh)
        return (True, None)


def sample_binary_value(
    model: str,
    run_id: str,
    var: str,
    fh: int,
    *,
    lat: float,
    lon: float,
    region: str | None = None,
) -> tuple[bool, float | None]:
    """Sample one frame's grid binary.

    Returns ``(present, value)`` with the same shape as :func:`sample_value` so
    canary/shadow comparisons can call both paths side by side without touching
    production route handlers.
    """
    resolved = _resolve_binary_grid_frame(model, run_id, var, fh, region=region)
    if resolved is None:
        return (False, None)
    frame_path, meta_path = resolved
    try:
        return (
            True,
            sample_binary_point_value(
                frame_path,
                meta_path,
                model=model,
                var=var,
                lat=lat,
                lon=lon,
            ),
        )
    except Exception:
        logger.exception("Binary sample failed: %s/%s/%s/fh%03d", model, run_id, var, fh)
        return (True, None)


def sample_values_parallel(
    tasks: list[SampleTask],
    *,
    lat: float,
    lon: float,
    region: str | None = None,
) -> list[tuple[bool, float | None]]:
    """Sample every frame in ``tasks`` (all models/variables for one request) in
    a single pool, returning ``(present, value)`` per task in input order.

    Uses a thread pool: effective only while the per-frame COG opens are
    I/O-bound. If profiling shows the opens are GIL-bound (hot page cache), swap
    ``ThreadPoolExecutor`` for ``ProcessPoolExecutor`` here — the worker
    (:func:`sample_value`) is already a top-level, picklable function with no
    shared state, so that is the only change required.
    """
    if not tasks:
        return []
    if len(tasks) == 1:
        m, r, v, fh = tasks[0]
        return [sample_value(m, r, v, fh, lat=lat, lon=lon, region=region)]

    workers = min(_METEOGRAM_SAMPLE_WORKERS, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(
                lambda t: sample_value(t[0], t[1], t[2], t[3], lat=lat, lon=lon, region=region),
                tasks,
            )
        )


def read_frame_valid_times(
    tasks: list[SampleTask], *, region: str | None = None
) -> list[str | None]:
    """Sidecar ``valid_time`` per task, in input order.

    Fallback for frames whose run manifest omits per-frame valid_time (the
    sidecar is the canonical source — same one ``/frames`` reads). Normally the
    manifest carries valid_time, so this is called with an empty/short list.
    """
    def _one(t: SampleTask) -> str | None:
        sidecar = _resolve_sidecar(t[0], t[1], t[2], t[3], region=region)
        vt = sidecar.get("valid_time") if isinstance(sidecar, dict) else None
        return vt if isinstance(vt, str) and vt else None

    if not tasks:
        return []
    if len(tasks) == 1:
        return [_one(tasks[0])]
    workers = min(_METEOGRAM_SAMPLE_WORKERS, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, tasks))
