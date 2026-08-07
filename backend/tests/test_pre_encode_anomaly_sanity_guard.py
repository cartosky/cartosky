"""Plausibility-envelope tests for the pre-encode value-sanity gate.

Anomaly colormaps are discrete, which exempts them from the gate's ±20%
physical-range check (``skip_physical_range_checks``) — before the
``sanity_range`` envelope existed, a Kelvin-magnitude field on a °C anomaly
ladder passed the gate with no warning and published silently (found during
the SST Phase 2 review; see docs/SST_SPIKE_2026-08-06.md).

These tests feed the live gate synthetic arrays through the exact
(var_spec, var_spec_model, var_capability) triples the publish paths use and
pin, per variable kind:

- anomaly (discrete + sanity_range): wrong-unit magnitudes -> hard fail;
  plausible fields and legitimate extremes beyond the display ladder -> pass
- continuous (no sanity_range): out-of-range stays WARNING ONLY — unchanged
- discrete non-anomaly (no sanity_range): gross values still pass — the
  envelope is opt-in per spec, not implied by discreteness
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
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_anomaly_sanity_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.models.sst import SST_MODEL
from app.services.builder.pipeline import (
    _resolve_model_var_capability,
    _resolve_model_var_spec,
    check_pre_encode_value_sanity,
)
from app.services.colormaps import anomaly_sanity_range, get_color_map_spec

SHAPE = (40, 50)
TOTAL = SHAPE[0] * SHAPE[1]


def _gfs_spec_triple(var: str) -> tuple[dict[str, Any], Any, Any]:
    var_spec_model = _resolve_model_var_spec("gfs", var)
    var_capability = _resolve_model_var_capability("gfs", var)
    var_spec = get_color_map_spec(var_capability.color_map_id)
    return var_spec, var_spec_model, var_capability


def _sst_spec_triple(var: str) -> tuple[dict[str, Any], Any, Any]:
    var_capability = SST_MODEL.get_var_capability(var)
    var_spec = get_color_map_spec(var_capability.color_map_id)
    return var_spec, SST_MODEL.get_var(var), var_capability


_TRIPLES = {
    "gfs": _gfs_spec_triple,
    "sst": _sst_spec_triple,
}


def _gradient(low: float, high: float) -> np.ndarray:
    return np.linspace(low, high, TOTAL, dtype=np.float32).reshape(SHAPE)


# (case_name, model, var, array_builder, expected_pass)
#
# The wrong-unit builders model the real bug class per variable: Kelvin
# magnitudes on °C/°F temperature anomalies, raw geopotential (m² s⁻², ≈9.8×)
# on the height anomaly, and millimeters on the inch precip anomaly.
CASES: list[tuple[str, str, str, Any, bool]] = [
    # --- anomaly kind: plausible in-ladder fields pass -----------------------
    ("tmp2m_anom_plausible", "gfs", "tmp2m_anom", lambda: _gradient(-25.0, 25.0), True),
    ("tmp850_anom_plausible", "gfs", "tmp850_anom", lambda: _gradient(-15.0, 15.0), True),
    ("hgt500_anom_plausible", "gfs", "hgt500_anom", lambda: _gradient(-400.0, 400.0), True),
    ("precip_5d_anom_plausible", "gfs", "precip_5d_anom", lambda: _gradient(-5.0, 5.0), True),
    ("sst_anom_plausible", "sst", "sst_anom", lambda: _gradient(-4.0, 4.0), True),
    # --- anomaly kind: legitimate extremes beyond the display ladder pass ----
    # The guard must catch unit errors without clipping real record events.
    ("tmp2m_anom_record_extreme", "gfs", "tmp2m_anom", lambda: _gradient(-60.0, 60.0), True),
    ("sst_anom_marine_heatwave", "sst", "sst_anom", lambda: _gradient(-3.0, 12.0), True),
    ("precip_anom_harvey_class", "gfs", "precip_5d_anom", lambda: _gradient(-5.0, 45.0), True),
    # Stalled-TC / monsoon-burst 16-day departures can exceed 100 in; one
    # out-of-envelope pixel rejects the whole frame, so these must pass.
    ("precip_anom_monsoon_burst_extreme", "gfs", "precip_16d_anom", lambda: _gradient(-20.0, 130.0), True),
    # Exactly at the envelope edge (±25 °C for sst_anom) is still inside.
    ("sst_anom_envelope_boundary", "sst", "sst_anom", lambda: _gradient(-25.0, 25.0), True),
    # --- anomaly kind: wrong-unit magnitudes fail closed ---------------------
    ("tmp2m_anom_kelvin_field", "gfs", "tmp2m_anom", lambda: _gradient(270.0, 300.0), False),
    ("tmp850_anom_kelvin_field", "gfs", "tmp850_anom", lambda: _gradient(250.0, 290.0), False),
    ("sst_anom_kelvin_field", "sst", "sst_anom", lambda: _gradient(270.0, 300.0), False),
    ("hgt500_anom_geopotential_field", "gfs", "hgt500_anom", lambda: _gradient(-3000.0, 4000.0), False),
    # Envelope is ±165 in (30×): mm confusion trips it during any major event.
    ("precip_anom_millimeter_field", "gfs", "precip_5d_anom", lambda: _gradient(-50.0, 250.0), False),
    # One-sided excursions trip it too (only vmax out of envelope).
    ("sst_anom_high_side_only", "sst", "sst_anom", lambda: _gradient(0.0, 40.0), False),
    # NaN pixels don't mask the violation: finite subset is still out of range.
    (
        "sst_anom_kelvin_with_nans",
        "sst",
        "sst_anom",
        lambda: np.where(np.arange(TOTAL).reshape(SHAPE) % 3 == 0, np.nan, _gradient(270.0, 300.0)).astype(
            np.float32
        ),
        False,
    ),
    # --- continuous kind: unchanged, out-of-range remains warning-only -------
    ("sst_continuous_gross_values_warn_only", "sst", "sst", lambda: _gradient(270.0, 300.0), True),
    ("tmp2m_continuous_gross_values_warn_only", "gfs", "tmp2m", lambda: _gradient(5000.0, 6000.0), True),
]


@pytest.mark.parametrize(("case_name", "model", "var", "build", "expected_pass"), CASES, ids=[c[0] for c in CASES])
def test_anomaly_plausibility_guard_decision(
    case_name: str,
    model: str,
    var: str,
    build: Any,
    expected_pass: bool,
) -> None:
    var_spec, var_spec_model, var_capability = _TRIPLES[model](var)
    values = build()

    decision = check_pre_encode_value_sanity(
        values,
        var_spec,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        label=f"anomaly-sanity:{case_name}",
    )

    assert decision == expected_pass, (
        f"Gate decided {'PASS' if decision else 'REJECT'} for "
        f"{case_name} ({model}/{var}), expected "
        f"{'PASS' if expected_pass else 'REJECT'}"
    )


def test_discrete_non_anomaly_specs_are_unaffected() -> None:
    """The envelope is opt-in via ``sanity_range``; a discrete non-anomaly
    spec (rh) without one must keep today's behavior — gross values pass."""
    var_spec = get_color_map_spec("rh")
    assert "sanity_range" not in var_spec

    decision = check_pre_encode_value_sanity(
        _gradient(2000.0, 9000.0),
        var_spec,
        var_spec_model=None,
        var_capability=None,
        label="anomaly-sanity:rh_gross_values_pass",
    )
    assert decision is True


def test_all_anomaly_colormaps_declare_sanity_range() -> None:
    """Every anomaly ladder must carry the envelope — a new anomaly colormap
    added without one recreates the silent-publish gap this guard closes."""
    anomaly_specs = ["tmp2m_anom", "tmp850_anom", "hgt500_anom", "precip_anom", "sst_anom"]
    for spec_id in anomaly_specs:
        spec = get_color_map_spec(spec_id)
        sanity_range = spec.get("sanity_range")
        assert sanity_range is not None and len(sanity_range) == 2, spec_id
        sane_min, sane_max = float(sanity_range[0]), float(sanity_range[1])
        spec_min, spec_max = (float(v) for v in spec["range"])
        # Generous: strictly wider than the display ladder on both sides.
        assert sane_min < spec_min and sane_max > spec_max, spec_id


def test_anomaly_sanity_range_helper() -> None:
    assert anomaly_sanity_range((-5.0, 5.0)) == (-25.0, 25.0)
    assert anomaly_sanity_range((-5.5, 5.5), factor=30.0) == (-165.0, 165.0)
    # Asymmetric ladders take the larger absolute endpoint.
    assert anomaly_sanity_range((-2.0, 10.0)) == (-50.0, 50.0)


@pytest.mark.parametrize(
    "bad_range",
    [
        5.0,                      # scalar, not a pair
        ("a", "b"),               # non-numeric
        (None, 5.0),              # None endpoint
        (float("nan"), float("nan")),  # non-finite must not silently disable
        (5.0, -5.0),              # inverted
        (3.0, 3.0),               # empty envelope
    ],
    ids=["scalar", "non_numeric", "none_endpoint", "nan", "inverted", "empty"],
)
def test_malformed_sanity_range_fails_closed_without_raising(bad_range: Any) -> None:
    """A typo'd envelope on a future anomaly ladder must reject the frame with
    a clear log line, not crash the build loop or silently disable the guard."""
    var_spec = dict(get_color_map_spec("sst_anom"))
    var_spec["sanity_range"] = bad_range

    decision = check_pre_encode_value_sanity(
        _gradient(-4.0, 4.0),
        var_spec,
        var_spec_model=None,
        var_capability=None,
        label="anomaly-sanity:malformed_sanity_range",
    )
    assert decision is False
