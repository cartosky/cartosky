"""AIFS-vs-IFS profile diff: same valid time, Kuchera ratio computed with repo formula.

Holds everything but the profile source fixed (per GPT refinement): only the four
profile temps differ; ratio math is the exact CartoSky vendor formula.
"""
import numpy as np
from omfiles import OmFileReader
from omhttp import CountingHTTPFS

T0 = np.float32(271.16)
LEVELS = [925, 850, 700, 600]
NLAT, NLON = 721, 1440  # 0.25 deg

def kuchera_ratio(temp_stack_k):
    maxt = np.full(temp_stack_k[0].shape, -np.inf, dtype=np.float32)
    valid = np.zeros(temp_stack_k[0].shape, dtype=bool)
    for t in temp_stack_k:
        f = np.isfinite(t)
        valid |= f
        maxt = np.maximum(maxt, np.where(f, t, -np.inf))
    maxt = np.where(valid, maxt, np.nan)
    ratio = np.where(maxt > T0, 12.0 + 2.0 * (T0 - maxt), 12.0 + 1.0 * (T0 - maxt))
    return np.clip(ratio, 5.0, 30.0).astype(np.float32), maxt

def load_profile(model_dir, fname):
    fs = CountingHTTPFS()
    url = f"https://openmeteo.s3.amazonaws.com/data_spatial/{model_dir}/2026/08/04/1200Z/{fname}"
    rd = OmFileReader.from_fsspec(fs, url)
    stack = []
    for lv in LEVELS:
        c = rd.get_child_by_name(f"temperature_{lv}hPa")
        assert c is not None, f"{model_dir} missing temperature_{lv}hPa"
        a = c.read_array((slice(0, NLAT), slice(0, NLON))) if len(c.shape) == 2 else c.read_array((slice(0,1), slice(0, NLAT*NLON))).reshape(NLAT, NLON)
        stack.append(np.asarray(a, dtype=np.float32))
    print(f"{model_dir}: shapes ok, {fs.bytes/1e6:.1f} MB fetched; t850 global mean {stack[1].mean():.1f}")
    return stack

VALID = "2026-08-05T0000.om"  # fh12, exists in both (AIFS 6h cadence)
ifs = load_profile("ecmwf_ifs025", VALID)
aifs = load_profile("ecmwf_aifs025_single", VALID)

# Units check: values ~285 => Kelvin already; ~12 => Celsius, convert.
def to_k(stack):
    return [t + 273.15 if np.nanmean(t) < 200 else t for t in stack]
ifs, aifs = to_k(ifs), to_k(aifs)

# Orientation sanity: row 0 (assumed +90N) should be warmer than last row (S pole, Aug).
t850 = ifs[1]
print(f"orientation check t850: row0(mean)={np.nanmean(t850[0]):.1f}K rowN={np.nanmean(t850[-1]):.1f}K (S->N grid: expect row0 < rowN in Aug)")

lats = np.linspace(-90, 90, NLAT)  # S->N per bucket BBOX and t850 orientation check
lons = np.linspace(-180, 179.75, NLON)
na = np.ix_((lats >= 5) & (lats <= 82), (lons >= -178) & (lons <= -25))
sh = np.ix_((lats >= -60) & (lats <= -20), (lons >= -80) & (lons <= -60))  # Andes winter

for name, region in (("NA", na), ("Andes(SH winter)", sh)):
    ri, mi = kuchera_ratio([t[region] for t in ifs])
    ra, ma = kuchera_ratio([t[region] for t in aifs])
    dt = ma - mi
    dr = ra - ri
    # Restrict SLR stats to where snow is plausible: column max temp < +2C on either model
    snowish = (mi < 275.15) | (ma < 275.15)
    print(f"\n[{name}] maxT diff (AIFS-IFS): mean={np.nanmean(dt):+.2f}K  MAE={np.nanmean(np.abs(dt)):.2f}K  p95|.|={np.nanpercentile(np.abs(dt),95):.2f}K  max|.|={np.nanmax(np.abs(dt)):.2f}K")
    if snowish.any():
        a = np.abs(dr[snowish])
        print(f"[{name}] SLR ratio diff where snow-plausible ({snowish.mean()*100:.0f}% of area): MAE={np.nanmean(a):.2f}  p95={np.nanpercentile(a,95):.2f}  max={np.nanmax(a):.2f}  (ratio scale 5-30)")
        print(f"[{name}] area with |dSLR|>1: {(a>1).mean()*100:.1f}%  >2: {(a>2).mean()*100:.1f}%  >4: {(a>4).mean()*100:.1f}%")
