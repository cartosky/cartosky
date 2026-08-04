"""Phase 2 — grid correctness: O1280 reduced Gaussian -> CartoSky target grids.

One precomputed sampler serves both build families:

- NA:     EPSG:3857, 9 km, TAP-snapped bbox (raster_grid.compute_transform_and_shape
          rules replicated exactly) — bilinear in reduced-Gaussian space.
- global: EPSG:4326, 0.25 deg point-registered, row 0 north (raster_grid.
          compute_native_transform_and_shape) — bilinear AND box-mean (aliasing
          test for the 9 km -> 25 km downsample), antimeridian-wrapped.

Validation oracles:
- global: ECMWF's own 0.25 deg product (ecmwf_ifs025 bucket file, same run/valid).
- NA:     Open-Meteo point API at low-terrain sites (elevation downscaling in the
          API poisons mountain points, so those are reported but not gated).

Latitudes are exact Gaussian (Legendre roots), not uniform-approximated: the
uniform approximation shifts rows by up to ~2 km meridionally, which matters
for a 9 km product.

NOTE: the NA fetch band must be 4.5..82.5 (not 5..82): the NA grid's edge cell
centers sit at 4.967N and 82.006N, just outside the WGS84 reference bbox.
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy.special import roots_legendre

from o1280 import NROWS, TOTAL, points_in_row, row_start

R_MERC = 6378137.0

# --- exact O1280 row latitudes, N -> S (row 0 northernmost) -----------------
_x, _ = roots_legendre(NROWS)
GAUSS_LATS_N2S = np.degrees(np.arcsin(_x))[::-1].copy()  # descending

_ROW_START = np.array([row_start(i) for i in range(1, NROWS + 1)], dtype=np.int64)
_ROW_NPTS = np.array([points_in_row(i) for i in range(1, NROWS + 1)], dtype=np.int64)


def build_bilinear_sampler(lat: np.ndarray, lon: np.ndarray):
    """Precompute 4-point indices/weights into the O1280 1D array for target
    points (degrees, any lon convention). Rows clamp at the poles; longitudes
    wrap. Returns (idx[4,N] int32, w[4,N] float32)."""
    lat = np.asarray(lat, dtype=np.float64).ravel()
    lon = np.mod(np.asarray(lon, dtype=np.float64).ravel(), 360.0)

    # bounding rows in the descending-lat table
    k = np.searchsorted(-GAUSS_LATS_N2S, -lat)  # first row with row_lat <= lat
    r_lo = np.clip(k, 1, NROWS - 1)             # southern neighbor row
    r_up = r_lo - 1                             # northern neighbor row
    lat_up = GAUSS_LATS_N2S[r_up]
    lat_lo = GAUSS_LATS_N2S[r_lo]
    wlat = np.clip((lat_up - lat) / (lat_up - lat_lo), 0.0, 1.0)  # 0 => upper row

    idx = np.empty((4, lat.size), dtype=np.int64)
    w = np.empty((4, lat.size), dtype=np.float64)
    for which, rows, wrow in ((0, r_up, 1.0 - wlat), (1, r_lo, wlat)):
        n = _ROW_NPTS[rows]
        p = lon * n / 360.0
        j0 = np.floor(p).astype(np.int64) % n
        j1 = (j0 + 1) % n
        wlon = p - np.floor(p)
        base = _ROW_START[rows]
        idx[2 * which] = base + j0
        idx[2 * which + 1] = base + j1
        w[2 * which] = wrow * (1.0 - wlon)
        w[2 * which + 1] = wrow * wlon
    return idx.astype(np.int32), w.astype(np.float32)


def apply_sampler(idx, w, data_1d, offset=0):
    """data_1d may be a fetched sub-band; offset is its global start index."""
    gathered = data_1d[idx - offset]
    return (gathered * w).sum(axis=0)


# --- target grids (replicating raster_grid.py exactly) ----------------------

def na_target_grid_9km():
    xmin, ymin, xmax, ymax = (-19814869.36, 557305.26, -2782987.27, 16967796.94)
    res = 9000.0
    axmin = math.floor(xmin / res) * res
    aymax = math.ceil(ymax / res) * res
    axmax = math.ceil(xmax / res) * res
    aymin = math.floor(ymin / res) * res
    width = round((axmax - axmin) / res)
    height = round((aymax - aymin) / res)
    xs = axmin + (np.arange(width) + 0.5) * res
    ys = aymax - (np.arange(height) + 0.5) * res
    xg, yg = np.meshgrid(xs, ys)
    lon = np.degrees(xg / R_MERC)
    lat = np.degrees(2.0 * np.arctan(np.exp(yg / R_MERC)) - np.pi / 2.0)
    return lat, lon, (height, width), (axmin, aymin, axmax, aymax)


def global_target_grid_025():
    # compute_native_transform_and_shape((-180.125,-90.125,179.875,90.125), 0.25)
    width, height = 1440, 721
    lon = -180.0 + 0.25 * np.arange(width)
    lat = 90.0 - 0.25 * np.arange(height)  # row 0 north
    lg, tg = np.meshgrid(lon, lat)
    return tg, lg, (height, width)


def build_boxmean_assignment():
    """Assign every O1280 point to its containing 0.25 deg point-registered
    cell (cells centered on 0.25 multiples). Returns flat cell index per point."""
    lats = np.repeat(GAUSS_LATS_N2S, _ROW_NPTS)
    lons = np.concatenate([360.0 * np.arange(n) / n for n in _ROW_NPTS])
    row = np.clip(np.round((90.0 - lats) / 0.25).astype(np.int64), 0, 720)
    lon_c = np.mod(lons + 180.0, 360.0) - 180.0  # [-180, 180)
    col = np.mod(np.round((lon_c + 180.0) / 0.25).astype(np.int64), 1440)
    return row * 1440 + col


def boxmean_global(data_1d):
    cell = build_boxmean_assignment()
    sums = np.bincount(cell, weights=data_1d.astype(np.float64), minlength=721 * 1440)
    cnts = np.bincount(cell, minlength=721 * 1440)
    out = np.divide(sums, cnts, out=np.full(721 * 1440, np.nan), where=cnts > 0)
    return out.reshape(721, 1440)


if __name__ == "__main__":
    from omfiles import OmFileReader
    from omblock import BlockCachedHTTPFS

    RUN_DIR = "2026/08/04/1200Z"
    VALID = "2026-08-05T0000"  # fh12
    url_9km = f"https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs/{RUN_DIR}/{VALID}.om"
    url_025 = f"https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs025/{RUN_DIR}/{VALID}.om"

    # ---- read full-globe t2m from the 9 km file (global build path) --------
    fs = BlockCachedHTTPFS()
    rd = OmFileReader.from_fsspec(fs, url_9km)
    t0 = time.time()
    t2m_full = np.asarray(
        rd.get_child_by_name("temperature_2m").read_array((slice(0, 1), slice(0, TOTAL)))
    ).ravel()
    print(f"full-globe t2m read: {fs.bytes/1e6:.1f} MB in {time.time()-t0:.1f}s")

    # ---- NA 9 km grid ------------------------------------------------------
    t0 = time.time()
    lat_na, lon_na, shape_na, snapped = na_target_grid_9km()
    idx_na, w_na = build_bilinear_sampler(lat_na, lon_na)
    print(f"NA sampler: shape={shape_na} snapped_bbox={snapped} build={time.time()-t0:.1f}s")
    t0 = time.time()
    na = apply_sampler(idx_na, w_na, t2m_full).reshape(shape_na)
    print(f"NA apply: {time.time()-t0:.2f}s  t2m degC min={na.min():.1f} max={na.max():.1f}")

    # Band-read coverage check: indices needed for NA vs the 4.5..82.5 band
    need_lo, need_hi = idx_na.min(), idx_na.max()
    from o1280 import band_indices
    b0, b1, _, _ = band_indices(4.5, 82.5)
    print(f"NA needs global idx [{need_lo}, {need_hi}] vs band(4.5-82.5)=[{b0}, {b1}] "
          f"contained={b0 <= need_lo and need_hi < b1}")

    # ---- global 0.25 grid: bilinear + box-mean vs ECMWF's own 0.25 ---------
    lat_g, lon_g, shape_g = global_target_grid_025()
    t0 = time.time()
    idx_g, w_g = build_bilinear_sampler(lat_g, lon_g)
    gbl_bilin = apply_sampler(idx_g, w_g, t2m_full).reshape(shape_g)
    print(f"global bilinear: build+apply {time.time()-t0:.1f}s")
    t0 = time.time()
    gbl_box = boxmean_global(t2m_full)
    print(f"global box-mean: {time.time()-t0:.1f}s")

    fs2 = BlockCachedHTTPFS()
    rd2 = OmFileReader.from_fsspec(fs2, url_025)

    def compare_global(var: str):
        src = np.asarray(
            rd.get_child_by_name(var).read_array((slice(0, 1), slice(0, TOTAL)))
        ).ravel()
        ref = np.asarray(
            rd2.get_child_by_name(var).read_array((slice(0, 721), slice(0, 1440)))
        )[::-1]  # bucket 0.25 files are S->N; CartoSky global grid is N->S
        bilin = apply_sampler(idx_g, w_g, src).reshape(shape_g)
        box = boxmean_global(src)
        empty = int(np.isnan(box).sum())
        for name, arr in (("bilinear", bilin), ("box-mean", box)):
            d = arr - ref
            print(
                f"global {var} {name:8s} vs ecmwf_ifs025: MAE={np.nanmean(np.abs(d)):.4f} "
                f"p95={np.nanpercentile(np.abs(d),95):.4f} max={np.nanmax(np.abs(d)):.2f} "
                f"bias={np.nanmean(d):+.4f}"
                + (f" empty_cells={empty}" if name == "box-mean" else "")
            )

    for var in ("temperature_2m", "precipitation"):
        compare_global(var)

    # ---- NA point oracle (flat sites gate; mountain sites informational) ---
    SITES = [  # name, lat, lon, flat?
        ("Chicago", 41.88, -87.63, True), ("Dallas", 32.78, -96.80, True),
        ("Miami", 25.76, -80.19, True), ("Winnipeg", 49.90, -97.14, True),
        ("KansasCity", 39.10, -94.58, True), ("Denver", 39.74, -104.99, False),
        ("Seattle", 47.61, -122.33, False),
    ]
    axmin, aymin, axmax, aymax = snapped
    print("site        grid_t2m  (compare with point API, printed by phase2_oracle.sh)")
    for name, la, lo, flat in SITES:
        x = math.radians(lo) * R_MERC
        y = R_MERC * math.log(math.tan(math.pi / 4 + math.radians(la) / 2))
        col = int((x - axmin) / 9000.0)
        row = int((aymax - y) / 9000.0)
        print(f"  {name:10s} {na[row, col]:7.1f}C  ({'flat' if flat else 'terrain'})")
