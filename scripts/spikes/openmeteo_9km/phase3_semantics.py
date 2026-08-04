"""Phase 3 — accumulation semantics for all accumulation vars + cadence
transitions.

Hypothesis (proven for `precipitation` in phase3_accum_check.py): .om
accumulation fields hold the accumulation SINCE THE PREVIOUS STEP, with step
length = the product's cadence at that fh. Tests, all against ECMWF's own
ifs025 product (bilinear-regridded 9 km, MAE floor ~0.02 from Phase 2):

- fh12 (9 km hourly regime vs ifs025 3-hourly): single file should be ~1/3 of
  the reference; the fh10+11+12 sum should match.
- fh93 (both products 3-hourly — just past the 9 km FH90 hourly->3h
  transition): single files should match directly.
- fh150 (both products 6-hourly — past the FH144 3h->6h transition): single
  files should match directly.

August note: NA has no snow, but the check is global (Antarctica/Andes/NZ
carry real snowfall_water_equivalent signal).
"""
import numpy as np

from omfiles import OmFileReader
from omblock import BlockCachedHTTPFS
from o1280 import TOTAL
from phase2_regrid import apply_sampler, build_bilinear_sampler, global_target_grid_025

BASE = "https://openmeteo.s3.amazonaws.com/data_spatial"
RUN = "2026/08/04/1200Z"
VARS = ["precipitation", "snowfall_water_equivalent", "showers"]


def read_field(model: str, valid: str, var: str) -> np.ndarray:
    fs = BlockCachedHTTPFS()
    rd = OmFileReader.from_fsspec(fs, f"{BASE}/{model}/{RUN}/{valid}.om")
    child = rd.get_child_by_name(var)
    if child is None:
        raise KeyError(f"{model}/{valid} missing {var}")
    if tuple(child.shape)[-1] == TOTAL:  # 9 km O1280: shape (1, 6599680)
        return np.asarray(child.read_array((slice(0, 1), slice(0, TOTAL)))).ravel()
    return np.asarray(child.read_array((slice(0, 721), slice(0, 1440))))


lat_g, lon_g, shape_g = global_target_grid_025()
idx_g, w_g = build_bilinear_sampler(lat_g, lon_g)


def regrid(vec: np.ndarray) -> np.ndarray:
    return apply_sampler(idx_g, w_g, vec).reshape(shape_g)


def report(label: str, cand: np.ndarray, ref: np.ndarray) -> None:
    d = cand - ref
    scale = float(np.nanmean(np.abs(ref)))
    print(
        f"  {label:34s} MAE={np.nanmean(np.abs(d)):.4f} bias={np.nanmean(d):+.4f} "
        f"p99={np.nanpercentile(np.abs(d), 99):.3f} (ref mean|.|={scale:.4f})"
    )


def internal_consistency(var: str) -> None:
    """No ifs025 reference (e.g. `showers`): prove the field is per-step by the
    cp<=tp constraint. A run-cumulative field would dwarf per-step
    `precipitation` at late forecast hours; a per-step one stays bounded by it."""
    for fh, valid in ((12, "2026-08-05T0000"), (93, "2026-08-08T0900"), (150, "2026-08-10T1800")):
        cand = read_field("ecmwf_ifs", valid, var)
        tp = read_field("ecmwf_ifs", valid, "precipitation")
        excess = cand - tp
        frac = float((excess > 0.1).mean())
        print(
            f"  fh{fh} {var}<=precip check: mean {var}={np.nanmean(cand):.4f} "
            f"mean precip={np.nanmean(tp):.4f} frac({var}>precip+0.1mm)={frac:.5f} "
            f"max_excess={np.nanmax(excess):.2f}"
        )


for var in VARS:
    print(f"== {var} ==")
    try:
        ref12 = read_field("ecmwf_ifs025", "2026-08-05T0000", var)[::-1]
    except (KeyError, ValueError):
        print("  (no ifs025 reference — internal per-step consistency test)")
        internal_consistency(var)
        continue
    single12 = regrid(read_field("ecmwf_ifs", "2026-08-05T0000", var))
    summed12 = regrid(
        sum(
            read_field("ecmwf_ifs", v, var)
            for v in ("2026-08-04T2200", "2026-08-04T2300", "2026-08-05T0000")
        )
    )
    report("fh12 single (expect ~1/3 low)", single12, ref12)
    report("fh12 sum 10+11+12 (expect match)", summed12, ref12)

    for fh, valid in ((93, "2026-08-08T0900"), (150, "2026-08-10T1800")):
        ref = read_field("ecmwf_ifs025", valid, var)[::-1]
        cand = regrid(read_field("ecmwf_ifs", valid, var))
        report(f"fh{fh} single (expect match)", cand, ref)
