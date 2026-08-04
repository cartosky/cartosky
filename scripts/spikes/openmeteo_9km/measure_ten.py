import time
import numpy as np
from omfiles import OmFileReader
from omblock import BlockCachedHTTPFS
from o1280 import band_indices, TOTAL, points_in_row, row_start, row_lat, NROWS

VARS = ["temperature_2m","dew_point_2m","precipitation","showers",
        "snowfall_water_equivalent","snow_depth","pressure_msl",
        "wind_gusts_10m","wind_u_component_10m","wind_v_component_10m"]
i0, i1, r0, r1 = band_indices(5.0, 82.0)
fs = BlockCachedHTTPFS()
url = "https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs/2026/08/04/1200Z/2026-08-05T0000.om"
t0 = time.time()
rd = OmFileReader.from_fsspec(fs, url)
per = {}
vals = {}
for v in VARS:
    b0 = fs.bytes
    arr = rd.get_child_by_name(v).read_array((slice(0,1), slice(i0, i1)))
    per[v] = fs.bytes - b0
    vals[v] = np.asarray(arr).ravel()
dt = time.time() - t0
for v, b in per.items():
    print(f"  {v:34s} {b/1e6:6.2f} MB")
print(f"TOTAL {fs.bytes/1e6:.2f} MB ({fs.requests} range requests, {dt:.1f}s incl. block overfetch)")
print(f"per-day: x504 files = {fs.bytes*504/1e9:.2f} GB/day (upper bound; lon-subset would cut ~2.4x)")

# O1280 orientation spot check vs values: extract t2m at cities assuming N-first rows, lon 0..360 east.
def point_value(vec, lat, lon):
    # vec covers global indices [i0:i1]; row: nearest by lat under N-first assumption
    best = min(range(1, NROWS+1), key=lambda i: abs(row_lat(i) - lat))
    n = points_in_row(best)
    j = int(round(((lon % 360) / 360) * n)) % n
    gidx = row_start(best) + j
    return vec[gidx - i0] if i0 <= gidx < i1 else float("nan")

for city, la, lo in (("Denver", 39.74, -104.99), ("Toronto", 43.65, -79.38), ("Seattle", 47.61, -122.33)):
    print(f"  t2m {city}: {point_value(vals['temperature_2m'], la, lo):.1f}")
