"""Phase C gate-agreement test: pre-encode sanity gate vs COG sanity gate.

Naturally-occurring bad frames are too rare to observe passively (zero
rejections from either gate across the full multi-day GFS canary window), so
this test feeds both gates deliberately bad synthetic arrays and requires
their accept/reject decisions to agree exactly.

Both production gates delegate to the shared ``_check_value_array_sanity()``
helper in ``builder/pipeline.py`` — confirmed by reading both wrappers:

- ``check_pre_encode_value_sanity(values, ...)`` (the new Phase C gate) is a
  pure passthrough that only sets ``gate_name="Pre-encode value sanity"``.
- ``check_value_sanity(val_path, ...)`` (the existing COG gate) adds exactly
  one behavior beyond the passthrough: ``rasterio.open(val_path).read(1)``.
  Its other differences (``gate_name="Value COG"``, ``pass_name``) feed log
  strings only — nothing in the pass/fail logic reads them.

So the old gate's decision logic is exercised here by calling
``_check_value_array_sanity()`` directly with the exact arguments
``check_value_sanity()`` passes after its file read, and the new gate through
its real public function — identical arrays, no file I/O. The COG-specific
*structural* layer (``validate_cog()``: CRS/tiling/overviews/dtype) is a
separate Gate 1 and intentionally out of scope: it checks file structure, not
values, and has its own binary-side counterpart (``validate_grid_binary_frame``).

Case thresholds below are derived from the real logic in
``_check_value_array_sanity()``, not guessed:

- empty array -> hard fail
- nodata ratio > 0.95 -> fail (categorical-ptype specs relax this to 0.998,
  and an entirely-NaN categorical-ptype frame is explicitly allowed as "dry")
- flat field (min == max) -> fail, unless the spec allows dry frames
  (``allow_dry_frame`` and value at/below the first discrete level, with a
  missing levels list meaning any flat value passes)
- values outside spec ``range`` +/- 20% -> WARNING ONLY, never a rejection —
  the out-of-range case here pins that both gates agree on *accepting* it
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_gate_agreement_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.services.builder.pipeline import (
    _check_value_array_sanity,
    _resolve_model_var_capability,
    _resolve_model_var_spec,
    check_pre_encode_value_sanity,
)
from app.services.colormaps import get_color_map_spec

# 40 x 50 = 2000 pixels: NaN counts below map to exact ratios around the real
# thresholds (0.95 general, 0.998 categorical-ptype).
SHAPE = (40, 50)
TOTAL = SHAPE[0] * SHAPE[1]


def _gfs_spec_triple(var: str) -> tuple[dict[str, Any], Any, Any]:
    """The exact (var_spec, var_spec_model, var_capability) triple the
    pipeline passes to both gates for a real GFS variable."""
    var_spec_model = _resolve_model_var_spec("gfs", var)
    var_capability = _resolve_model_var_capability("gfs", var)
    var_spec = get_color_map_spec(var_capability.color_map_id)
    return var_spec, var_spec_model, var_capability


def _gradient(low: float, high: float) -> np.ndarray:
    return np.linspace(low, high, TOTAL, dtype=np.float32).reshape(SHAPE)


def _with_leading_nans(values: np.ndarray, nan_count: int) -> np.ndarray:
    flat = values.astype(np.float32).ravel().copy()
    flat[:nan_count] = np.nan
    return flat.reshape(SHAPE)


# (case_name, gfs_var, array_builder, expected_pass)
#
# expected_pass pins the current behavior of the shared logic so the
# agreement assertion cannot be trivially satisfied by both gates degrading
# into always-True (or always-False) together.
CASES: list[tuple[str, str, Any, bool]] = [
    # Control: realistic winter/spring 2m temperatures with a plausible
    # ocean-mask worth of nodata. Both gates must agree on ACCEPT.
    ("control_valid_tmp2m", "tmp2m", lambda: _with_leading_nans(_gradient(20.0, 80.0), TOTAL // 10), True),
    # Entirely NaN -> nodata ratio 1.0 > 0.95 -> reject.
    ("all_nan_tmp2m", "tmp2m", lambda: np.full(SHAPE, np.nan, dtype=np.float32), False),
    # 1920/2000 NaN = 0.96, just above the 0.95 threshold -> reject.
    ("nan_just_above_threshold_tmp2m", "tmp2m", lambda: _with_leading_nans(_gradient(20.0, 80.0), 1920), False),
    # 1880/2000 NaN = 0.94, just below the threshold -> accept (the other
    # side of the same boundary, so the threshold itself is pinned).
    ("nan_just_below_threshold_tmp2m", "tmp2m", lambda: _with_leading_nans(_gradient(20.0, 80.0), 1880), True),
    # Degenerate all-identical field; tmp2m has no allow_dry_frame -> reject.
    ("flat_constant_tmp2m", "tmp2m", lambda: np.full(SHAPE, 55.0, dtype=np.float32), False),
    # Wildly outside tmp2m's spec range (-60..120 F +/- 20%). The real logic
    # treats spec-range violations as a WARNING, not a rejection, so both
    # gates must agree on ACCEPT here — this case exists to catch either gate
    # becoming stricter than the other on range.
    ("far_out_of_range_tmp2m", "tmp2m", lambda: _gradient(5000.0, 6000.0), True),
    # Zero pixels -> hard reject.
    ("empty_array_tmp2m", "tmp2m", lambda: np.zeros((0, 0), dtype=np.float32), False),
    # precip_total's spec sets allow_dry_frame=True with no discrete levels
    # list -> a flat (dry) frame is explicitly allowed.
    ("dry_flat_precip_total_allowed", "precip_total", lambda: np.zeros(SHAPE, dtype=np.float32), True),
    # Categorical ptype: an entirely-NaN frame is the explicit "dry scene"
    # exception -> accept.
    ("ptype_all_nan_dry_allowed", "ptype_intensity", lambda: np.full(SHAPE, np.nan, dtype=np.float32), True),
    # Categorical ptype sparse-but-not-empty: 1997/2000 NaN = 0.9985, just
    # above the relaxed 0.998 threshold with finite pixels present -> reject.
    (
        "ptype_sparse_above_relaxed_threshold",
        "ptype_intensity",
        lambda: _with_leading_nans(np.tile(np.array([2.0, 3.0], dtype=np.float32), TOTAL // 2).reshape(SHAPE), 1997),
        False,
    ),
    # 1994/2000 NaN = 0.997, just below the relaxed threshold, non-flat
    # finite values -> accept.
    (
        "ptype_sparse_below_relaxed_threshold",
        "ptype_intensity",
        lambda: _with_leading_nans(np.tile(np.array([2.0, 3.0], dtype=np.float32), TOTAL // 2).reshape(SHAPE), 1994),
        True,
    ),
]


@pytest.mark.parametrize(("case_name", "var", "build", "expected_pass"), CASES, ids=[c[0] for c in CASES])
def test_gate_agreement(case_name: str, var: str, build: Any, expected_pass: bool) -> None:
    var_spec, var_spec_model, var_capability = _gfs_spec_triple(var)
    values = build()

    new_gate = check_pre_encode_value_sanity(
        values,
        var_spec,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        label=f"gate-agreement:{case_name}",
    )
    # The COG gate's decision logic, minus only the rasterio file read (see
    # module docstring): identical arguments to check_value_sanity()'s
    # delegation.
    old_gate = _check_value_array_sanity(
        values,
        var_spec,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        label=f"gate-agreement:{case_name}",
        gate_name="Value COG",
        pass_name="Value sanity",
    )

    assert new_gate == old_gate, (
        f"GATE DISAGREEMENT on {case_name} (gfs/{var}): "
        f"pre-encode gate={'PASS' if new_gate else 'REJECT'} vs "
        f"COG gate={'PASS' if old_gate else 'REJECT'} — Phase C parallel-gate "
        f"evidence is NOT satisfied for this failure class."
    )
    assert new_gate == expected_pass, (
        f"Both gates agree on {case_name} (gfs/{var}) but decided "
        f"{'PASS' if new_gate else 'REJECT'}, expected "
        f"{'PASS' if expected_pass else 'REJECT'} — the shared sanity logic "
        f"changed behavior for this failure class."
    )
