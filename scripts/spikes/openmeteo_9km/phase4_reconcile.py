"""Phase 4 — cross-source reconciliation on the production NA 9 km grid.

Same run, same valid time, same variable, both sources regridded onto the
exact TAP-snapped NA 1825x1893 EPSG:3857 grid:

- fast:   O1280 native 9 km (the new path)
- legacy: ifs025 0.25 deg bilinear-upsampled (what the current pipeline
          effectively serves; Phase 2 showed ifs025 == the delayed 0.25 source
          to 0.02 C, and the builder's rasterio warp is also bilinear)

Expected: tight agreement at synoptic scale (coarse-averaged fields), real
fine-scale differences (terrain, coastlines, convective structure) — that
delta IS the product improvement, not an error. MSLP must agree tightly at
full resolution too (smooth field; disagreement there would indicate a grid
registration bug, not resolution).

Outputs stats per variable + side-by-side PNGs (early Phase 5 preview).
The formal Phase 5 gate still renders through the real builder pipeline.
"""
import numpy as np

from omfiles import OmFileReader
from omblock import BlockCachedHTTPFS
from o1280 import TOTAL
from phase2_regrid import apply_sampler, build_bilinear_sampler, na_target_grid_9km

BASE = "https://openmeteo.s3.amazonaws.com/data_spatial"
RUN = "2026/08/04/1200Z"

# (variable, valid_time, label) — precip uses fh93 so both sources carry an
# identical 3 h step (Phase 3); the others use fh12.
CASES = [
    ("pressure_msl", "2026-08-05T0000", "MSLP fh12 (alignment guard)"),
    ("temperature_2m", "2026-08-05T0000", "t2m fh12"),
    ("wind_gusts_10m", "2026-08-05T0000", "gusts fh12"),
    ("precipitation", "2026-08-08T0900", "precip 3h step fh93"),
]


def read_field(model: str, valid: str, var: str) -> np.ndarray:
    fs = BlockCachedHTTPFS()
    rd = OmFileReader.from_fsspec(fs, f"{BASE}/{model}/{RUN}/{valid}.om")
    child = rd.get_child_by_name(var)
    if child is None:
        raise KeyError(f"{model}/{valid} missing {var}")
    if tuple(child.shape)[-1] == TOTAL:
        return np.asarray(child.read_array((slice(0, 1), slice(0, TOTAL)))).ravel()
    return np.asarray(child.read_array((slice(0, 721), slice(0, 1440))))


def regular025_bilinear(field_s2n: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Bilinear sample of the 0.25 deg S->N global grid at (lat, lon) points —
    stand-in for the builder's rasterio bilinear warp."""
    fi = (lat + 90.0) / 0.25
    fj = np.mod(lon + 180.0, 360.0) / 0.25
    i0 = np.clip(np.floor(fi).astype(np.int64), 0, 719)
    j0 = np.floor(fj).astype(np.int64) % 1440
    i1, j1 = i0 + 1, (j0 + 1) % 1440
    wi, wj = fi - i0, fj - np.floor(fj)
    f = field_s2n
    return (
        f[i0, j0] * (1 - wi) * (1 - wj)
        + f[i0, j1] * (1 - wi) * wj
        + f[i1, j0] * wi * (1 - wj)
        + f[i1, j1] * wi * wj
    )


def coarsen(a: np.ndarray, k: int = 12) -> np.ndarray:
    """~1 deg mean-pool (12 x 9 km) for the synoptic-agreement metric."""
    h, w = a.shape
    return a[: h // k * k, : w // k * k].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


if __name__ == "__main__":
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lat_na, lon_na, shape_na, _ = na_target_grid_9km()
    idx_na, w_na = build_bilinear_sampler(lat_na, lon_na)

    for var, valid, label in CASES:
        fast = apply_sampler(idx_na, w_na, read_field("ecmwf_ifs", valid, var)).reshape(shape_na)
        legacy = regular025_bilinear(
            read_field("ecmwf_ifs025", valid, var), lat_na, lon_na
        ).reshape(shape_na)
        if var == "pressure_msl":
            # Units differ per product for the SAME variable name: 9 km files
            # store Pa, ifs025 stores hPa (measured 2026-08-04). Normalize to hPa.
            fast = fast / 100.0
        d = fast - legacy
        cf, cl = coarsen(fast), coarsen(legacy)
        corr = float(np.corrcoef(fast.ravel(), legacy.ravel())[0, 1])
        print(
            f"{label:28s} MAE={np.abs(d).mean():.3f} bias={d.mean():+.4f} "
            f"p99={np.percentile(np.abs(d), 99):.3f} max={np.abs(d).max():.2f} "
            f"corr={corr:.5f} synoptic(1deg)MAE={np.abs(cf - cl).mean():.3f}"
        )

        if var in ("temperature_2m", "precipitation"):
            sub = slice(None, None, 2)  # halve for reasonable PNG size
            fig, axes = plt.subplots(1, 3, figsize=(19, 6.2), constrained_layout=True)
            if var == "temperature_2m":
                kw = dict(cmap="turbo", vmin=-10, vmax=40)
                dkw = dict(cmap="RdBu_r", vmin=-4, vmax=4)
                unit = "degC"
            else:
                kw = dict(cmap="turbo", vmin=0, vmax=10)
                dkw = dict(cmap="RdBu_r", vmin=-5, vmax=5)
                unit = "mm/3h"
            for ax, data, ttl, kws in (
                (axes[0], fast[sub, sub], f"fast path: native 9 km ({unit})", kw),
                (axes[1], legacy[sub, sub], "legacy: 0.25 deg upsampled", kw),
                (axes[2], d[sub, sub], "fast - legacy", dkw),
            ):
                im = ax.imshow(data, **kws)
                ax.set_title(ttl)
                ax.axis("off")
                fig.colorbar(im, ax=ax, shrink=0.75)
            fig.suptitle(f"ECMWF 12z 2026-08-04 -> {valid}  |  {label}  |  NA 9 km grid")
            out = f"phase4_{var}.png"
            fig.savefig(out, dpi=110)
            plt.close(fig)
            print(f"  wrote {out}")
