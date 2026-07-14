from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module


def test_step_guard_passes_when_sequence_ends_at_fh() -> None:
    steps = derive_module._resolve_cumulative_step_fhs(
        hints={"step_hours": "6"}, fh=12, run_date=datetime(2026, 7, 14, 0, 0),
    )
    derive_module._require_cumulative_steps_end_at_fh(
        steps, fh=12, model_id="gfs", var_key="precip_total",
    )


def test_step_guard_passes_across_cadence_transition() -> None:
    steps = derive_module._resolve_cumulative_step_fhs(
        hints={"step_hours": "3", "step_transition_fh": "144", "step_hours_after_fh": "6"},
        fh=150,
        run_date=datetime(2026, 7, 14, 0, 0),
    )
    assert steps[-1] == 150
    derive_module._require_cumulative_steps_end_at_fh(
        steps, fh=150, model_id="ecmwf", var_key="ice_total",
    )


def test_step_guard_rejects_off_cadence_fh() -> None:
    steps = derive_module._resolve_cumulative_step_fhs(
        hints={"step_hours": "6"}, fh=15, run_date=datetime(2026, 7, 14, 0, 0),
    )
    assert steps == [6, 12]  # the 12-15h tail window is silently dropped
    with pytest.raises(ValueError, match=r"gfs/precip_total.*fh015.*omit the tail window"):
        derive_module._require_cumulative_steps_end_at_fh(
            steps, fh=15, model_id="gfs", var_key="precip_total",
        )


def test_step_guard_rejects_empty_sequence() -> None:
    with pytest.raises(ValueError, match=r"fh003"):
        derive_module._require_cumulative_steps_end_at_fh(
            [], fh=3, model_id="gfs", var_key="precip_total",
        )


def test_precip_total_cumulative_fails_loud_on_off_cadence_fh() -> None:
    # Regression for audit 1.4: pre-fix, an off-cadence fh silently rendered an
    # accumulation valid only through the last resolved step but labeled fh.
    # No fetch mocks needed — the guard must fire before any component fetch.
    with pytest.raises(ValueError, match=r"gfs/precip_total.*fh015"):
        derive_module._derive_precip_total_cumulative(
            model_id="gfs",
            var_key="precip_total",
            product="pgrb2.0p25",
            run_date=datetime(2026, 7, 14, 0, 0),
            fh=15,
            var_spec_model=SimpleNamespace(
                selectors=SimpleNamespace(hints={"step_hours": "6"})
            ),
            var_capability=None,
            model_plugin=object(),
        )


def test_ecmwf_ptype_accumulation_fails_loud_on_off_cadence_fh() -> None:
    with pytest.raises(ValueError, match=r"ecmwf/ice_total.*fh007"):
        derive_module._derive_ptype_accumulation_ecmwf(
            model_id="ecmwf",
            var_key="ice_total",
            product="oper",
            run_date=datetime(2026, 7, 14, 0, 0),
            fh=7,
            var_spec_model=SimpleNamespace(
                selectors=SimpleNamespace(hints={"ptype_component": "ice", "step_hours": "3"})
            ),
            var_capability=None,
            model_plugin=object(),
        )
