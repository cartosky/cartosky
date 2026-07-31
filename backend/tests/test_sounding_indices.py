"""Sounding indices / parcel tests (Skew-T design 2026-07-30, Phase 4).

The reproduction case is the spike's OKC column (HRRR 2026-07-30 12z F006,
35.477 N / −97.506 W, full-grid row 417 / col 899) — the same numbers
``skewt-spike/profile_okc_v2.json`` fed to ``step5_indices.py``, embedded here so
the test does not depend on a scratchpad that will not survive the session.

**Tolerances.** MetPy is unpinned in ``requirements.txt`` and its parcel/LCL
solvers change between minor releases: the reference values were produced on
1.7.1, this suite is developed against 1.5.1, and the two disagree by a few
J/kg. Tolerances below are therefore set to "a version bump must not go
unnoticed, a version bump must not break CI": ±25 J/kg on CAPE-like quantities,
±0.5 mm on PWAT, ±3 hPa on the LCL. A change larger than that is a real
regression, not solver drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import sounding_indices  # noqa: E402

# --- spike reference column ------------------------------------------------

LEVELS = [
    1000.0, 975.0, 950.0, 925.0, 900.0, 875.0, 850.0, 825.0, 800.0, 775.0,
    750.0, 725.0, 700.0, 675.0, 650.0, 625.0, 600.0, 575.0, 550.0, 525.0,
    500.0, 475.0, 450.0, 425.0, 400.0, 375.0, 350.0, 325.0, 300.0, 275.0,
    250.0, 225.0, 200.0, 175.0, 150.0, 125.0, 100.0,
]
T_C = [
    37.031, 35.522, 33.382, 30.852, 28.374, 25.922, 23.462, 21.275, 19.124,
    17.323, 15.957, 14.790, 13.299, 11.492, 9.451, 7.074, 4.598, 2.145,
    -0.117, -1.979, -3.780, -5.681, -7.973, -10.542, -13.275, -16.148,
    -19.329, -23.140, -27.638, -32.618, -38.116, -43.611, -50.094, -57.207,
    -64.652, -71.746, -73.034,
]
TD_C = [
    18.657, 17.309, 16.439, 15.978, 15.481, 14.986, 14.344, 12.851, 11.640,
    9.720, 6.793, 2.961, 0.744, 0.667, 1.729, 1.600, 0.697, -2.338, -5.400,
    -8.463, -11.025, -13.588, -17.525, -19.713, -21.400, -25.775, -34.463,
    -39.650, -41.525, -51.525, -54.588, -62.088, -71.213, -76.650, -79.338,
    -80.838, -81.025,
]
SURFACE = {"pres_sfc": 968.5, "t2m": 37.448, "td2m": 16.985, "u10m": -1.114, "v10m": 4.262}

# ``indices_v2.json`` "anchored" + "diagnostics" blocks, MetPy 1.7.1.
SPIKE = {
    "tv_sbcape": 489.7,
    "tv_sbcin": 0.0,
    "mlcape": 83.4,
    "mlcin": -101.4,
    "pwat_mm": 44.01,
    "lcl_hPa": 721.0,
    "lcl_C": 12.41,
}


def _compute(**overrides):
    kwargs = {
        "levels_hpa": LEVELS,
        "t": T_C,
        "td": TD_C,
        "u": None,
        "v": None,
        "surface": SURFACE,
    }
    kwargs.update(overrides)
    return sounding_indices.compute_frame_indices(**kwargs)


# ---------------------------------------------------------------------------
# Reproduction of the spike reference
# ---------------------------------------------------------------------------


def test_reproduces_the_spike_reference_indices() -> None:
    indices = _compute()["indices"]
    assert indices is not None

    assert indices["sbcape"] == pytest.approx(SPIKE["tv_sbcape"], abs=25)
    assert indices["sbcin"] == pytest.approx(SPIKE["tv_sbcin"], abs=25)
    assert indices["mlcape"] == pytest.approx(SPIKE["mlcape"], abs=25)
    assert indices["mlcin"] == pytest.approx(SPIKE["mlcin"], abs=25)
    assert indices["pwat_mm"] == pytest.approx(SPIKE["pwat_mm"], abs=0.5)
    assert indices["lcl_hPa"] == pytest.approx(SPIKE["lcl_hPa"], abs=3.0)
    assert indices["lcl_C"] == pytest.approx(SPIKE["lcl_C"], abs=0.5)


def test_sb_parcel_is_virtual_temperature_corrected_not_plain() -> None:
    """Decision #5: the shipped SBCAPE is the Tv-corrected one.

    The plain (non-corrected) surface-based value for this column is ~367 J/kg
    and the corrected one ~490. Asserting the gap is what pins the *identity* of
    the parcel — an accidental switch to ``surface_based_cape_cin`` would still
    pass a loose "is it a plausible CAPE" check.
    """
    import metpy.calc as mpcalc
    import numpy as np
    from metpy.units import units

    column = sounding_indices.build_column(LEVELS, t=T_C, td=TD_C, surface=SURFACE)
    assert column is not None
    plain_cape, _plain_cin = mpcalc.surface_based_cape_cin(
        column.p * units.hPa, column.t * units.degC, column.td * units.degC
    )
    plain = float(np.asarray(plain_cape.m).reshape(-1)[0])

    indices = _compute()["indices"]
    assert indices["sbcape"] > plain + 60
    assert indices["sbcape"] == pytest.approx(SPIKE["tv_sbcape"], abs=25)


def test_anchored_column_drops_below_ground_levels() -> None:
    column = sounding_indices.build_column(LEVELS, t=T_C, td=TD_C, surface=SURFACE)
    assert column is not None
    # Surface first, then 35 of the 37 levels (1000 and 975 are below ground).
    assert column.p[0] == pytest.approx(968.5)
    assert len(column.p) == 36
    assert all(column.p[i] > column.p[i + 1] for i in range(len(column.p) - 1))
    assert 1000.0 not in list(column.p)
    assert 975.0 not in list(column.p)


def test_lfc_and_el_are_real_pressures_for_this_column() -> None:
    indices = _compute()["indices"]
    assert indices["lfc_hPa"] is not None
    assert indices["el_hPa"] is not None
    # LFC below the EL, both inside the column.
    assert indices["el_hPa"] < indices["lfc_hPa"] <= indices["lcl_hPa"] + 1.0
    assert 100.0 <= indices["el_hPa"] <= 968.5


# ---------------------------------------------------------------------------
# Parcel path
# ---------------------------------------------------------------------------


def test_parcel_path_starts_at_the_surface_and_includes_the_lcl() -> None:
    computed = _compute()
    parcel = computed["parcel"]
    indices = computed["indices"]
    assert parcel is not None

    assert len(parcel["p"]) == len(parcel["t"])
    # 36 anchored points + the inserted LCL.
    assert len(parcel["p"]) == 37
    assert parcel["p"][0] == pytest.approx(968.5, abs=0.1)
    assert parcel["t"][0] == pytest.approx(37.4, abs=0.2)
    assert all(parcel["p"][i] >= parcel["p"][i + 1] for i in range(len(parcel["p"]) - 1))
    # The LCL pressure is one of the sampled points.
    assert min(abs(p - indices["lcl_hPa"]) for p in parcel["p"]) < 0.2
    # Dry below the LCL is steeper than moist above it.
    assert parcel["t"][-1] < parcel["t"][0]


def test_parcel_path_is_small_enough_to_ship_per_frame() -> None:
    parcel = _compute()["parcel"]
    # One sample per stack level plus the LCL — no resampled giant array.
    assert len(parcel["p"]) <= len(LEVELS) + 2


# ---------------------------------------------------------------------------
# Capped sounding: no LFC / EL
# ---------------------------------------------------------------------------


def test_capped_sounding_has_no_lfc_or_el_but_still_reports_lcl_and_pwat() -> None:
    """A strong inversion with a dry surface parcel: nothing ever gets buoyant.

    The parcel must never be forced to find an LFC — ``None`` is the correct,
    displayable answer (design §7 Phase 4).
    """
    surface = {"pres_sfc": 968.5, "t2m": 10.0, "td2m": -10.0}
    # Warm nose right above the ground, then a normal lapse rate.
    t = []
    for level in LEVELS:
        if level >= 900.0:
            t.append(24.0)
        else:
            t.append(24.0 - (900.0 - level) * 0.055)
    td = [value - 25.0 for value in t]

    computed = sounding_indices.compute_frame_indices(
        levels_hpa=LEVELS, t=t, td=td, surface=surface
    )
    indices = computed["indices"]
    assert indices is not None
    assert indices["lfc_hPa"] is None
    assert indices["el_hPa"] is None
    assert indices["sbcape"] == 0
    assert indices["lcl_hPa"] is not None
    assert indices["pwat_mm"] is not None
    assert computed["parcel"] is not None


# ---------------------------------------------------------------------------
# Null / nodata handling — the module must never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "surface",
    [
        {},
        {"pres_sfc": None, "t2m": 30.0, "td2m": 20.0},
        {"pres_sfc": 968.5, "t2m": None, "td2m": 20.0},
        {"pres_sfc": 968.5, "t2m": 30.0, "td2m": None},
        {"pres_sfc": float("nan"), "t2m": 30.0, "td2m": 20.0},
        # Nonsense pressure from a nodata-ish decode.
        {"pres_sfc": 40.0, "t2m": 30.0, "td2m": 20.0},
    ],
)
def test_missing_surface_inputs_yield_null_indices_not_an_exception(surface: dict) -> None:
    computed = _compute(surface=surface)
    assert computed == {"indices": None, "parcel": None, "profiles": None}


def test_all_null_levels_yield_null_indices() -> None:
    computed = _compute(t=[None] * len(LEVELS), td=[None] * len(LEVELS))
    assert computed == {"indices": None, "parcel": None, "profiles": None}


def test_a_single_null_level_is_dropped_and_the_frame_still_computes() -> None:
    t = list(T_C)
    td = list(TD_C)
    t[20] = None
    td[20] = None
    indices = _compute(t=t, td=td)["indices"]
    assert indices is not None
    # 500 hPa gone barely moves the integral.
    assert indices["pwat_mm"] == pytest.approx(SPIKE["pwat_mm"], abs=1.0)


def test_short_arrays_and_wrong_types_do_not_raise() -> None:
    assert _compute(t=[], td=[]) == {"indices": None, "parcel": None, "profiles": None}
    assert _compute(levels_hpa=[]) == {"indices": None, "parcel": None, "profiles": None}
    assert _compute(t=["x"] * len(LEVELS), td=["y"] * len(LEVELS)) == {
        "indices": None,
        "parcel": None,
        "profiles": None,
    }
    assert sounding_indices.compute_frame_indices(
        levels_hpa=LEVELS, t=T_C, td=TD_C, surface=None
    ) == {"indices": None, "parcel": None, "profiles": None}


def test_supersaturated_dewpoint_from_quantization_is_clamped() -> None:
    """Td a hundredth above T is a rounding artefact, not an error."""
    td = [value + 0.01 for value in T_C]
    computed = _compute(td=td)
    assert computed["indices"] is not None
    assert computed["indices"]["pwat_mm"] is not None


# ---------------------------------------------------------------------------
# model_sbcape passthrough (format_version 2)
# ---------------------------------------------------------------------------


def test_model_sbcape_is_null_without_a_v2_surface_block() -> None:
    assert _compute()["indices"]["model_sbcape"] is None


def test_model_sbcape_is_reported_when_the_stack_carries_it() -> None:
    surface = dict(SURFACE, cape_sfc=966.4)
    indices = _compute(surface=surface)["indices"]
    assert indices["model_sbcape"] == 966
    # It never changes our own parcel (decision #5).
    assert indices["sbcape"] == pytest.approx(SPIKE["tv_sbcape"], abs=25)


def test_parcel_definition_names_the_correction() -> None:
    text = sounding_indices.PARCEL_DEFINITION
    assert "2 m" in text
    assert "virtual" in text.lower()


# ---------------------------------------------------------------------------
# Phase 5 overlay profiles (tw / theta_e / height_m_agl)
# ---------------------------------------------------------------------------


def test_profiles_are_aligned_to_the_level_ladder_with_nulls_below_ground() -> None:
    """The client indexes these in parallel with ``levels_hPa``.

    They are *computed* on the anchored column (surface first, below-ground
    levels dropped) but must be *shipped* on the full ladder, or every
    consumer would need its own copy of the anchoring rules.
    """
    profiles = _compute()["profiles"]
    assert profiles is not None

    for key in ("tw", "theta_e", "height_m_agl"):
        assert len(profiles[key]) == len(LEVELS), key
        # 1000 and 975 hPa are below the 968.5 hPa ground.
        assert profiles[key][0] is None, key
        assert profiles[key][1] is None, key
        assert all(value is not None for value in profiles[key][2:]), key


def test_profiles_carry_the_surface_anchor_values() -> None:
    profiles = _compute()["profiles"]
    # The surface entry has no slot on the isobaric ladder.
    assert profiles["surface_tw"] == pytest.approx(22.8, abs=0.5)
    assert profiles["surface_theta_e"] == pytest.approx(353.0, abs=1.0)
    # Surface height AGL is 0 by definition and is therefore not shipped.
    assert "surface_height_m_agl" not in profiles


def test_wet_bulb_never_exceeds_temperature_and_matches_metpy() -> None:
    """Tw sits between Td and T at every level — the physical sanity gate."""
    import metpy.calc as mpcalc
    import numpy as np
    from metpy.units import units

    profiles = _compute()["profiles"]
    column = sounding_indices.build_column(LEVELS, t=T_C, td=TD_C, surface=SURFACE)
    reference = np.asarray(
        mpcalc.wet_bulb_temperature(
            column.p * units.hPa, column.t * units.degC, column.td * units.degC
        )
        .to(units.degC)
        .m,
        dtype=float,
    )

    checked = 0
    for index, level in enumerate(LEVELS):
        tw = profiles["tw"][index]
        if tw is None:
            continue
        assert TD_C[index] - 0.05 <= tw <= T_C[index] + 0.05, level
        # Column position: +1 for the prepended surface entry, -2 for the two
        # below-ground levels.
        assert tw == pytest.approx(float(reference[index - 1]), abs=0.02)
        checked += 1
    assert checked == len(LEVELS) - 2


def test_theta_e_matches_metpy_and_is_a_plausible_kelvin_range() -> None:
    import metpy.calc as mpcalc
    import numpy as np
    from metpy.units import units

    profiles = _compute()["profiles"]
    column = sounding_indices.build_column(LEVELS, t=T_C, td=TD_C, surface=SURFACE)
    reference = np.asarray(
        mpcalc.equivalent_potential_temperature(
            column.p * units.hPa, column.t * units.degC, column.td * units.degC
        )
        .to(units.kelvin)
        .m,
        dtype=float,
    )
    values = [value for value in profiles["theta_e"] if value is not None]
    # Kelvin, not Celsius — a unit slip here would be invisible on the chart.
    assert all(250.0 < value < 400.0 for value in values)
    for index in range(2, len(LEVELS)):
        assert profiles["theta_e"][index] == pytest.approx(float(reference[index - 1]), abs=0.02)


def test_heights_are_hypsometric_monotonic_and_standard_atmosphere_plausible() -> None:
    """Design §1: heights are *reconstructed*, not read from the stack.

    ``pressure_to_height_std`` would answer the standard atmosphere rather than
    this column, so the integration is pinned against MetPy's own
    ``thickness_hydrostatic`` applied layer by layer.
    """
    import metpy.calc as mpcalc
    from metpy.units import units

    profiles = _compute()["profiles"]
    heights = [value for value in profiles["height_m_agl"] if value is not None]
    assert all(heights[i] < heights[i + 1] for i in range(len(heights) - 1))
    assert heights[0] > 0.0

    # ~5.6 km to 500 hPa for a column whose ground is already at ~380 m.
    assert profiles["height_m_agl"][LEVELS.index(500.0)] == pytest.approx(5600.0, rel=0.05)

    column = sounding_indices.build_column(LEVELS, t=T_C, td=TD_C, surface=SURFACE)
    p = column.p * units.hPa
    t = column.t * units.degC
    mixing_ratio = mpcalc.mixing_ratio_from_relative_humidity(
        p, t, mpcalc.relative_humidity_from_dewpoint(t, column.td * units.degC)
    )
    running = 0.0
    for i in range(len(column.p) - 1):
        running += float(
            mpcalc.thickness_hydrostatic(
                p[i : i + 2], t[i : i + 2], mixing_ratio=mixing_ratio[i : i + 2]
            )
            .to(units.m)
            .m
        )
        assert heights[i] == pytest.approx(running, abs=0.5)


def test_a_hole_in_the_ladder_nulls_only_that_level() -> None:
    hole = 10
    t = list(T_C)
    t[hole] = None
    profiles = _compute(t=t)["profiles"]
    assert profiles["tw"][hole] is None
    assert profiles["theta_e"][hole] is None
    assert profiles["height_m_agl"][hole] is None
    # Neighbours survive; the integration simply spans the gap.
    assert profiles["height_m_agl"][hole - 1] is not None
    assert profiles["height_m_agl"][hole + 1] is not None


def test_profiles_are_nulled_wholesale_when_metpy_fails(monkeypatch) -> None:
    """Containment: an overlay failure must never cost the indices."""
    import metpy.calc as mpcalc

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic MetPy failure")

    monkeypatch.setattr(mpcalc, "wet_bulb_temperature", _boom)
    computed = _compute()
    assert computed["profiles"] is None
    assert computed["indices"] is not None
    assert computed["parcel"] is not None
