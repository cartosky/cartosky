"""Thermodynamic indices + parcel path for one sounding frame (Phase 4).

Normative: ``docs/SKEWT_DESIGN_2026-07-30.md`` §7 Phase 4 and decision #5.

Everything here is **server-side MetPy** — the client contains zero
thermodynamics (design bullet "Thermo policy"). One call takes a single decoded
frame (the 37-level ladder plus the surface block that
:func:`app.services.sounding.read_profile` returns) and gives back:

* ``indices``  — SBCAPE/SBCIN, MLCAPE/MLCIN, LCL, LFC, EL, PWAT
* ``parcel``   — the (p, T) polyline the Skew-T draws for the SB parcel
* ``profiles`` — per-level wet-bulb, θe and height AGL for the Phase 5
  overlays (wet-bulb trace, θe inset, height-coloured hodograph)

## The column (design §2)

Indices are computed on the **anchored** column: the surface block first, then
only isobaric levels strictly above the ground (``p < pres_sfc``). This mirrors
the spike's ``step5_indices.py`` exactly — computing on the raw isobaric ladder
put a fictitious below-ground parcel origin in the integral and moved SBCAPE by
hundreds of J/kg on the reference profile.

## The SB parcel (decision #5, RESOLVED 2026-07-31)

Origin = HRRR's native **2 m temperature and 2 m dewpoint at the surface
pressure**. CAPE/CIN carry the **virtual-temperature correction** (Doswell &
Rasmussen 1994): both the environment and the parcel are converted to virtual
temperature before the buoyancy integral, via MetPy's documented
``cape_cin(p, Tv_env, Td, Tv_parcel)`` form. Below the LCL the parcel keeps the
mixing ratio it started with; above it, it is saturated. That is the *only*
correction applied — no parcel-origin blending, no mixed-layer fallback, no
reverse-engineering of another product's undocumented convention.

The definition is published verbatim to the client as
:data:`PARCEL_DEFINITION` so the UI never has to restate it.

## LFC / EL markers

These annotate the parcel path that is actually **drawn**, so they come from the
plain (non-Tv) parcel profile — a marker that did not sit on the visible curve
would be a lie. They are legitimately undefined for a capped sounding and are
``None`` then, never 0.

## Robustness contract

No input can make this module raise: a frame whose surface block is nodata, or
whose column collapses to nothing, yields every key ``None``. Individual
metrics degrade to ``None`` on their own without taking the rest of the frame
down.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Mapping, Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: Verbatim UI string — single source of truth for what the SB parcel *is*.
#: The parcel definition is identical across models (decision #5); only the name
#: of the model whose 2 m fields it uses changes, so Phase 6 parameterises that
#: one token rather than forking the sentence.
PARCEL_DEFINITION_TEMPLATE = "SB parcel: {model} 2 m T/Td, virtual-temperature corrected"

#: Backwards-compatible HRRR rendering (pre-Phase-6 callers, tests).
PARCEL_DEFINITION = PARCEL_DEFINITION_TEMPLATE.format(model="HRRR")

_MODEL_LABELS = {"hrrr": "HRRR", "gfs": "GFS", "ecmwf": "ECMWF"}


def parcel_definition_for_model(model: str | None) -> str:
    """The parcel caption naming *model*, e.g. "SB parcel: GFS 2 m T/Td, ...".

    An unknown id falls back to the neutral word "model" rather than shouting a
    raw internal id at the user.
    """
    key = str(model or "").strip().lower()
    return PARCEL_DEFINITION_TEMPLATE.format(model=_MODEL_LABELS.get(key, "model"))

#: Mixed-layer parcel depth (design §7 Phase 4).
MIXED_LAYER_DEPTH_HPA = 100.0

#: Below this many usable isobaric levels the column is not worth integrating.
MIN_COLUMN_LEVELS = 8


def _metpy() -> tuple[Any, Any]:
    """Import MetPy lazily.

    MetPy pulls in pint + a units registry (~1 s of import time); the sounding
    endpoint is the only caller, so the API process should not pay for it at
    startup. Same lazy-import pattern as ``sampling.resolve_run``'s ``main``
    import.
    """
    import metpy.calc as mpcalc
    from metpy.units import units

    return mpcalc, units


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _scalar(quantity: Any) -> float | None:
    """Magnitude of a pint scalar, or ``None`` when it is NaN/undefined."""
    try:
        return _finite(np.asarray(quantity.m).reshape(-1)[0])
    except Exception:  # noqa: BLE001 - defensive: never raise out of this module
        return None


def _round_or_none(value: float | None, digits: int | None) -> float | int | None:
    if value is None:
        return None
    return int(round(value)) if digits is None else round(value, digits)


class _Column:
    """The anchored column, in plain floats (units are attached at use time).

    ``idx`` maps each column entry back to its index in the caller's isobaric
    level ladder, with ``-1`` for the surface entry. Phase 5's per-level
    profiles are computed on this column but shipped **level-aligned**, so the
    client's existing index-parallel arrays keep working.
    """

    __slots__ = ("p", "t", "td", "idx")

    def __init__(self, p: np.ndarray, t: np.ndarray, td: np.ndarray, idx: np.ndarray) -> None:
        self.p = p
        self.t = t
        self.td = td
        self.idx = idx


def build_column(
    levels_hpa: Sequence[Any],
    *,
    t: Sequence[Any],
    td: Sequence[Any],
    surface: Mapping[str, Any],
) -> _Column | None:
    """Surface-anchored (p, T, Td) column, or ``None`` when it cannot be built.

    Levels whose T or Td is nodata are dropped rather than failing the frame —
    a hole in the ladder is a data gap, not a broken profile — but a column that
    falls below :data:`MIN_COLUMN_LEVELS` is not integrated at all.
    """
    psfc = _finite(surface.get("pres_sfc"))
    t2m = _finite(surface.get("t2m"))
    td2m = _finite(surface.get("td2m"))
    if psfc is None or t2m is None or td2m is None:
        return None
    if not (200.0 <= psfc <= 1100.0):
        return None

    pressures: list[float] = [psfc]
    temps: list[float] = [t2m]
    dewpoints: list[float] = [td2m]
    source: list[int] = [-1]

    kept = 0
    last_p = psfc
    for index, level in enumerate(levels_hpa):
        level_p = _finite(level)
        if level_p is None or level_p >= psfc or level_p >= last_p:
            continue
        level_t = _finite(t[index]) if index < len(t) else None
        level_td = _finite(td[index]) if index < len(td) else None
        if level_t is None or level_td is None:
            continue
        pressures.append(level_p)
        temps.append(level_t)
        dewpoints.append(level_td)
        source.append(index)
        last_p = level_p
        kept += 1

    if kept < MIN_COLUMN_LEVELS:
        return None

    p_array = np.asarray(pressures, dtype=float)
    t_array = np.asarray(temps, dtype=float)
    # Quantization can push a saturated level's Td a hundredth of a degree above
    # T; MetPy treats supersaturation as an error, so clamp it away.
    td_array = np.minimum(np.asarray(dewpoints, dtype=float), t_array)
    return _Column(p_array, t_array, td_array, np.asarray(source, dtype=int))


def _parcel_path(mpcalc: Any, units: Any, column: _Column) -> dict[str, list[float]] | None:
    """(p, T) polyline for the drawn parcel: dry to the LCL, moist above.

    Sampled at the stack's own levels plus the inserted LCL point — enough
    resolution for a 640 px tall plot, and small enough to ship per frame.
    """
    try:
        p_out, _t_env, _td_env, t_parcel = mpcalc.parcel_profile_with_lcl(
            column.p * units.hPa, column.t * units.degC, column.td * units.degC
        )
        pressures = np.asarray(p_out.to(units.hPa).m, dtype=float)
        temperatures = np.asarray(t_parcel.to(units.degC).m, dtype=float)
    except Exception:  # noqa: BLE001
        logger.debug("Sounding parcel path failed", exc_info=True)
        return None

    keep = np.isfinite(pressures) & np.isfinite(temperatures)
    if int(keep.sum()) < 2:
        return None
    return {
        "p": [round(float(value), 1) for value in pressures[keep]],
        "t": [round(float(value), 1) for value in temperatures[keep]],
    }


def _virtual_temperature_cape_cin(
    mpcalc: Any, units: Any, column: _Column, parcel: Any, lcl_pressure: Any
) -> tuple[float | None, float | None]:
    """SBCAPE/SBCIN with the virtual-temperature correction (decision #5).

    Environment: Tv from the observed mixing ratio.
    Parcel: constant mixing ratio (its surface saturation value at Td) below the
    LCL, saturated at the parcel temperature above it. This is the standard
    Doswell & Rasmussen treatment and reproduces the spike's reference value.
    """
    try:
        p = column.p * units.hPa
        t = column.t * units.degC
        td = column.td * units.degC
        w_env = mpcalc.mixing_ratio_from_relative_humidity(
            p, t, mpcalc.relative_humidity_from_dewpoint(t, td)
        )
        w_parcel = (
            np.where(
                column.p >= float(lcl_pressure.to(units.hPa).m),
                mpcalc.saturation_mixing_ratio(p[0], td[0]).m,
                mpcalc.saturation_mixing_ratio(p, parcel).m,
            )
            * units("kg/kg")
        )
        cape, cin = mpcalc.cape_cin(
            p,
            mpcalc.virtual_temperature(t, w_env),
            td,
            mpcalc.virtual_temperature(parcel, w_parcel),
        )
    except Exception:  # noqa: BLE001
        logger.debug("Tv-corrected SBCAPE failed", exc_info=True)
        return None, None
    return _scalar(cape), _scalar(cin)


# ---------------------------------------------------------------------------
# Phase 5 — per-level overlay profiles
# ---------------------------------------------------------------------------


def _hypsometric_heights_m(mpcalc: Any, units: Any, column: _Column) -> np.ndarray:
    """Height AGL (m) at every column level, surface = 0.

    The stack deliberately omits HGT (design §1: "+25% cost for an axis we can
    reconstruct hypsometrically"), so the hodograph's height colouring comes
    from integrating layer thicknesses upward from the surface:

        Δz = (Rd / g) · Tv̄ · ln(p_below / p_above)

    with ``Tv̄`` the layer-mean virtual temperature. That is exactly MetPy's
    ``thickness_hydrostatic`` applied to each two-point layer (trapezoidal
    integration of ``Rd·Tv d(ln p)/g``), and a test pins it against MetPy
    layer-by-layer — but done here as one vectorised pass instead of 36 MetPy
    calls per frame, because this runs 49 times per request.

    ``pressure_to_height_std`` would be wrong: it is the standard atmosphere,
    not this column.
    """
    import metpy.constants as mpconsts

    p = column.p * units.hPa
    t = column.t * units.degC
    mixing_ratio = mpcalc.mixing_ratio_from_relative_humidity(
        p, t, mpcalc.relative_humidity_from_dewpoint(t, column.td * units.degC)
    )
    tv = np.asarray(mpcalc.virtual_temperature(t, mixing_ratio).to(units.kelvin).m, dtype=float)

    rd_over_g = float(mpconsts.Rd.m_as("J / (kg kelvin)")) / float(mpconsts.g.m_as("m / s**2"))
    layer_mean_tv = 0.5 * (tv[:-1] + tv[1:])
    thickness = rd_over_g * layer_mean_tv * np.log(column.p[:-1] / column.p[1:])
    heights = np.empty_like(column.p)
    heights[0] = 0.0
    heights[1:] = np.cumsum(thickness)
    return heights


def _level_aligned(
    values: np.ndarray, source_index: np.ndarray, level_count: int, digits: int
) -> list[float | None]:
    """Scatter column values back onto the isobaric ladder, ``None`` elsewhere.

    Levels below ground (masked out of the column) and levels whose T/Td were
    nodata (dropped) both stay ``None``, so the client can keep indexing these
    arrays in parallel with ``levels_hPa``.
    """
    out: list[float | None] = [None] * level_count
    for position, level_index in enumerate(source_index):
        if level_index < 0 or level_index >= level_count:
            continue
        value = _finite(values[position])
        out[int(level_index)] = None if value is None else round(value, digits)
    return out


def _compute_profiles(
    mpcalc: Any, units: Any, column: _Column, level_count: int
) -> dict[str, Any] | None:
    """``{"tw", "theta_e", "height_m_agl", "surface_*"}`` for one frame.

    All three are computed on the SAME anchored column the indices use, then
    mapped back to the level ladder. A failure nulls the whole object rather
    than shipping a half-populated one — the overlays are all-or-nothing per
    frame and a partial set would be harder to reason about than none.
    """
    try:
        p = column.p * units.hPa
        t = column.t * units.degC
        td = column.td * units.degC
        tw = np.asarray(
            mpcalc.wet_bulb_temperature(p, t, td).to(units.degC).m, dtype=float
        ).reshape(-1)
        theta_e = np.asarray(
            mpcalc.equivalent_potential_temperature(p, t, td).to(units.kelvin).m, dtype=float
        ).reshape(-1)
        heights = _hypsometric_heights_m(mpcalc, units, column)
    except Exception:  # noqa: BLE001 - never raise out of this module
        logger.debug("Sounding overlay profiles failed", exc_info=True)
        return None

    if tw.shape != column.p.shape or theta_e.shape != column.p.shape:
        logger.debug("Sounding overlay profiles returned an unexpected shape")
        return None

    return {
        "tw": _level_aligned(tw, column.idx, level_count, 2),
        "theta_e": _level_aligned(theta_e, column.idx, level_count, 2),
        "height_m_agl": _level_aligned(heights, column.idx, level_count, 1),
        # The surface entry has no slot on the isobaric ladder, and the client
        # anchors its traces there: ship it explicitly rather than making the
        # client fabricate one. Surface height is 0 m AGL by definition.
        "surface_tw": _round_or_none(_finite(tw[0]), 2),
        "surface_theta_e": _round_or_none(_finite(theta_e[0]), 2),
    }


def compute_frame_indices(
    *,
    levels_hpa: Sequence[Any],
    t: Sequence[Any],
    td: Sequence[Any],
    u: Sequence[Any] | None = None,
    v: Sequence[Any] | None = None,
    surface: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``{"indices", "parcel", "profiles"}`` for one frame, each independently
    nullable.

    ``u``/``v`` are accepted (the caller already has them) but nothing reads
    them: the Phase 5 hodograph consumes the raw components client-side and
    only needs ``profiles["height_m_agl"]`` from here.

    Never raises.
    """
    empty: dict[str, Any] = {"indices": None, "parcel": None, "profiles": None}
    surface = surface or {}

    try:
        column = build_column(levels_hpa, t=t, td=td, surface=surface)
    except Exception:  # noqa: BLE001
        logger.debug("Sounding column build failed", exc_info=True)
        return empty
    if column is None:
        return empty

    try:
        mpcalc, units = _metpy()
    except Exception:  # noqa: BLE001 - MetPy missing on this host
        logger.warning("MetPy unavailable; sounding indices disabled", exc_info=True)
        return empty

    p = column.p * units.hPa
    t_q = column.t * units.degC
    td_q = column.td * units.degC

    lcl_pressure_q: Any | None = None
    lcl_hpa: float | None = None
    lcl_c: float | None = None
    try:
        lcl_p, lcl_t = mpcalc.lcl(p[0], t_q[0], td_q[0])
        lcl_pressure_q = lcl_p
        lcl_hpa = _scalar(lcl_p.to(units.hPa))
        lcl_c = _scalar(lcl_t.to(units.degC))
    except Exception:  # noqa: BLE001
        logger.debug("LCL failed", exc_info=True)

    parcel_q: Any | None = None
    try:
        parcel_q = mpcalc.parcel_profile(p, t_q[0], td_q[0]).to(units.degC)
    except Exception:  # noqa: BLE001
        logger.debug("Parcel profile failed", exc_info=True)

    sbcape = sbcin = None
    if parcel_q is not None and lcl_pressure_q is not None:
        sbcape, sbcin = _virtual_temperature_cape_cin(
            mpcalc, units, column, parcel_q, lcl_pressure_q
        )

    mlcape = mlcin = None
    try:
        ml_cape_q, ml_cin_q = mpcalc.mixed_layer_cape_cin(
            p, t_q, td_q, depth=MIXED_LAYER_DEPTH_HPA * units.hPa
        )
        mlcape, mlcin = _scalar(ml_cape_q), _scalar(ml_cin_q)
    except Exception:  # noqa: BLE001
        logger.debug("Mixed-layer CAPE/CIN failed", exc_info=True)

    lfc_hpa = el_hpa = None
    if parcel_q is not None:
        # A capped sounding legitimately has neither: MetPy returns NaN, which
        # `_scalar` turns into None.
        try:
            lfc_hpa = _scalar(
                mpcalc.lfc(p, t_q, td_q, parcel_temperature_profile=parcel_q)[0].to(units.hPa)
            )
        except Exception:  # noqa: BLE001
            logger.debug("LFC failed", exc_info=True)
        try:
            el_hpa = _scalar(
                mpcalc.el(p, t_q, td_q, parcel_temperature_profile=parcel_q)[0].to(units.hPa)
            )
        except Exception:  # noqa: BLE001
            logger.debug("EL failed", exc_info=True)

    pwat_mm = None
    try:
        pwat_mm = _scalar(mpcalc.precipitable_water(p, td_q).to(units.mm))
    except Exception:  # noqa: BLE001
        logger.debug("PWAT failed", exc_info=True)

    indices = {
        "sbcape": _round_or_none(sbcape, None),
        "sbcin": _round_or_none(sbcin, None),
        "mlcape": _round_or_none(mlcape, None),
        "mlcin": _round_or_none(mlcin, None),
        "lcl_hPa": _round_or_none(lcl_hpa, 1),
        "lcl_C": _round_or_none(lcl_c, 1),
        "lfc_hPa": _round_or_none(lfc_hpa, 1),
        "el_hPa": _round_or_none(el_hpa, 1),
        "pwat_mm": _round_or_none(pwat_mm, 1),
        # HRRR's own surface CAPE field — present only on format_version >= 2
        # stacks, and only ever a side-by-side diagnostic (decision #5).
        "model_sbcape": _round_or_none(_finite(surface.get("cape_sfc")), None),
    }
    parcel = _parcel_path(mpcalc, units, column) if parcel_q is not None else None
    profiles = _compute_profiles(mpcalc, units, column, len(levels_hpa))
    return {"indices": indices, "parcel": parcel, "profiles": profiles}
