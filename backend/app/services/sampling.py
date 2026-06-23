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

import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

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

        saw_complete = False
        disqualified = False
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
                disqualified = True  # present but still building -> not usable
                break

        if saw_complete and not disqualified:
            return run_id

    return None


def manifest_frame_hours(model: str, run: str, var: str, *, region: str | None = None) -> list[int]:
    """Return the sorted forecast hours published for ``var`` in ``run``.

    Mirrors the frame source used by ``/api/v4/{model}/{run}/{var}/frames``:
    the manifest ``variables[<canonical_var>].frames[].fh`` list.
    """
    from .. import main as _main
    from ..models.registry import get_model

    manifest = _main._load_manifest(model, run, region=region)
    if not isinstance(manifest, dict):
        return []
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return []

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
        return []

    frames = entry.get("frames")
    if not isinstance(frames, list):
        return []

    hours = {
        int(item["fh"])
        for item in frames
        if isinstance(item, dict) and isinstance(item.get("fh"), int)
    }
    return sorted(hours)


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
