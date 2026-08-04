import numpy as np, sys
sys.path.insert(0, ".")
from omfiles import OmFileReader
from omblock import BlockCachedHTTPFS
from o1280 import TOTAL
from phase2_regrid import build_bilinear_sampler, global_target_grid_025, apply_sampler

BASE = "https://openmeteo.s3.amazonaws.com/data_spatial"
RUN = "2026/08/04/1200Z"

def read_9km_precip(valid):
    fs = BlockCachedHTTPFS()
    rd = OmFileReader.from_fsspec(fs, f"{BASE}/ecmwf_ifs/{RUN}/{valid}.om")
    return np.asarray(rd.get_child_by_name("precipitation").read_array((slice(0,1), slice(0, TOTAL)))).ravel()

# 9km hourly steps covering (fh9, fh12]: valid 22:00, 23:00, 00:00
summed = sum(read_9km_precip(v) for v in ("2026-08-04T2200", "2026-08-04T2300", "2026-08-05T0000"))
single = read_9km_precip("2026-08-05T0000")

fs = BlockCachedHTTPFS()
rd = OmFileReader.from_fsspec(fs, f"{BASE}/ecmwf_ifs025/{RUN}/2026-08-05T0000.om")
ref = np.asarray(rd.get_child_by_name("precipitation").read_array((slice(0,721), slice(0,1440))))[::-1]

lat_g, lon_g, shape_g = global_target_grid_025()
idx_g, w_g = build_bilinear_sampler(lat_g, lon_g)
for name, src in (("single fh12 file", single), ("sum fh10+11+12", summed)):
    d = apply_sampler(idx_g, w_g, src).reshape(shape_g) - ref
    print(f"{name:18s} vs ifs025 fh12: MAE={np.nanmean(np.abs(d)):.4f}mm bias={np.nanmean(d):+.4f} p99={np.nanpercentile(np.abs(d),99):.3f}")
