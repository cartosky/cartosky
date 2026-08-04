# Octahedral reduced Gaussian O1280 index geometry.
# Rows 1..2560 pole-to-pole; per-hemisphere row i (1-based from pole) has 16+4i points.
NROWS = 2560

def points_in_row(i):  # i is 1-based from north pole
    k = i if i <= 1280 else NROWS + 1 - i
    return 16 + 4 * k

_starts = [0]
for _i in range(1, NROWS + 1):
    _starts.append(_starts[-1] + points_in_row(_i))
TOTAL = _starts[-1]  # 6_599_680

def row_start(i):
    return _starts[i - 1]

def row_lat(i):  # approximate Gaussian latitude
    return 90.0 - (i - 0.5) * (180.0 / NROWS)

def band_indices(lat_min, lat_max):
    rows = [i for i in range(1, NROWS + 1) if lat_min <= row_lat(i) <= lat_max]
    first, last = min(rows), max(rows)
    return row_start(first), row_start(last) + points_in_row(last), first, last
