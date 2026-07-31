"""Serving layer for sounding stacks (Skew-T design 2026-07-30, §5 / Phase 2).

``POST /api/v4/forecast/sounding`` answers with **every** published forecast
hour of one point's vertical profile in a single response: a sounding panel
scrubs time, so per-fh requests would waterfall.

The route handler in :mod:`app.main` stays thin — run resolution, lat/lon →
(row, col) mapping and frame assembly all live here, exactly as the meteogram
route delegates to :mod:`app.services.forecast_page`.

All packing/layout knowledge stays in :mod:`app.services.sounding`: this module
only calls its reader and path helpers, and is otherwise driven by the
per-fh sidecar JSON.

**Row-convention warning (design §5, cost us the Phase 1 prod gate):** the stack
affine is north-up (row 0 = north) while xarray/cfgrib GRIB decodes index
south-up. ``(row, col)`` here is derived strictly from the sidecar affine —
never from an xarray-style index.
"""

from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from pyproj import Transformer
from rasterio.transform import Affine

from ..config import sounding_models
from . import sounding as sounding_service
from . import sounding_indices
from . import sounding_thermo
from .run_ids import RUN_ID_RE

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088


class SoundingRequestError(Exception):
    """Request the caller can fix (out-of-domain point) — HTTP 400."""


class SoundingUnavailableError(Exception):
    """Nothing to serve: model not flagged, or no run has stacks — HTTP 404."""


class SoundingDataError(Exception):
    """Published stacks disagree with each other — HTTP 500 (a build bug)."""


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def sounding_enabled(model: str) -> bool:
    """Dark unless the model is BOTH flagged and pipeline-supported.

    Mirrors the scheduler's ``_sounding_enabled``: the flag is the kill switch,
    ``SUPPORTED_MODELS`` is the capability floor. Empty flag (the default)
    means the endpoint 404s everywhere.
    """
    model_norm = str(model).strip().lower()
    return model_norm in sounding_models() and model_norm in sounding_service.SUPPORTED_MODELS


# ---------------------------------------------------------------------------
# Run resolution
# ---------------------------------------------------------------------------


def _run_root(model: str, run_id: str) -> Path:
    # Lazy import: the house pattern for service -> main path helpers (see
    # sampling.resolve_run). It also keeps the roots monkeypatchable in tests.
    from .. import main as _main

    return _main._published_model_root(model) / run_id


def manifest_available_fhs(manifest: Any) -> list[int]:
    """``sounding.available_fhs`` from a run manifest, or ``[]``."""
    if not isinstance(manifest, dict):
        return []
    section = manifest.get("sounding")
    if not isinstance(section, dict):
        return []
    raw = section.get("available_fhs")
    if not isinstance(raw, list):
        return []
    fhs: list[int] = []
    for value in raw:
        try:
            fhs.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(fhs))


def resolve_sounding_run(model: str, requested_run: str | None) -> tuple[str, list[int]]:
    """Newest run that actually has sounding stacks, honouring a pin when usable.

    A pinned run that carries no sounding section (or no available fhs) **falls
    back** to the latest eligible run rather than 404-ing: the panel's run
    selector and the sounding retention window are not the same set, and a
    shared URL pinned to an aged-out run should still draw something. The
    response echoes both ids so the client can tell.

    ``requested_run`` is user input that becomes a **path segment** (manifest
    path here, run root in :func:`_run_root`), so it is validated against the
    house ``RUN_ID_RE`` before any filesystem use — same gate every other
    run-pinned route gets from ``main._resolve_run``. A malformed pin degrades
    exactly like an unknown one; it is deliberately not a new error path.
    """
    from .. import main as _main

    try:
        candidates = _main._scan_manifest_runs(model)
    except Exception:
        logger.exception("Sounding run scan failed for %s", model)
        candidates = []

    pinned = str(requested_run or "").strip()
    if pinned and pinned.lower() != "latest":
        if RUN_ID_RE.match(pinned) is None:
            logger.warning(
                "Sounding pinned run for %s is not a valid run id; ignoring pin: %r",
                model,
                pinned,
            )
        else:
            manifest = _main._load_manifest(model, pinned)
            fhs = manifest_available_fhs(manifest)
            if fhs:
                return pinned, fhs
            logger.info(
                "Sounding pinned run %s/%s has no published stacks; falling back to latest eligible",
                model,
                pinned,
            )

    for run_id in candidates:
        fhs = manifest_available_fhs(_main._load_manifest(model, run_id))
        if fhs:
            return run_id, fhs

    raise SoundingUnavailableError(f"no published soundings for model '{model}'")


# ---------------------------------------------------------------------------
# Coordinate mapping
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _sounding_transformer(projection: str) -> Transformer:
    """lat/lon -> stack projection. Keyed on the sidecar's WKT string.

    Same shape as ``sampling._sample_transformer``; a separate cache because
    the key here is a full WKT blob rather than an EPSG code.
    """
    return Transformer.from_crs("EPSG:4326", projection, always_xy=True)


def _project(lat: float, lon: float, projection: str) -> tuple[float, float]:
    if not projection or projection.strip().upper() == "EPSG:4326":
        return float(lon), float(lat)
    x, y = _sounding_transformer(projection).transform(float(lon), float(lat))
    return float(x), float(y)


def _unproject(x: float, y: float, projection: str) -> tuple[float, float]:
    if not projection or projection.strip().upper() == "EPSG:4326":
        return float(y), float(x)
    lon, lat = _sounding_transformer(projection).transform(float(x), float(y), direction="INVERSE")
    return float(lat), float(lon)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def locate_grid_point(sidecar: dict[str, Any], *, lat: float, lon: float) -> dict[str, Any]:
    """Nearest decimated grid point for a lat/lon (design decision #4).

    The affine maps *pixel corners*, so ``floor(~T * (x, y))`` is the containing
    cell and its sample sits at ``(col + 0.5, row + 0.5)``. Row 0 is NORTH: the
    affine is the only source of truth here.
    """
    projection = str(sidecar.get("projection") or "")
    transform_values = [float(value) for value in sidecar["transform"]]
    affine = Affine(*transform_values)

    x, y = _project(lat, lon, projection)
    # Points the projection cannot represent (e.g. the poles for HRRR's LCC)
    # come back as inf/NaN; treat them as outside the grid, not a 500.
    if not (math.isfinite(x) and math.isfinite(y)):
        raise SoundingRequestError(
            "point cannot be projected into the model grid"
        )
    col_f, row_f = ~affine * (x, y)
    row, col = int(math.floor(row_f)), int(math.floor(col_f))

    width = int(sidecar["width"])
    height = int(sidecar["height"])
    if not (0 <= row < height and 0 <= col < width):
        raise SoundingRequestError(
            f"point ({lat:.4f}, {lon:.4f}) is outside the sounding grid for this model"
        )

    center_x, center_y = affine * (col + 0.5, row + 0.5)
    grid_lat, grid_lon = _unproject(center_x, center_y, projection)
    return {
        "lat": round(grid_lat, 5),
        "lon": round(grid_lon, 5),
        "row": row,
        "col": col,
        "distance_km": round(haversine_km(lat, lon, grid_lat, grid_lon), 3),
    }


# ---------------------------------------------------------------------------
# Sidecar consistency
# ---------------------------------------------------------------------------


def _units_map(sidecar: dict[str, Any]) -> dict[str, str]:
    units: dict[str, str] = {}
    for entry in list(sidecar.get("variables") or []) + list(sidecar.get("surface_fields") or []):
        units[str(entry["id"])] = str(entry.get("units", ""))
    return units


def _shape_fingerprint(sidecar: dict[str, Any]) -> tuple:
    """Everything that must be identical across an fh series to answer once.

    Levels and units are stated once at the top of the response, and the
    (row, col) mapping is computed from a single sidecar — so a run whose
    stacks disagree on any of this cannot be served coherently.

    The **surface block is deliberately excluded**: a format bump that appends a
    surface field (v1 -> v2's ``cape_sfc``) can land mid-run when the scheduler
    restarts, and that run must still serve. The isobaric ladder is what the
    per-level arrays are aligned to, so it is the part that genuinely has to
    agree; a v1 frame inside a v2 run simply carries a shorter surface dict.
    """
    isobaric_units = sorted(
        (str(entry["id"]), str(entry.get("units", "")))
        for entry in list(sidecar.get("variables") or [])
    )
    return (
        [int(level) for level in sidecar["levels_hPa"]],
        isobaric_units,
        int(sidecar["width"]),
        int(sidecar["height"]),
        tuple(round(float(value), 9) for value in sidecar["transform"]),
        str(sidecar.get("projection") or ""),
    )


# ---------------------------------------------------------------------------
# Response cache (Phase 5 — the lever Phase 4 pre-staged)
# ---------------------------------------------------------------------------
#
# Phase 4 measured ~1.2-1.4 s of MetPy per 49-frame request and deliberately
# shipped no cache. Phase 5 adds three more per-level MetPy passes (wet-bulb is
# the expensive one — an LCL plus a moist-lapse integration per level), which
# roughly doubles that. A sounding panel is also *re-requested* far more than a
# meteogram: every map re-pick near the same grid cell, every share-link open,
# every reload of the same permalink.
#
# So: an in-process TTL cache of the fully-assembled payload, checked after the
# cheap work (run resolution + one sidecar read + the grid-point mapping) and
# before the ~49 stack reads and the MetPy pass.

#: Short enough that a newly published fh shows up quickly on the next request.
SOUNDING_CACHE_TTL_S = 300.0
#: ~128 × a 49-frame payload; bounded because this is process memory.
SOUNDING_CACHE_MAX_ENTRIES = 128

_cache_lock = Lock()
#: key -> (expires_at, payload). Insertion-ordered so the oldest evicts first.
_response_cache: "OrderedDict[tuple, tuple[float, dict[str, Any]]]" = OrderedDict()


def _now() -> float:
    """Monotonic clock, indirected so tests can move time without sleeping."""
    return time.monotonic()


def _cache_key(
    model: str, run_id: str, *, row: int, col: int, fhs: list[int]
) -> tuple:
    """Cache identity for one served payload.

    The forecast-hour set is part of the key on purpose. A run is built
    incrementally: a request that lands while only fh 0-5 exist would otherwise
    keep serving that short list for the whole TTL, even as fh 6+ publish. Both
    the count and the newest fh are included so growth at either end is a miss.
    """
    return (model, run_id, int(row), int(col), len(fhs), int(fhs[-1]) if fhs else -1)


def _cache_get(key: tuple) -> dict[str, Any] | None:
    now = _now()
    with _cache_lock:
        entry = _response_cache.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at <= now:
            _response_cache.pop(key, None)
            return None
        return payload


def _cache_put(key: tuple, payload: dict[str, Any]) -> None:
    now = _now()
    with _cache_lock:
        _response_cache.pop(key, None)
        _response_cache[key] = (now + SOUNDING_CACHE_TTL_S, payload)
        # Drop anything already expired before evicting live entries, so a burst
        # of stale keys cannot push out a fresh one.
        for stale_key in [k for k, (exp, _) in _response_cache.items() if exp <= now]:
            _response_cache.pop(stale_key, None)
        while len(_response_cache) > SOUNDING_CACHE_MAX_ENTRIES:
            _response_cache.popitem(last=False)


def clear_response_cache() -> None:
    """Drop every cached payload (tests; also a hook for a future admin path)."""
    with _cache_lock:
        _response_cache.clear()


def _with_request_fields(
    payload: dict[str, Any],
    *,
    run: str | None,
    lat: float,
    lon: float,
    grid_point: dict[str, Any],
) -> dict[str, Any]:
    """Shallow copy carrying every REQUEST-scoped field.

    The cache key is the grid cell, but three things belong to the caller, not
    the cell, and must never be served from cache (verification 2026-07-31
    caught ``location``/``distance_km`` leaking one caller's click to another):

    - ``requested_run`` — two callers can pin differently (``latest`` vs an
      aged-out id) and still resolve to the same run.
    - ``location`` — the caller's own lat/lon, echoed back.
    - ``grid_point`` — row/col/grid lat-lon are cell attributes, but
      ``distance_km`` is the haversine from THIS caller's click; the whole
      object is recomputed per request anyway (it is how the key is derived).

    The copy is shallow: nothing downstream mutates the payload, and
    deep-copying a 49-frame response on every hit would give back the time the
    cache saved.
    """
    result = dict(payload)
    result["location"] = {"lat": float(lat), "lon": float(lon)}
    result["grid_point"] = grid_point
    pinned = str(run or "").strip()
    if pinned and pinned.lower() != "latest":
        result["requested_run"] = pinned
    else:
        result.pop("requested_run", None)
    return result


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------


def _frame_for_fh(
    *,
    run_root: Path,
    fh: int,
    row: int,
    col: int,
    reference: dict[str, Any],
) -> dict[str, Any] | None:
    """One seek-read + decode, or ``None`` when this fh must be skipped.

    A single unreadable stack never fails the whole response: the manifest's
    ``available_fhs`` is a snapshot, and retention or an interrupted write can
    remove one file underneath us.
    """
    stack_path, _sidecar_path = sounding_service.stack_paths(run_root, fh)
    try:
        sidecar = sounding_service.read_sidecar(run_root, fh)
    except Exception as exc:  # noqa: BLE001 - one bad fh, keep the rest
        logger.warning("Sounding sidecar unreadable, skipping fh%03d in %s: %s", fh, run_root, exc)
        return None

    if _shape_fingerprint(sidecar) != reference["fingerprint"]:
        raise SoundingDataError(
            f"sounding stacks disagree on levels/units/geometry within this run (fh{fh:03d})"
        )

    try:
        profile = sounding_service.read_profile(stack_path, sidecar, row=row, col=col)
    except Exception as exc:  # noqa: BLE001 - one bad fh, keep the rest
        logger.warning("Sounding stack unreadable, skipping fh%03d in %s: %s", fh, run_root, exc)
        return None

    frame: dict[str, Any] = {
        "fh": int(fh),
        "valid_time": sidecar.get("valid_time"),
        "surface": profile.get("surface") or {},
    }
    for var_id in reference["variable_ids"]:
        frame[var_id] = profile.get(var_id)
    return frame


def _attach_indices(frames: list[dict[str, Any]], levels_hpa: list[int]) -> None:
    """Add ``indices`` / ``parcel`` / ``profiles`` to every frame, in place.

    Deliberately the LAST step: the stack reads above are a few hundred bytes
    each, while this is the only expensive part of the request (MetPy parcel
    ascents, ~20 ms per frame measured). Keeping it after the cheap work means
    a future response cache can short-circuit the whole thing.

    A failure here is never allowed to cost the profile that the panel can
    already draw: one bad frame gets ``indices: null`` and the rest are served.

    Frames are independent, so the work is fanned out across a process pool
    (:mod:`app.services.sounding_thermo`), which degrades to this same serial
    loop wherever subprocesses are unavailable.
    """
    computed = sounding_thermo.compute_frames(
        [
            {
                "levels_hpa": levels_hpa,
                "t": frame.get("t") or [],
                "td": frame.get("td") or [],
                "u": frame.get("u"),
                "v": frame.get("v"),
                "surface": frame.get("surface") or {},
            }
            for frame in frames
        ]
    )
    for frame, result in zip(frames, computed):
        frame["indices"] = result.get("indices")
        frame["parcel"] = result.get("parcel")
        frame["profiles"] = result.get("profiles")


def get_sounding(*, model: str, lat: float, lon: float, run: str | None = None) -> dict[str, Any]:
    """Full multi-fh sounding payload for one point (design §5).

    Raises :class:`SoundingUnavailableError` (404), :class:`SoundingRequestError`
    (400) or :class:`SoundingDataError` (500); the route maps them.

    Cached in-process on (model, run, row, col, fh-set) for
    :data:`SOUNDING_CACHE_TTL_S`. The cheap identifying work — run resolution,
    one sidecar read, the lat/lon → (row, col) mapping — runs on every request
    because it *is* the key; everything expensive sits behind the lookup.
    """
    model_norm = str(model or "").strip().lower()
    if not sounding_enabled(model_norm):
        raise SoundingUnavailableError(f"sounding not available for this model: '{model_norm}'")

    run_id, fhs = resolve_sounding_run(model_norm, run)
    run_root = _run_root(model_norm, run_id)

    reference_sidecar: dict[str, Any] | None = None
    usable_fhs: list[int] = []
    for fh in fhs:
        try:
            reference_sidecar = sounding_service.read_sidecar(run_root, fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sounding sidecar unreadable, skipping fh%03d in %s: %s", fh, run_root, exc)
            continue
        usable_fhs = fhs[fhs.index(fh) :]
        break

    if reference_sidecar is None:
        raise SoundingUnavailableError(
            f"no readable sounding stacks for {model_norm}/{run_id}"
        )

    grid_point = locate_grid_point(reference_sidecar, lat=lat, lon=lon)

    cache_key = _cache_key(
        model_norm,
        run_id,
        row=int(grid_point["row"]),
        col=int(grid_point["col"]),
        fhs=usable_fhs,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return _with_request_fields(cached, run=run, lat=lat, lon=lon, grid_point=grid_point)

    reference = {
        "fingerprint": _shape_fingerprint(reference_sidecar),
        "variable_ids": [str(entry["id"]) for entry in reference_sidecar["variables"]],
    }

    frames: list[dict[str, Any]] = []
    for fh in usable_fhs:
        frame = _frame_for_fh(
            run_root=run_root,
            fh=fh,
            row=int(grid_point["row"]),
            col=int(grid_point["col"]),
            reference=reference,
        )
        if frame is not None:
            frames.append(frame)

    if not frames:
        raise SoundingUnavailableError(
            f"no readable sounding stacks for {model_norm}/{run_id}"
        )

    levels_hpa = [int(level) for level in reference_sidecar["levels_hPa"]]
    _attach_indices(frames, levels_hpa)

    # Request-scoped fields (location, grid_point, requested_run) are attached
    # by _with_request_fields AFTER caching — the cached body is cell-scoped only.
    payload: dict[str, Any] = {
        "model": model_norm,
        "run": run_id,
        "levels_hPa": levels_hpa,
        # Server-owned, displayed verbatim: the client never restates what the
        # parcel is (design decision #5).
        "parcel_definition": sounding_indices.PARCEL_DEFINITION,
        "units": _units_map(reference_sidecar),
        "frames": frames,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _cache_put(cache_key, payload)
    return _with_request_fields(payload, run=run, lat=lat, lon=lon, grid_point=grid_point)
