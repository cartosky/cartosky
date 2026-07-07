"""Layer 1 of the value-COG → grid-binary sampling migration (Phase A/G).

Pure-math encode/decode round-trip tests for ``_decode_values()``, the inverse
of ``_encode_values()``. Zero file I/O, zero sampling — this proves the packing
arithmetic in isolation so later layers (binary sampler parity, canary,
meteogram integration) can assume the decode primitive is correct.

Parameterized by model per the Phase G checklist (item 5): GFS (Phases A–F),
plus HRRR and NBM (Phase G static-readiness audit), plus GEFS and EPS (the
ensemble Phase G audit). Variable lists are derived programmatically from
``_PACKING_BY_MODEL_VAR`` filtered by model — never hardcoded — so a future
packing-table addition is automatically covered. For the ensemble models the
list is additionally filtered through the canary's own scope logic: GEFS/EPS
publish exclusively under runtime ``__mean`` artifact ids, so their packed
scope contains bare-id dead aliases that are never independently written to
disk — no decode round-trip exists for those, and testing them would validate
dead code paths, not real behavior.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.grid import (
    GRID_DTYPE,
    GRID_DTYPE_UINT8,
    _decode_values,
    _encode_values,
    _PACKING_BY_MODEL_VAR,
    grid_dtype,
)

# Models covered by Layers 1-4 so far: GFS completed Phases A-F; HRRR and NBM
# are in Phase G (checklist item 5). Model names appear only here and in
# test-data assertions below — never in helper logic.
MODELS_UNDER_TEST = ("gfs", "hrrr", "nbm")

# Precip-anomaly variables that an earlier scoping pass missed because they are
# registered via a loop, not as literal dict entries. Asserted present below so
# the methodology gap that dropped them can never silently recur.
GFS_PRECIP_ANOM_VARS = (
    "precip_5d_anom",
    "precip_7d_anom",
    "precip_10d_anom",
    "precip_16d_anom",
)


def _vars_for_model(model: str) -> list[str]:
    return sorted(
        var for (mdl, var) in _PACKING_BY_MODEL_VAR if mdl == model
    )


def _canary_scope_vars(model: str) -> list[str]:
    """Packed variables the canary actually compares for ``model`` —
    ``_PACKING_BY_MODEL_VAR`` keys intersected with the canary's own
    ``_split_scope_by_buildable`` logic (via ``_scope_for_model``), so this
    parameterization cannot silently drift from what Layer 3 exercises."""
    from backend.scripts.canary_binary_sampler import _scope_for_model

    return list(_scope_for_model(model)[0])


# Ensemble models from the "Phase G audit — GEFS and EPS static readiness"
# section. Their scope is canary-filtered (see _canary_scope_vars), unlike the
# MODELS_UNDER_TEST tuple whose full packed lists are all real artifacts.
ENSEMBLE_MODELS_UNDER_TEST = ("gefs", "eps")

# Poll-driven standalone publishers (NDFD, WPC): minute-stamped run ids,
# dedicated publish modules (ndfd_publish/wpc_publish, not the scheduler
# pipeline), zero display-prep entries (all Group 1). Scope is derived through
# the same canary intersection as the ensemble models even though the audit
# found every exclusion bucket empty for both — asserted below — so a future
# catalog change cannot silently drift from what the canary compares.
PUBLISHER_MODELS_UNDER_TEST = ("ndfd", "wpc")

# The audited write-path-dead bare aliases per ensemble model (packed and
# catalog-buildable, but runtime var-id resolution redirects every build to
# the __mean twin, so no frame is ever written under these ids). Pinned so an
# unaudited catalog change fails loudly instead of silently narrowing the
# parameterized coverage below.
ENSEMBLE_DEAD_ALIAS_VARS = {
    "gefs": {
        "hgt500_anom", "precip_10d_anom", "precip_16d_anom", "precip_5d_anom",
        "precip_7d_anom", "tmp2m_anom", "tmp850_anom",
    },
    "eps": {
        "hgt500_anom", "precip_10d_anom", "precip_15d_anom", "precip_5d_anom",
        "precip_7d_anom", "tmp2m_anom", "tmp850_anom",
    },
}


# (model, var) parameterization across every packed variable of every model
# under test — derived from the packing table itself (canary-scope-filtered
# for the ensemble models).
MODEL_VAR_PARAMS = [
    (model, var) for model in MODELS_UNDER_TEST for var in _vars_for_model(model)
] + [
    (model, var)
    for model in ENSEMBLE_MODELS_UNDER_TEST
    for var in _canary_scope_vars(model)
] + [
    (model, var)
    for model in PUBLISHER_MODELS_UNDER_TEST
    for var in _canary_scope_vars(model)
]


def _packing(model: str, var: str) -> dict:
    return _PACKING_BY_MODEL_VAR[(model, var)]


def _representable_max(scale: float, offset: float, nodata: int) -> float:
    # Code (nodata - 1) is the largest non-sentinel code.
    return offset + (nodata - 1) * scale


def test_gfs_scope_includes_loop_registered_precip_anomaly_vars() -> None:
    """Guard the exact correction from the migration plan: the four loop-
    registered precip-anomaly variables must be in the enumerated GFS scope."""
    for var in GFS_PRECIP_ANOM_VARS:
        assert ("gfs", var) in _PACKING_BY_MODEL_VAR, (
            f"gfs/{var} missing from _PACKING_BY_MODEL_VAR — scope regression"
        )


@pytest.mark.parametrize("model", MODELS_UNDER_TEST)
def test_model_scope_is_nonempty(model: str) -> None:
    """A packing-table refactor must never silently empty a model's scope —
    an empty list would make every parameterized test below vacuously pass."""
    assert _vars_for_model(model), f"no packed variables found for {model}"


@pytest.mark.parametrize(("model", "var"), MODEL_VAR_PARAMS)
def test_in_range_values_round_trip_within_half_scale(model: str, var: str) -> None:
    """In-range (non-clipping) values decode back within scale/2 of the input.

    Values are generated from each variable's own packing band, so they are
    physically representative per variable: for negative-offset packings
    (e.g. hrrr vort500 at offset=-100, hrrr tmp850_anom at offset=-80) the
    aligned values start below zero and exercise the signed range.
    """
    packing = _packing(model, var)
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    lo = offset
    hi = _representable_max(scale, offset, nodata)
    # Stay strictly inside the representable band so nothing clips. Mix exactly
    # representable values (offset + k*scale) with midway values that exercise
    # the round-to-nearest path (worst-case error = scale/2).
    span = hi - lo
    aligned = lo + np.arange(0, 6) * scale
    midway = np.linspace(lo + span * 0.05, lo + span * 0.95, 7)
    values = np.concatenate([aligned, midway]).astype(np.float32)

    encoded = _encode_values(
        values, scale=scale, offset=offset, nodata=nodata, dtype=dtype
    )
    decoded = _decode_values(encoded, model=model, var=var)

    # scale/2 is the exact arithmetic bound; the small epsilon absorbs float32
    # representation error on large-magnitude values/offsets.
    tol = scale / 2 + 1e-4
    assert np.all(np.abs(decoded - values) <= tol), (
        f"{model}/{var}: round-trip exceeded {tol}\n"
        f"values={values}\ndecoded={decoded}\ndiff={np.abs(decoded - values)}"
    )


@pytest.mark.parametrize(("model", "var"), MODEL_VAR_PARAMS)
def test_out_of_range_values_decode_to_clipped_result(model: str, var: str) -> None:
    """Deliberately out-of-range values must decode to the CLIPPED result, not
    round-trip near the original input (the clip-boundary correction)."""
    packing = _packing(model, var)
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    hi = _representable_max(scale, offset, nodata)
    # One value far above the representable max (clips to code nodata-1) and one
    # far below the min (clips to code 0).
    over = hi + 1000.0 * max(scale, 1.0)
    under = offset - 1000.0 * max(scale, 1.0)
    values = np.array([over, under], dtype=np.float32)

    encoded = _encode_values(
        values, scale=scale, offset=offset, nodata=nodata, dtype=dtype
    )
    decoded = _decode_values(encoded, model=model, var=var)

    # Expected = decode of the clamped code, computed through the same float32
    # path _decode_values uses, so equality is exact.
    expected_codes = np.clip(
        np.rint((values.astype(np.float64) - offset) / scale), 0, nodata - 1
    )
    expected = (
        expected_codes.astype(np.float32) * np.float32(scale) + np.float32(offset)
    )
    assert np.array_equal(decoded, expected), (
        f"{model}/{var}: clipped decode mismatch\n"
        f"values={values}\ndecoded={decoded}\nexpected={expected}"
    )
    # And the clipped decode must NOT be close to the original (proves we are
    # actually exercising the clip path, not a value that happened to fit).
    assert not np.allclose(decoded, values)


@pytest.mark.parametrize(("model", "var"), MODEL_VAR_PARAMS)
def test_nodata_sentinel_decodes_to_nan(model: str, var: str) -> None:
    """Non-finite inputs encode to the nodata sentinel, which must decode to NaN."""
    packing = _packing(model, var)
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    values = np.array([np.nan, np.inf, -np.inf], dtype=np.float32)
    encoded = _encode_values(
        values, scale=scale, offset=offset, nodata=nodata, dtype=dtype
    )
    assert np.all(encoded == nodata)

    decoded = _decode_values(encoded, model=model, var=var)
    assert np.all(np.isnan(decoded))


@pytest.mark.parametrize(("model", "var"), MODEL_VAR_PARAMS)
def test_decode_preserves_shape(model: str, var: str) -> None:
    """Decode must preserve the input array shape (2-D grids)."""
    packing = _packing(model, var)
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    base = offset + (nodata // 4) * scale
    values = np.full((4, 5), base, dtype=np.float32)
    values[0, 0] = np.nan  # sentinel mixed in
    encoded = _encode_values(
        values, scale=scale, offset=offset, nodata=nodata, dtype=dtype
    )
    decoded = _decode_values(encoded, model=model, var=var)
    assert decoded.shape == values.shape
    assert np.isnan(decoded[0, 0])


@pytest.mark.parametrize("model", ENSEMBLE_MODELS_UNDER_TEST)
def test_ensemble_scope_partitions_cleanly_and_pins_dead_aliases(model: str) -> None:
    """The canary's three scope buckets must partition the packing table, and
    the dead-alias bucket must equal the audited set — so an unaudited catalog
    or packing change fails loudly here rather than silently narrowing (or
    widening) the parameterized decode coverage above."""
    from backend.scripts.canary_binary_sampler import _scope_for_model

    (
        in_scope,
        excluded_non_buildable,
        excluded_dead_alias,
        excluded_uncataloged,
    ) = _scope_for_model(model)
    packed = set(_vars_for_model(model))

    # Every packed entry lands in exactly one bucket — nothing silently dropped.
    assert (
        set(in_scope)
        | set(excluded_non_buildable)
        | set(excluded_dead_alias)
        | set(excluded_uncataloged)
    ) == packed
    assert set(in_scope).isdisjoint(excluded_dead_alias)
    assert set(in_scope).isdisjoint(excluded_non_buildable)
    # Both ensemble models' packed entries are fully cataloged (the
    # uncataloged bucket exists for cross-model packing-loop strays, e.g.
    # ecmwf's precip_16d_anom).
    assert excluded_uncataloged == []

    # The dead-alias set is exactly what the Phase G audit established.
    assert set(excluded_dead_alias) == ENSEMBLE_DEAD_ALIAS_VARS[model]

    # And the Layer 1 parameterization covers exactly the canary scope.
    covered = {var for (mdl, var) in MODEL_VAR_PARAMS if mdl == model}
    assert covered == set(in_scope)
    assert covered, f"no parameterized variables for {model}"


def test_gefs_and_eps_packed_variables_are_all_uint16() -> None:
    """Phase G ensemble-audit invariant: every GEFS and EPS packed entry —
    including the dead-alias bare ids excluded from decode parameterization —
    is uint16. A future uint8 addition must be a deliberate, audited change."""
    for model in ENSEMBLE_MODELS_UNDER_TEST:
        for var in _vars_for_model(model):
            packing = _packing(model, var)
            resolved = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
            assert resolved != GRID_DTYPE_UINT8, (
                f"{model}/{var} is uint8-packed — the ensemble Phase G audit "
                f"assumed all-uint16; re-audit before relying on these tests"
            )


def test_hrrr_and_nbm_packed_variables_are_all_uint16() -> None:
    """Phase G audit invariant: every HRRR and NBM packed variable is uint16
    (no uint8 packing for either model). A future uint8 addition must be a
    deliberate, audited change — this test forces that conversation."""
    for model in ("hrrr", "nbm"):
        for var in _vars_for_model(model):
            packing = _packing(model, var)
            resolved = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
            assert resolved != GRID_DTYPE_UINT8, (
                f"{model}/{var} is uint8-packed — Phase G audit assumed all-"
                f"uint16 for this model; re-audit before relying on these tests"
            )


@pytest.mark.parametrize("model", PUBLISHER_MODELS_UNDER_TEST)
def test_publisher_scope_has_no_exclusions_and_is_fully_covered(model: str) -> None:
    """NDFD/WPC audit pin: every exclusion bucket is empty for both standalone
    publishers — the canary scope IS the full packed list — and the Layer 1
    parameterization covers exactly that scope. Derived through the canary's
    own logic (not hardcoded) so a future catalog change that populates a
    bucket fails loudly here instead of silently narrowing coverage."""
    from backend.scripts.canary_binary_sampler import _scope_for_model

    (
        in_scope,
        excluded_non_buildable,
        excluded_dead_alias,
        excluded_uncataloged,
    ) = _scope_for_model(model)

    assert excluded_non_buildable == []
    assert excluded_dead_alias == []
    assert excluded_uncataloged == []
    assert sorted(in_scope) == _vars_for_model(model)

    covered = {var for (mdl, var) in MODEL_VAR_PARAMS if mdl == model}
    assert covered == set(in_scope)
    assert covered, f"no parameterized variables for {model}"


def test_ndfd_and_wpc_packed_variables_are_all_uint16() -> None:
    """Publisher audit invariant: every NDFD and WPC packed variable is uint16.
    A future uint8 addition must be a deliberate, audited change."""
    for model in PUBLISHER_MODELS_UNDER_TEST:
        for var in _vars_for_model(model):
            packing = _packing(model, var)
            resolved = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
            assert resolved != GRID_DTYPE_UINT8, (
                f"{model}/{var} is uint8-packed — the NDFD/WPC audit assumed "
                f"all-uint16; re-audit before relying on these tests"
            )


@pytest.mark.parametrize("var", ["mint", "maxt"])
def test_ndfd_temperature_negative_offset_round_trips_signed_values(var: str) -> None:
    """The signed-variable diligence every prior model's audit applied: NDFD's
    mint/maxt pack with offset=-100.0 (F), so sub-zero temperatures live in the
    low code range. Pin the offset and prove realistic negative values —
    including non-lattice ones that exercise round-to-nearest — decode back
    within scale/2."""
    packing = _packing("ndfd", var)
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    assert offset == -100.0
    assert scale == 0.1

    # CONUS record cold is about -70 F (Rogers Pass); mix lattice-aligned and
    # round-to-nearest values across the signed part of the band.
    values = np.array([-70.0, -40.0, -39.97, -0.5, -0.03, 0.0, 32.0], dtype=np.float32)
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
    encoded = _encode_values(values, scale=scale, offset=offset, nodata=nodata, dtype=dtype)
    decoded = _decode_values(encoded, model="ndfd", var=var)

    tol = scale / 2 + 1e-4
    assert np.all(np.abs(decoded - values) <= tol), (
        f"ndfd/{var}: signed round-trip exceeded {tol}\n"
        f"values={values}\ndecoded={decoded}"
    )
    # The negative inputs must decode negative — sign survives the packing.
    assert np.all(decoded[values < -tol] < 0)


# Realistic post-rollup extremes for NDFD's in-app rolling-sum (and rolling-max)
# variables. The packing/decode math is derivation-agnostic, but the fixtures
# must use magnitudes a real 4-6-frame rollup can reach — not raw single-frame
# values — because scale=0.1/0.01 headroom is consumed differently at rollup
# scale. Values are near the physical record for each window:
#   qpf_24h  ~43 in (Alvin, TX 1979 US 24h rainfall record)
#   qpf_48h  Harvey-scale multi-day totals
#   snow_24h ~75.8 in (Silver Lake, CO 1921 US 24h snowfall record)
#   snow_48h stacked lake-effect/Sierra events
#   wgust_24h_max ~231 mph (Mount Washington observed gust)
NDFD_ROLLUP_EXTREMES = {
    "qpf_24h": 42.0,
    "qpf_48h": 62.0,
    "snow_24h": 75.8,
    "snow_48h": 120.5,
    "wgust_24h_max": 231.0,
}


@pytest.mark.parametrize(("var", "extreme"), sorted(NDFD_ROLLUP_EXTREMES.items()))
def test_ndfd_rollup_variables_round_trip_at_realistic_rollup_magnitudes(
    var: str, extreme: float
) -> None:
    packing = _packing("ndfd", var)
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    # Headroom pin: the record-scale rollup value must sit comfortably inside
    # the representable band (2x margin), not just barely below the ceiling.
    hi = _representable_max(scale, offset, nodata)
    assert extreme * 2 <= hi, (
        f"ndfd/{var}: representable max {hi} leaves <2x headroom over the "
        f"realistic rollup extreme {extreme} — re-audit the packing"
    )

    # The extreme itself plus nearby non-lattice values (round-to-nearest at
    # rollup magnitude, where float32 ULP is far larger than at single-frame
    # magnitudes).
    values = np.array(
        [extreme, extreme - 0.37 * scale, extreme + 1.13 * scale, extreme / 2],
        dtype=np.float32,
    )
    encoded = _encode_values(values, scale=scale, offset=offset, nodata=nodata, dtype=dtype)
    decoded = _decode_values(encoded, model="ndfd", var=var)

    tol = scale / 2 + 1e-3
    assert np.all(np.abs(decoded - values) <= tol), (
        f"ndfd/{var}: rollup-magnitude round-trip exceeded {tol}\n"
        f"values={values}\ndecoded={decoded}\ndiff={np.abs(decoded - values)}"
    )


def test_wpc_precip_total_round_trips_at_seven_day_cumulative_extremes() -> None:
    """WPC publishes precip_total as a cumulative sum out to fh=168 (7 days),
    so the packing must round-trip at cumulative-total magnitudes, not just
    single-period ones. scale=0.01 / nodata=65535 gives ~655 in of headroom;
    the CONUS 7-day record is Harvey's ~60.6 in storm total — pin that the
    ceiling covers it with an order-of-magnitude margin, then round-trip
    values at and above the record."""
    packing = _packing("wpc", "precip_total")
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
    assert scale == 0.01
    assert nodata == 65535

    hi = _representable_max(scale, offset, nodata)
    assert hi == pytest.approx(655.34)
    harvey_storm_total = 60.58
    assert hi >= harvey_storm_total * 10  # order-of-magnitude margin

    # Record-scale and beyond-record cumulative totals, lattice and non-lattice.
    values = np.array(
        [harvey_storm_total, 62.37, 100.0, 150.03], dtype=np.float32
    )
    encoded = _encode_values(values, scale=scale, offset=offset, nodata=nodata, dtype=dtype)
    decoded = _decode_values(encoded, model="wpc", var="precip_total")

    tol = scale / 2 + 1e-3
    assert np.all(np.abs(decoded - values) <= tol), (
        f"wpc/precip_total: cumulative-extreme round-trip exceeded {tol}\n"
        f"values={values}\ndecoded={decoded}\ndiff={np.abs(decoded - values)}"
    )


def test_decode_branches_on_dtype_for_uint8_packed_variable() -> None:
    """Dtype branching: a uint8-packed variable (MRMS) must decode in the uint8
    domain — sentinel 255, codes interpreted as uint8 — proving the decode does
    not assume uint16 from day one (per the cross-model requirement)."""
    model, var = "mrms", "reflectivity"
    if (model, var) not in _PACKING_BY_MODEL_VAR:
        pytest.skip("mrms/reflectivity packing not present")
    packing = _packing(model, var)
    assert grid_dtype(str(packing.get("dtype"))) == GRID_DTYPE_UINT8

    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])  # 255 for uint8

    codes = np.array([0, 10, nodata - 1, nodata], dtype=np.uint8)
    decoded = _decode_values(codes, model=model, var=var)
    assert decoded.dtype == np.float32
    assert np.isnan(decoded[-1])
    expected = codes[:-1].astype(np.float32) * np.float32(scale) + np.float32(offset)
    assert np.allclose(decoded[:-1], expected)


def test_decode_rejects_wider_dtype_for_uint8_packed_variable() -> None:
    """A uint16 buffer passed for a uint8-packed variable must fail loudly.

    Casting this through uint8 would wrap values above 255 and silently corrupt
    the sample. That is the exact latent failure mode Phase G needs guarded.
    """
    model, var = "mrms", "reflectivity"
    if (model, var) not in _PACKING_BY_MODEL_VAR:
        pytest.skip("mrms/reflectivity packing not present")
    packing = _packing(model, var)
    assert grid_dtype(str(packing.get("dtype"))) == GRID_DTYPE_UINT8

    codes = np.array([0, 42, 300], dtype=np.uint16)
    with pytest.raises(ValueError, match="encoded array dtype uint16 is wider"):
        _decode_values(codes, model=model, var=var)


def test_decode_unsupported_pair_raises() -> None:
    with pytest.raises(ValueError):
        _decode_values(np.zeros((2, 2), dtype=np.uint16), model="gfs", var="not_a_real_var")
