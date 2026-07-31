"""Regenerates ``frontend/src/assets/skewt-background.json`` (Skew-T design §6).

Precomputes the Skew-T background geometry (dry adiabats, moist adiabats,
saturation mixing-ratio lines) as polylines in DATA coordinates. Each line is
``{"label": <number>, "p": [hPa...], "t": [degC...]}`` where ``t`` is the
temperature (degC) the curve has at that pressure. Rendering — skew transform,
clipping, styling — is entirely the client's job.

The output is a CHECKED-IN generated asset: the frontend imports it as a
bundled module, so this script only needs to run if the plot domain
(``P_TOP``/``P_BOTTOM``) or the curve families below change.

Requires ``metpy``, which is deliberately NOT a production backend dependency —
nothing on the serving path runs this. Install it into a throwaway venv:

    python3 -m venv /tmp/venv-skewt-bg
    /tmp/venv-skewt-bg/bin/pip install metpy numpy
    /tmp/venv-skewt-bg/bin/python backend/scripts/gen_skewt_background.py

then copy the emitted ``background_lines.json`` over
``frontend/src/assets/skewt-background.json``.
"""

import json
import os

import numpy as np
from metpy.calc import dewpoint, dry_lapse, moist_lapse, vapor_pressure
from metpy.units import units

HERE = os.path.dirname(os.path.abspath(__file__))

P_TOP = 100.0
P_BOTTOM = 1050.0
N_LEVELS = 40

# Log-spaced pressure levels, high pressure first (bottom -> top of chart).
PRESSURE = np.logspace(np.log10(P_BOTTOM), np.log10(P_TOP), N_LEVELS)
P = PRESSURE * units.hPa

DRY_THETAS = np.arange(-30, 161, 10)          # degC at 1000 hPa
MOIST_STARTS = np.arange(-20, 41, 5)          # degC at 1000 hPa
MIXING_RATIOS = [0.5, 1, 2, 4, 8, 12, 16, 20, 28]  # g/kg


def _clean(p_vals, t_vals):
    """Drop non-finite samples, round for compact JSON."""
    p_out, t_out = [], []
    for p, t in zip(p_vals, t_vals):
        if np.isfinite(t):
            p_out.append(round(float(p), 2))
            t_out.append(round(float(t), 3))
    return p_out, t_out


def dry_adiabats():
    out = []
    for theta in DRY_THETAS:
        t = dry_lapse(P, theta * units.degC, 1000 * units.hPa).to("degC")
        p_l, t_l = _clean(PRESSURE, t.magnitude)
        out.append({"label": int(theta), "p": p_l, "t": t_l})
    return out


def moist_adiabats():
    out = []
    for t0 in MOIST_STARTS:
        # moist_lapse integrates the pseudoadiabatic ODE; reference 1000 hPa sits
        # inside the domain, so integrate downward and upward separately.
        below = PRESSURE[PRESSURE >= 1000.0]
        above = PRESSURE[PRESSURE < 1000.0]

        t_below = moist_lapse(
            np.concatenate([[1000.0], below[::-1]]) * units.hPa,
            t0 * units.degC,
        ).to("degC").magnitude[1:][::-1]
        t_above = moist_lapse(
            np.concatenate([[1000.0], above]) * units.hPa,
            t0 * units.degC,
        ).to("degC").magnitude[1:]

        t_all = np.concatenate([t_below, t_above])
        p_l, t_l = _clean(PRESSURE, t_all)
        out.append({"label": int(t0), "p": p_l, "t": t_l})
    return out


def mixing_ratio_lines():
    out = []
    for w in MIXING_RATIOS:
        mr = (w / 1000.0) * units("kg/kg")   # g/kg -> dimensionless kg/kg
        e = vapor_pressure(P, mr)
        td = dewpoint(e).to("degC")
        p_l, t_l = _clean(PRESSURE, td.magnitude)
        out.append({"label": w, "p": p_l, "t": t_l})
    return out


def main():
    data = {
        "meta": {
            "generator": "gen_background.py (MetPy)",
            "pressure_domain_hPa": [P_BOTTOM, P_TOP],
            "n_levels": N_LEVELS,
            "coords": "data coordinates: temperature degC at each pressure hPa",
            "reference_pressure_hPa": 1000.0,
        },
        "pressure_levels": [round(float(p), 2) for p in PRESSURE],
        "dry_adiabats": dry_adiabats(),
        "moist_adiabats": moist_adiabats(),
        "mixing_ratio_lines": mixing_ratio_lines(),
    }
    path = os.path.join(HERE, "background_lines.json")
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {path}")
    for k in ("dry_adiabats", "moist_adiabats", "mixing_ratio_lines"):
        print(f"  {k}: {len(data[k])} curves")


if __name__ == "__main__":
    main()
