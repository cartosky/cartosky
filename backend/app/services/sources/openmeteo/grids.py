"""Grid decode and samplers for the Open-Meteo fast path.

Two source grid families live in the bucket and they behave differently
(prototype §4 gotcha 1 — *per-product*, not per-model):

``o1280``
    ECMWF IFS HRES native 9 km octahedral reduced Gaussian. 2,560 rows
    pole-to-pole, row *i* (1-based from either pole) holding ``16 + 4i``
    points, 6,599,680 total, stored as one flat ``(1, N)`` array. Rows run
    **north first**, longitudes ``0 → 360°E``. Latitudes are exact Gaussian
    (Legendre roots) — the uniform approximation shifts rows by up to ~2 km
    meridionally, which is a real error at 9 km.

``regular025``
    0.25° regular lat/lon (``ecmwf_ifs025``, ``ecmwf_aifs025_single``, …).
    These are **south-first**, unlike the O1280 files in the same bucket —
    hence :func:`detect_south_to_north`, which is run per run and never
    assumed.

Both families produce ``(idx, weights)`` bilinear samplers consumed by the
shared :func:`apply_sampler`. Samplers are expensive to build (~0.5 s) and
cheap to apply (~0.06 s/field), so :func:`get_sampler` memoises them per
(source grid, target grid) pair and :func:`precompute` warms them at startup.

Target grids come from ``app.services.builder.raster_grid`` rather than being
re-derived here: that module is the production source of truth for the
TAP-snapped NA 9 km EPSG:3857 grid and the native 0.25° EPSG:4326 global grid,
and the fast path must land on *exactly* those grids or nothing downstream
(packing, sidecars, manifests) applies. The expected shape/bbox are still
asserted here so drift fails loudly at build time rather than silently
producing a shifted product.

Ported from ``scripts/spikes/openmeteo_9km/{o1280.py, phase2_regrid.py}`` and
``scripts/openmeteo_monitor/monitor.py`` (orientation detection).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Mapping

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# O1280 octahedral reduced-Gaussian geometry
# ---------------------------------------------------------------------------

#: Rows pole-to-pole.
O1280_NROWS = 2560
#: Total grid points. Any file whose last axis differs is a different grid.
O1280_TOTAL_POINTS = 6_599_680

#: Latitude band the NA 9 km target needs. The NA grid's edge cell centres sit
#: at 4.967°N and 82.006°N — just *outside* a naive 5–82 box, which is how a
#: band read silently loses the first and last rows (prototype §4 gotcha 5).
NA_BAND_LAT = (4.5, 82.5)


def o1280_points_in_row(row: int) -> int:
    """Points in 1-based row ``row`` (1 = northernmost)."""
    if not 1 <= row <= O1280_NROWS:
        raise ValueError(f"row {row} out of range 1..{O1280_NROWS}")
    mirrored = row if row <= O1280_NROWS // 2 else O1280_NROWS + 1 - row
    return 16 + 4 * mirrored


@lru_cache(maxsize=1)
def _o1280_row_tables() -> tuple[np.ndarray, np.ndarray]:
    """``(row_start, row_npts)`` as int64 arrays indexed 0-based from north."""
    npts = np.array(
        [o1280_points_in_row(i) for i in range(1, O1280_NROWS + 1)], dtype=np.int64
    )
    starts = np.concatenate(([0], np.cumsum(npts)[:-1])).astype(np.int64)
    total = int(npts.sum())
    if total != O1280_TOTAL_POINTS:
        raise AssertionError(f"O1280 geometry drift: {total} != {O1280_TOTAL_POINTS}")
    return starts, npts


def o1280_row_start(row: int) -> int:
    """Flat index of the first point of 1-based row ``row``."""
    starts, _ = _o1280_row_tables()
    return int(starts[row - 1])


@lru_cache(maxsize=1)
def o1280_row_latitudes() -> np.ndarray:
    """Exact Gaussian row latitudes, **descending** (row 0 = northernmost).

    ``roots_legendre`` is imported lazily: scipy is a backend dependency but
    the roots computation is only needed when a sampler is actually built.
    """
    from scipy.special import roots_legendre  # noqa: PLC0415

    nodes, _ = roots_legendre(O1280_NROWS)
    return np.degrees(np.arcsin(nodes))[::-1].copy()


def o1280_band_indices(lat_min: float, lat_max: float) -> tuple[int, int]:
    """Flat ``[start, stop)`` index range covering all rows inside the band."""
    lats = o1280_row_latitudes()
    rows = np.nonzero((lats >= float(lat_min)) & (lats <= float(lat_max)))[0]
    if rows.size == 0:
        raise ValueError(f"no O1280 rows inside [{lat_min}, {lat_max}]")
    starts, npts = _o1280_row_tables()
    first, last = int(rows[0]), int(rows[-1])
    return int(starts[first]), int(starts[last] + npts[last])


def na_band_index_range() -> tuple[int, int]:
    """The ``[start, stop)`` O1280 range the NA 9 km target needs."""
    return o1280_band_indices(*NA_BAND_LAT)


# ---------------------------------------------------------------------------
# Regular lat/lon source grids
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegularGrid:
    """A regular lat/lon source grid, row-major flattened.

    ``lat0``/``lon0`` are the *first row/column centres*; ``dlat`` positive
    means row 0 is the southernmost (the bucket's 0.25° convention).
    """

    nlat: int
    nlon: int
    lat0: float = -90.0
    dlat: float = 0.25
    lon0: float = -180.0
    dlon: float = 0.25

    @property
    def points(self) -> int:
        return int(self.nlat) * int(self.nlon)

    @property
    def south_to_north(self) -> bool:
        return self.dlat > 0

    def row_latitudes(self) -> np.ndarray:
        return self.lat0 + self.dlat * np.arange(self.nlat, dtype=np.float64)


#: The bucket's 0.25° products (``ecmwf_ifs025``, ``ecmwf_aifs025_single``):
#: 721×1440, both poles present, rows south-first.
REGULAR_025_SOURCE = RegularGrid(nlat=721, nlon=1440)


# ---------------------------------------------------------------------------
# Samplers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sampler:
    """Precomputed 4-point bilinear gather into a flat source array.

    ``idx`` is ``(4, N)`` int32 flat source indices, ``weights`` ``(4, N)``
    float32 summing to 1 per column, ``shape`` the target's 2D shape. Apply
    with :func:`apply_sampler`.
    """

    idx: np.ndarray
    weights: np.ndarray
    shape: tuple[int, int]

    def __iter__(self):
        """Unpack as ``(idx, weights)``."""
        yield self.idx
        yield self.weights

    @property
    def index_range(self) -> tuple[int, int]:
        """``(min, max+1)`` source indices touched — the minimum band to read."""
        return int(self.idx.min()), int(self.idx.max()) + 1


def apply_sampler(
    sampler: Sampler, values_1d: np.ndarray, *, offset: int = 0
) -> np.ndarray:
    """Gather ``values_1d`` through ``sampler`` onto the target grid.

    ``offset`` is the global flat index of ``values_1d[0]`` — non-zero when a
    band was read instead of the full field.
    """
    flat = np.asarray(values_1d).ravel()
    idx = sampler.idx
    if offset:
        idx = idx - np.int64(offset)
    lo, hi = int(idx.min()), int(idx.max())
    if lo < 0 or hi >= flat.size:
        raise ValueError(
            f"sampler needs source indices [{lo + offset}, {hi + offset}] but the "
            f"supplied band covers [{offset}, {offset + flat.size - 1}]"
        )
    gathered = flat[idx]
    out = (gathered * sampler.weights).sum(axis=0, dtype=np.float64)
    return out.astype(np.float32).reshape(sampler.shape)


def build_bilinear_sampler(
    lat: np.ndarray, lon: np.ndarray, *, shape: tuple[int, int] | None = None
) -> Sampler:
    """Bilinear sampler from the O1280 reduced-Gaussian grid to target points.

    Rows clamp at the poles (targets poleward of the outermost Gaussian row
    take that row's value rather than extrapolating); longitudes wrap, which is
    what makes the antimeridian seam correct for the global target.

    Ported from ``phase2_regrid.build_bilinear_sampler``: rows are N-first,
    longitudes are normalised into ``[0, 360)``.
    """
    lat_arr = np.asarray(lat, dtype=np.float64)
    target_shape = shape if shape is not None else tuple(lat_arr.shape)
    if len(target_shape) != 2:
        raise ValueError(f"target shape must be 2D, got {target_shape}")
    lat_flat = lat_arr.ravel()
    lon_flat = np.mod(np.asarray(lon, dtype=np.float64).ravel(), 360.0)

    lats_n2s = o1280_row_latitudes()
    starts, npts = _o1280_row_tables()

    # First row whose latitude is <= the target latitude (table descends).
    k = np.searchsorted(-lats_n2s, -lat_flat)
    row_lo = np.clip(k, 1, O1280_NROWS - 1)  # southern neighbour
    row_up = row_lo - 1  # northern neighbour
    lat_up = lats_n2s[row_up]
    lat_lo = lats_n2s[row_lo]
    w_lat = np.clip((lat_up - lat_flat) / (lat_up - lat_lo), 0.0, 1.0)  # 0 => upper row

    idx = np.empty((4, lat_flat.size), dtype=np.int64)
    weights = np.empty((4, lat_flat.size), dtype=np.float64)
    for which, (rows, row_weight) in enumerate(((row_up, 1.0 - w_lat), (row_lo, w_lat))):
        n = npts[rows]
        p = lon_flat * n / 360.0
        j0 = np.floor(p).astype(np.int64) % n
        j1 = (j0 + 1) % n
        w_lon = p - np.floor(p)
        base = starts[rows]
        idx[2 * which] = base + j0
        idx[2 * which + 1] = base + j1
        weights[2 * which] = row_weight * (1.0 - w_lon)
        weights[2 * which + 1] = row_weight * w_lon

    return Sampler(
        idx=idx.astype(np.int32),
        weights=weights.astype(np.float32),
        shape=(int(target_shape[0]), int(target_shape[1])),
    )


def build_regular_bilinear_sampler(
    lat: np.ndarray,
    lon: np.ndarray,
    source: RegularGrid = REGULAR_025_SOURCE,
    *,
    shape: tuple[int, int] | None = None,
) -> Sampler:
    """Bilinear sampler from a regular lat/lon source grid to target points.

    Latitude clamps at the source's first/last row; longitude wraps modulo the
    row length. The source array is assumed flattened row-major in the source
    grid's own row order (``source.dlat > 0`` = south-first) — normalise
    orientation *before* sampling if a run's detection says otherwise.
    """
    lat_arr = np.asarray(lat, dtype=np.float64)
    target_shape = shape if shape is not None else tuple(lat_arr.shape)
    if len(target_shape) != 2:
        raise ValueError(f"target shape must be 2D, got {target_shape}")
    lat_flat = lat_arr.ravel()
    lon_flat = np.asarray(lon, dtype=np.float64).ravel()

    nlat, nlon = int(source.nlat), int(source.nlon)
    f_lat = np.clip((lat_flat - source.lat0) / source.dlat, 0.0, nlat - 1.0)
    i0 = np.clip(np.floor(f_lat).astype(np.int64), 0, nlat - 1)
    i1 = np.clip(i0 + 1, 0, nlat - 1)
    w_lat = f_lat - i0

    f_lon = np.mod((lon_flat - source.lon0) / source.dlon, float(nlon))
    j0 = np.floor(f_lon).astype(np.int64) % nlon
    j1 = (j0 + 1) % nlon
    w_lon = f_lon - np.floor(f_lon)

    idx = np.empty((4, lat_flat.size), dtype=np.int64)
    weights = np.empty((4, lat_flat.size), dtype=np.float64)
    for which, (rows, row_weight) in enumerate(((i0, 1.0 - w_lat), (i1, w_lat))):
        base = rows * nlon
        idx[2 * which] = base + j0
        idx[2 * which + 1] = base + j1
        weights[2 * which] = row_weight * (1.0 - w_lon)
        weights[2 * which + 1] = row_weight * w_lon

    return Sampler(
        idx=idx.astype(np.int32),
        weights=weights.astype(np.float32),
        shape=(int(target_shape[0]), int(target_shape[1])),
    )


# ---------------------------------------------------------------------------
# Production target grids
# ---------------------------------------------------------------------------

_R_MERC = 6378137.0

#: Pinned NA 9 km EPSG:3857 geometry. Nothing here computes these — they are
#: asserted against ``raster_grid`` so a change to the production grid fails
#: the fast path loudly instead of shifting the product half a cell.
NA_9KM_SHAPE = (1825, 1893)
NA_9KM_SNAPPED_BBOX = (-19818000.0, 549000.0, -2781000.0, 16974000.0)
#: Native 0.25° EPSG:4326 global geometry: row 0 north, centres 90..-90 /
#: -180..179.75 (docs/GLOBAL_DOMAIN_4326_CONTRACT.md §4).
GLOBAL_025_SHAPE = (721, 1440)


@dataclass(frozen=True)
class TargetGrid:
    """A production target grid as sample points."""

    name: str
    crs: str
    lat: np.ndarray
    lon: np.ndarray
    shape: tuple[int, int]
    bbox: tuple[float, float, float, float]


@lru_cache(maxsize=4)
def na_target_9km(model: str = "ecmwf", region: str = "na") -> TargetGrid:
    """The TAP-snapped NA 9 km EPSG:3857 target, as lat/lon sample points.

    Delegates to ``raster_grid.get_grid_params`` +
    ``raster_grid.compute_transform_and_shape`` so there is one definition of
    the production grid, then hard-asserts :data:`NA_9KM_SHAPE` /
    :data:`NA_9KM_SNAPPED_BBOX`.
    """
    from ...builder.raster_grid import (  # noqa: PLC0415
        compute_transform_and_shape,
        get_grid_params,
    )

    bbox, resolution = get_grid_params(model, region)
    transform, height, width = compute_transform_and_shape(bbox, resolution)
    res = float(resolution)
    xmin = float(transform.c)
    ymax = float(transform.f)
    snapped = (xmin, ymax - height * res, xmin + width * res, ymax)
    if (height, width) != NA_9KM_SHAPE or snapped != NA_9KM_SNAPPED_BBOX:
        raise AssertionError(
            f"NA 9 km target grid drift for {model}/{region}: shape={(height, width)} "
            f"bbox={snapped}, expected {NA_9KM_SHAPE} / {NA_9KM_SNAPPED_BBOX}"
        )

    xs = xmin + (np.arange(width) + 0.5) * res
    ys = ymax - (np.arange(height) + 0.5) * res
    xg, yg = np.meshgrid(xs, ys)
    lon = np.degrees(xg / _R_MERC)
    lat = np.degrees(2.0 * np.arctan(np.exp(yg / _R_MERC)) - np.pi / 2.0)
    return TargetGrid(
        name="na_9km",
        crs="EPSG:3857",
        lat=lat,
        lon=lon,
        shape=(int(height), int(width)),
        bbox=snapped,
    )


@lru_cache(maxsize=4)
def global_target_025(model: str = "ecmwf", region: str = "global") -> TargetGrid:
    """The native 0.25° EPSG:4326 global target (721×1440, row 0 north)."""
    from ...builder.raster_grid import get_target_grid  # noqa: PLC0415

    grid = get_target_grid(model, region)
    if (grid.height, grid.width) != GLOBAL_025_SHAPE:
        raise AssertionError(
            f"global 0.25° target grid drift for {model}/{region}: "
            f"shape={(grid.height, grid.width)}, expected {GLOBAL_025_SHAPE}"
        )
    res = float(grid.resolution)
    lon_1d = float(grid.transform.c) + res / 2.0 + res * np.arange(grid.width)
    lat_1d = float(grid.transform.f) - res / 2.0 - res * np.arange(grid.height)
    lon, lat = np.meshgrid(lon_1d, lat_1d)
    return TargetGrid(
        name="global_025",
        crs=grid.crs,
        lat=lat,
        lon=lon,
        shape=(int(grid.height), int(grid.width)),
        bbox=tuple(float(v) for v in grid.bbox),  # type: ignore[arg-type]
    )


#: Target-grid builders addressable by name.
TARGET_BUILDERS: Mapping[str, Callable[[], TargetGrid]] = {
    "na_9km": na_target_9km,
    "global_025": global_target_025,
}

#: Source grid kinds addressable by name (mirrors ``catalog.GridKind``).
SOURCE_KINDS = ("o1280", "regular025")

#: The pairs the ECMWF fast path needs. One full-globe O1280 read feeds both
#: (design Decision 1).
DEFAULT_SAMPLER_PAIRS: tuple[tuple[str, str], ...] = (
    ("o1280", "na_9km"),
    ("o1280", "global_025"),
)


# ---------------------------------------------------------------------------
# Sampler cache
# ---------------------------------------------------------------------------

_sampler_cache: dict[tuple[str, str], Sampler] = {}
_sampler_lock = threading.Lock()


def get_sampler(source_kind: str, target: str) -> Sampler:
    """Memoised sampler for a (source grid, target grid) pair.

    Build cost is ~0.5 s and the result is a few hundred MB-free numpy arrays
    (~50 MB for the global pair), so this is built once per process.
    """
    key = (str(source_kind), str(target))
    cached = _sampler_cache.get(key)
    if cached is not None:
        return cached
    with _sampler_lock:
        cached = _sampler_cache.get(key)
        if cached is not None:
            return cached
        if key[0] not in SOURCE_KINDS:
            raise KeyError(f"unknown source grid kind {key[0]!r}")
        builder = TARGET_BUILDERS.get(key[1])
        if builder is None:
            raise KeyError(f"unknown target grid {key[1]!r}")
        grid = builder()
        if key[0] == "o1280":
            sampler = build_bilinear_sampler(grid.lat, grid.lon, shape=grid.shape)
        else:
            sampler = build_regular_bilinear_sampler(
                grid.lat, grid.lon, REGULAR_025_SOURCE, shape=grid.shape
            )
        _sampler_cache[key] = sampler
        logger.info(
            "openmeteo: built %s -> %s sampler shape=%s", key[0], key[1], sampler.shape
        )
        return sampler


def precompute(pairs: tuple[tuple[str, str], ...] = DEFAULT_SAMPLER_PAIRS) -> None:
    """Warm the sampler cache at startup so the first frame is not slow."""
    for source_kind, target in pairs:
        get_sampler(source_kind, target)


def clear_sampler_cache() -> None:
    """Drop cached samplers and target grids (tests / grid-config reload)."""
    with _sampler_lock:
        _sampler_cache.clear()
    na_target_9km.cache_clear()
    global_target_025.cache_clear()


# ---------------------------------------------------------------------------
# Orientation detection and self-checks
# ---------------------------------------------------------------------------


def detect_south_to_north(t2m: np.ndarray, source: RegularGrid = REGULAR_025_SOURCE) -> bool:
    """Season-independent row-order detection for a **regular** grid.

    The 45°N row crosses North America and Eurasia (high land fraction → high
    longitudinal variance of 2 m temperature); 45°S is nearly pure Southern
    Ocean (low variance) in every season. Returns True if the array is
    south-first.

    Note ±23° is **NOT** safe here: the Capricorn row crosses
    Australia/Kalahari/Andes and its variance can rival the Sahara's (this bit
    the monitor on 2026-08-04). Ported verbatim from
    ``scripts/openmeteo_monitor/monitor.py:detect_south_to_north``.

    O1280 files need no detection — the reduced-Gaussian layout is north-first
    by construction and a flipped file would fail the geometry decode.
    """
    field = np.asarray(t2m)
    if field.ndim != 2:
        raise ValueError(f"expected a 2D field, got shape {field.shape}")
    row_north = int(round((45.0 - source.lat0) / abs(source.dlat)))
    row_south = int(round((-45.0 - source.lat0) / abs(source.dlat)))
    variance_if_s2n = float(np.nanvar(field[row_north]))
    variance_if_n2s = float(np.nanvar(field[row_south]))
    return variance_if_s2n >= variance_if_n2s


def tropics_warmer_than_arctic(t2m: np.ndarray, row_latitudes: np.ndarray) -> bool:
    """Physical self-check on an **output** field: equator warmer than pole.

    Compares the mean of the row nearest the equator against the row nearest
    the pole. Meaningful only for a hemispheric window (the NA band, rows
    ~5°N→82°N) — a global field's two edge rows are both polar. A False here
    means the output was mirrored somewhere between read and regrid and the
    run's fast build must abort (design §6 "upstream layout/orientation
    change").
    """
    field = np.asarray(t2m)
    if field.ndim != 2:
        raise ValueError(f"expected a 2D field, got shape {field.shape}")
    lats = np.asarray(row_latitudes, dtype=np.float64).ravel()
    if lats.size != field.shape[0]:
        raise ValueError(
            f"row_latitudes has {lats.size} entries for {field.shape[0]} rows"
        )
    equatorward = int(np.argmin(np.abs(lats)))
    poleward = int(np.argmax(np.abs(lats)))
    return float(np.nanmean(field[equatorward])) > float(np.nanmean(field[poleward]))
