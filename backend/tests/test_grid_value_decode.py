"""Layer 1 of the value-COG → grid-binary sampling migration (Phase A/G).

Pure-math encode/decode round-trip tests for ``_decode_values()``, the inverse
of ``_encode_values()``. Zero file I/O, zero sampling — this proves the packing
arithmetic in isolation so later layers (binary sampler parity, canary,
meteogram integration) can assume the decode primitive is correct.

Parameterized by model per the Phase G checklist (item 5): GFS (Phases A–F),
plus HRRR and NBM (Phase G static-readiness audit). Variable lists are derived
programmatically from ``_PACKING_BY_MODEL_VAR`` filtered by model — never
hardcoded — so a future packing-table addition is automatically covered.
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


# (model, var) parameterization across every packed variable of every model
# under test — derived from the packing table itself.
MODEL_VAR_PARAMS = [
    (model, var) for model in MODELS_UNDER_TEST for var in _vars_for_model(model)
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
