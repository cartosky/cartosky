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
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer
from rasterio.transform import Affine

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

# ── Point sampling primitives ─────────────────────────────────────────────
@lru_cache(maxsize=16)
def _sample_transformer(dst_crs: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)


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


# Parsed frame-meta cache. Frame metas are written once (atomic tmp+rename,
# never edited in place) but re-read members × fhs times per meteogram
# member request (~10k parses for a GEFS+EPS include_members call) — parse
# once per publish instead. Entries are validated against mtime_ns at most
# every _FRAME_META_RECHECK_SECONDS (the main-module _load_json_cached
# pattern). Cached dicts are shared: callers treat frame meta as read-only.
_FRAME_META_CACHE: dict[str, dict[str, Any]] = {}
_FRAME_META_CACHE_LOCK = threading.Lock()
_FRAME_META_RECHECK_SECONDS = 15.0
_FRAME_META_CACHE_MAX_ENTRIES = 16384


def _load_binary_frame_meta_cached(meta_path: Path) -> dict[str, Any]:
    """``_load_binary_frame_meta`` behind an mtime-validated parse cache.

    Same contract (raises ``ValueError`` for missing/invalid metas — a
    vanished file also invalidates its entry).
    """
    key = str(meta_path)
    now = time.monotonic()

    with _FRAME_META_CACHE_LOCK:
        entry = _FRAME_META_CACHE.get(key)
        if entry is not None and now - float(entry["last_checked"]) < _FRAME_META_RECHECK_SECONDS:
            return entry["payload"]

    try:
        mtime_ns = int(Path(meta_path).stat().st_mtime_ns)
    except OSError as exc:
        with _FRAME_META_CACHE_LOCK:
            _FRAME_META_CACHE.pop(key, None)
        raise ValueError(f"Unreadable grid frame metadata: {meta_path}") from exc

    with _FRAME_META_CACHE_LOCK:
        entry = _FRAME_META_CACHE.get(key)
        if entry is not None and int(entry["mtime_ns"]) == mtime_ns:
            entry["last_checked"] = now
            return entry["payload"]

    meta = _load_binary_frame_meta(meta_path)
    with _FRAME_META_CACHE_LOCK:
        if len(_FRAME_META_CACHE) >= _FRAME_META_CACHE_MAX_ENTRIES:
            _FRAME_META_CACHE.clear()
        _FRAME_META_CACHE[key] = {
            "payload": meta,
            "mtime_ns": mtime_ns,
            "last_checked": now,
        }
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

    ``var`` must be the runtime variable id the frame was packed under (it
    selects the decode packing config) — callers with a requested/alias id
    resolve it first, e.g. via :func:`_resolve_binary_grid_frame`.
    """
    meta = _load_binary_frame_meta_cached(meta_path)
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
    """Sample a point from a grid binary frame, rounded like the COG helpers.

    ``var`` must be the runtime variable id the frame was packed under, as with
    :func:`read_binary_sample_value`.

    Reads through the seek primitive (result-identical to the full-frame read,
    pinned by equality tests): the full-frame decode was ~70ms/sample on
    MRMS's 1km CONUS grids, vs sub-millisecond for a one-pixel seek.
    """
    value, no_data = read_binary_sample_value_seek(
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


def read_binary_sample_value_seek(
    frame_path: Path,
    meta_path: Path,
    *,
    model: str,
    var: str,
    lat: float,
    lon: float,
) -> tuple[float | None, bool]:
    """Point sample via a single seek + itemsize-byte read of the frame file.

    Result-identical to :func:`read_binary_sample_value` (pinned by an
    equality test) but avoids the full-frame read + decode, which matters for
    the ensemble-member fan-out: ``include_members`` samples members × fhs
    frames per variable per request (~2,000 for GEFS), where full-frame
    decodes would dominate the meteogram latency budget. Decode still flows
    through :func:`_decode_values` on the one-element code array, preserving
    the migration plan's single decode authority.

    The file size is validated against the meta dims exactly like the
    full-read path, so a truncated frame raises rather than mis-addressing.
    """
    meta = _load_binary_frame_meta_cached(meta_path)
    row, col = _sample_binary_frame_index(meta, lon=lon, lat=lat)
    height = int(meta["height"])
    width = int(meta["width"])
    if row < 0 or row >= height or col < 0 or col >= width:
        return None, True

    resolved_dtype, encoded_dtype = _binary_encoded_dtype(model, var)
    itemsize = int(np.dtype(encoded_dtype).itemsize)
    expected_size = expected_grid_frame_size_bytes(width=width, height=height, dtype=resolved_dtype)
    actual_size = Path(frame_path).stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"Grid frame byte size mismatch: {frame_path} "
            f"actual={actual_size} expected={expected_size}"
        )

    offset = (row * width + col) * itemsize
    with open(frame_path, "rb") as handle:
        handle.seek(offset)
        payload = handle.read(itemsize)
    if len(payload) != itemsize:
        raise ValueError(f"Short read at offset {offset} in grid frame: {frame_path}")

    code = np.frombuffer(payload, dtype=encoded_dtype)
    value = float(_decode_values(code, model=model, var=var)[0])
    if np.isnan(value):
        return None, True
    return value, False


def sample_binary_value_seek(
    model: str,
    run_id: str,
    var: str,
    fh: int,
    *,
    lat: float,
    lon: float,
    domain: str | None = None,
) -> tuple[bool, float | None]:
    """Seek-read twin of :func:`sample_binary_value`: same ``(present, value)``
    shape and rounding, resolved through the same runtime-var path, but reads
    one pixel instead of the whole frame. Used by the meteogram member
    fan-out."""
    resolved = _resolve_binary_grid_frame(model, run_id, var, fh, domain=domain)
    if resolved is None:
        return (False, None)
    frame_path, meta_path, runtime_var = resolved
    try:
        value, no_data = read_binary_sample_value_seek(
            frame_path,
            meta_path,
            model=model,
            var=runtime_var,
            lat=lat,
            lon=lon,
        )
        return (True, None if (no_data or value is None) else round(float(value), 1))
    except Exception:
        logger.exception("Binary seek sample failed: %s/%s/%s/fh%03d", model, run_id, var, fh)
        return (True, None)


def sample_member_values_seek(
    model: str,
    run_id: str,
    member_vars: list[str],
    fhs: list[int],
    *,
    lat: float,
    lon: float,
    domain: str | None = None,
) -> dict[tuple[str, int], tuple[bool, float | None]]:
    """Batch seek-sampler for the meteogram member fan-out.

    Result-identical to calling :func:`sample_binary_value_seek` per
    ``(member_var, fh)`` — pinned by an equality test — but hoists the
    invariants out of the per-sample loop: run and runtime-var resolution
    happen once per member var (not once per sample), frame paths are built
    from the meta's own ``file`` field without re-stat'ing the meta path,
    and each member's pixel codes decode in ONE vectorized
    :func:`_decode_values` call. A GEFS+EPS ``include_members`` request is
    ~10k samples; the per-sample primitive spends most of its time
    re-resolving these invariants.
    """
    from .. import main as _main

    out: dict[tuple[str, int], tuple[bool, float | None]] = {}
    resolved_run = _main._resolve_run(model, run_id, domain=domain) or run_id

    for member_var in member_vars:
        try:
            runtime_var = _main._runtime_var_id_for_request(model, member_var, None)
            var_dir = _main._published_var_dir(model, resolved_run, runtime_var, domain=domain)
            run_root = var_dir.parent
            resolved_dtype, encoded_dtype = _binary_encoded_dtype(model, runtime_var)
        except Exception:
            logger.exception("Member batch resolution failed: %s/%s/%s", model, run_id, member_var)
            for fh in fhs:
                out[(member_var, fh)] = (False, None)
            continue
        itemsize = int(np.dtype(encoded_dtype).itemsize)

        codes: list[bytes] = []
        pending: list[int] = []  # fhs awaiting the batched decode
        for fh in fhs:
            meta_path = resolved_grid_frame_meta_path_for_run_root(run_root, runtime_var, fh)
            try:
                meta = _load_binary_frame_meta_cached(meta_path)
            except ValueError:
                out[(member_var, fh)] = (False, None)
                continue
            filename = Path(str(meta.get("file") or "")).name
            if not filename:
                out[(member_var, fh)] = (False, None)
                continue
            frame_path = meta_path.parent / filename
            try:
                height = int(meta["height"])
                width = int(meta["width"])
                row, col = _sample_binary_frame_index(meta, lon=lon, lat=lat)
                if row < 0 or row >= height or col < 0 or col >= width:
                    out[(member_var, fh)] = (True, None)
                    continue
                expected_size = expected_grid_frame_size_bytes(
                    width=width, height=height, dtype=resolved_dtype,
                )
                try:
                    actual_size = frame_path.stat().st_size
                except OSError:
                    # Frame file absent — same "not present" the per-sample
                    # resolver reports.
                    out[(member_var, fh)] = (False, None)
                    continue
                if actual_size != expected_size:
                    raise ValueError(
                        f"Grid frame byte size mismatch: {frame_path} "
                        f"actual={actual_size} expected={expected_size}"
                    )
                offset = (row * width + col) * itemsize
                with open(frame_path, "rb") as handle:
                    handle.seek(offset)
                    payload = handle.read(itemsize)
                if len(payload) != itemsize:
                    raise ValueError(
                        f"Short read at offset {offset} in grid frame: {frame_path}"
                    )
            except Exception:
                logger.exception(
                    "Binary seek sample failed: %s/%s/%s/fh%03d", model, run_id, member_var, fh,
                )
                out[(member_var, fh)] = (True, None)
                continue
            codes.append(payload)
            pending.append(fh)

        if pending:
            encoded = np.frombuffer(b"".join(codes), dtype=encoded_dtype)
            decoded = np.asarray(
                _decode_values(encoded, model=model, var=runtime_var), dtype=np.float64,
            ).ravel()
            for fh, value in zip(pending, decoded):
                out[(member_var, fh)] = (
                    True, None if not np.isfinite(value) else round(float(value), 1),
                )
    return out


def sample_binary_batch_values(
    frame_path: Path,
    meta_path: Path,
    *,
    model: str,
    var: str,
    points: list[Any],
) -> dict[str, float | None]:
    """Batch point sampling over one grid binary frame: one value per point
    id, rounded to 1 decimal, ``None`` for out-of-bounds / nodata / NaN
    pixels. ``var`` must be the runtime variable id the frame was encoded
    under, since it selects the decode packing config.

    Reads one pixel per point via seek instead of decoding the whole frame:
    N seeks beat one full-frame decode for any realistic N (the crossover to
    preferring a full read is in the thousands-of-points-on-one-frame range,
    not a real traffic pattern for these routes — MRMS's 1km grids made the
    full decode ~70ms while a seek is microseconds). Codes are decoded in one
    vectorized :func:`_decode_values` call, preserving the single decode
    authority, same as :func:`sample_member_values_seek`.
    """
    meta = _load_binary_frame_meta_cached(meta_path)
    height = int(meta["height"])
    width = int(meta["width"])
    resolved_dtype, encoded_dtype = _binary_encoded_dtype(model, var)
    itemsize = int(np.dtype(encoded_dtype).itemsize)
    expected_size = expected_grid_frame_size_bytes(width=width, height=height, dtype=resolved_dtype)
    actual_size = Path(frame_path).stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"Grid frame byte size mismatch: {frame_path} "
            f"actual={actual_size} expected={expected_size}"
        )

    out: dict[str, float | None] = {}
    pending_ids: list[str] = []
    codes: list[bytes] = []
    with open(frame_path, "rb") as handle:
        for point in points:
            row, col = _sample_binary_frame_index(meta, lon=point.lon, lat=point.lat)
            if row < 0 or row >= height or col < 0 or col >= width:
                out[point.id] = None
                continue
            offset = (row * width + col) * itemsize
            handle.seek(offset)
            payload = handle.read(itemsize)
            if len(payload) != itemsize:
                raise ValueError(f"Short read at offset {offset} in grid frame: {frame_path}")
            codes.append(payload)
            pending_ids.append(point.id)

    if pending_ids:
        encoded = np.frombuffer(b"".join(codes), dtype=encoded_dtype)
        decoded = np.asarray(_decode_values(encoded, model=model, var=var)).ravel()
        for point_id, value in zip(pending_ids, decoded):
            value_f = float(value)
            out[point_id] = None if np.isnan(value_f) else round(value_f, 1)
    return out


# ── Artifact resolution ───────────────────────────────────────────────────
# These delegate run / runtime-var / path resolution to ``app.main`` (lazy
# import to avoid a load-time cycle). The published value COG already stores
# display units (conversion happens at build time), so callers get the same
# values served by ``/api/v4/sample``.

def _resolve_sidecar(
    model: str,
    run: str,
    var: str,
    fh: int,
    *,
    ensemble_view: str | None = None,
    domain: str | None = None,
) -> dict | None:
    from .. import main as _main

    resolved = _main._resolve_run(model, run, domain=domain) or run
    runtime_var = _main._runtime_var_id_for_request(model, var, ensemble_view)
    candidate = _main._published_var_dir(model, resolved, runtime_var, domain=domain) / f"fh{fh:03d}.json"
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
    domain: str | None = None,
) -> tuple[Path, Path, str] | None:
    """Resolve a published grid binary frame to ``(frame_path, meta_path,
    runtime_var)``, or ``None`` when absent.

    ``runtime_var`` is the resolved runtime variable id the frame was published
    (and therefore packed) under — aliases normalized, default/requested
    ensemble view applied. It is returned so callers decode under the exact id
    that located the frame; deriving it independently at the decode site is how
    the path id and packing id can silently diverge.
    """
    from .. import main as _main

    resolved = _main._resolve_run(model, run, domain=domain) or run
    runtime_var = _main._runtime_var_id_for_request(model, var, ensemble_view)
    var_dir = _main._published_var_dir(model, resolved, runtime_var, domain=domain)
    meta_path = resolved_grid_frame_meta_path_for_run_root(var_dir.parent, runtime_var, fh)
    if not meta_path.is_file():
        return None
    try:
        meta = _load_binary_frame_meta_cached(meta_path)
    except ValueError:
        logger.exception("Grid frame metadata resolution failed: %s/%s/%s/fh%03d", model, run, var, fh)
        return None
    filename = Path(str(meta.get("file") or "")).name
    if not filename:
        return None
    frame_path = meta_path.parent / filename
    if frame_path.is_file():
        return frame_path, meta_path, runtime_var
    return None


# ── Meteogram-facing helpers ──────────────────────────────────────────────
# Higher-level helpers used by ``get_forecast_meteogram`` so that the service
# layer never imports ``app.main`` directly.
def resolve_run(model: str, run: str, *, domain: str | None = None) -> str | None:
    """Resolve a requested run (or ``"latest"``) to a concrete run id."""
    from .. import main as _main

    return _main._resolve_run(model, run, domain=domain)


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
    domain: str | None = None,
) -> bool:
    """Whether a specific ``run_id`` is complete/usable for the variables.

    Same completion semantics as :func:`resolve_latest_complete_run`'s per-run
    check, exposed for validating an explicitly pinned run before sampling it.
    """
    from .. import main as _main
    from ..models.registry import get_model

    manifest = _main._load_manifest(model, run_id, domain=domain)
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


def run_has_member_data(
    model: str, run_id: str, canonical_vars: list[str], *, domain: str | None = None,
) -> bool:
    """Does this run publish per-member frames for every listed canonical var?

    Cheap presence probe: the member pass builds each member var's grid
    manifest before its promote, so ``{var}__m01/grid/manifest.json`` in the
    published tree is the "members are ready" signal (m01 exists in every
    roster — GEFS and EPS alike).
    """
    from .. import main as _main

    for var in canonical_vars:
        var_dir = _main._published_var_dir(model, run_id, f"{var}__m01", domain=domain)
        if not (var_dir / "grid" / "manifest.json").is_file():
            return False
    return True


def resolve_latest_complete_run(
    model: str,
    variables: list[str],
    *,
    domain: str | None = None,
    member_data_vars: list[str] | None = None,
) -> str | None:
    """Newest published run that is *complete* for the requested variable(s).

    Fixes the building-run bug: ``latest_per_model`` must mean the latest
    complete usable run, not the latest discovered run (which may still be
    publishing frames). Scans runs newest-first and returns the first where every
    requested variable present in the manifest is complete and at least one
    requested variable is present and complete. Returns ``None`` when no run
    qualifies (caller maps that to ``unavailable``).

    ``member_data_vars`` additionally requires published member frames for
    those canonical vars (the plume view's "members-ready" preference — a
    fresh run's member pass lags its mean catchup, and a mean-only plume is
    worse than a one-cycle-older full fan). Callers fall back to a plain
    resolve when no run qualifies.
    """
    from .. import main as _main
    from ..models.registry import get_model

    try:
        candidates = _main._scan_manifest_runs(model, domain=domain)
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
        manifest = _main._load_manifest(model, run_id, domain=domain)
        if not isinstance(manifest, dict):
            continue
        variables_map = manifest.get("variables")
        if not isinstance(variables_map, dict):
            continue
        if not _run_manifest_complete(plugin, variables_map, variables, run_id):
            continue
        if member_data_vars and not run_has_member_data(
            model, run_id, member_data_vars, domain=domain,
        ):
            continue
        return run_id

    return None


def manifest_frame_entries(
    model: str, run: str, var: str, *, domain: str | None = None
) -> tuple[list[tuple[int, str | None]], str | None]:
    """Return ``([(fh, valid_time), ...], units)`` for ``var`` in ``run``.

    Reads the run manifest once (it is ``_load_json_cached``). The publish
    pipeline writes per-frame ``valid_time`` and the variable's ``units`` into
    the manifest, so the meteogram can source both here and skip a per-frame
    sidecar read. Frames are sorted and de-duplicated by fh.
    """
    from .. import main as _main
    from ..models.registry import get_model

    manifest = _main._load_manifest(model, run, domain=domain)
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


def manifest_frame_hours(model: str, run: str, var: str, *, domain: str | None = None) -> list[int]:
    """Return the sorted forecast hours published for ``var`` in ``run``.

    Mirrors the frame source used by ``/api/v4/{model}/{run}/{var}/frames``:
    the manifest ``variables[<canonical_var>].frames[].fh`` list.
    """
    entries, _units = manifest_frame_entries(model, run, var, domain=domain)
    return [fh for fh, _vt in entries]



# Concurrency for the meteogram sidecar fan-out.
_METEOGRAM_SAMPLE_WORKERS = 16

# (model, run_id, var, fh) — one frame to sample.
SampleTask = tuple[str, str, str, int]



def sample_binary_value(
    model: str,
    run_id: str,
    var: str,
    fh: int,
    *,
    lat: float,
    lon: float,
    domain: str | None = None,
) -> tuple[bool, float | None]:
    """Sample one frame's grid binary.

    Returns ``(present, value)``: ``present`` is False when the frame is
    absent (the caller omits it); ``value`` is None for nodata /
    out-of-bounds / read errors on a present frame.

    ``var`` may be any requestable id (canonical, alias, or ensemble-view
    default): the frame is resolved AND decoded under the runtime variable id
    returned by :func:`_resolve_binary_grid_frame`, so the packing config always
    matches the bytes on disk.
    """
    resolved = _resolve_binary_grid_frame(model, run_id, var, fh, domain=domain)
    if resolved is None:
        return (False, None)
    frame_path, meta_path, runtime_var = resolved
    try:
        return (
            True,
            sample_binary_point_value(
                frame_path,
                meta_path,
                model=model,
                var=runtime_var,
                lat=lat,
                lon=lon,
            ),
        )
    except Exception:
        logger.exception("Binary sample failed: %s/%s/%s/fh%03d", model, run_id, var, fh)
        return (True, None)



def read_frame_valid_times(
    tasks: list[SampleTask], *, domain: str | None = None
) -> list[str | None]:
    """Sidecar ``valid_time`` per task, in input order.

    Fallback for frames whose run manifest omits per-frame valid_time (the
    sidecar is the canonical source — same one ``/frames`` reads). Normally the
    manifest carries valid_time, so this is called with an empty/short list.
    """
    def _one(t: SampleTask) -> str | None:
        sidecar = _resolve_sidecar(t[0], t[1], t[2], t[3], domain=domain)
        vt = sidecar.get("valid_time") if isinstance(sidecar, dict) else None
        return vt if isinstance(vt, str) and vt else None

    if not tasks:
        return []
    if len(tasks) == 1:
        return [_one(tasks[0])]
    workers = min(_METEOGRAM_SAMPLE_WORKERS, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, tasks))
