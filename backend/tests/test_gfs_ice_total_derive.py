from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module


def _ice_total_var_spec() -> SimpleNamespace:
    return SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "ptype_component": "cfrzr",
                "step_hours": "3",
                "ptype_interval_sample_mode": "three_point",
                "ptype_mask_threshold": "0.5",
                "min_step_lwe_kgm2": "0.01",
            }
        )
    )


def test_gfs_ice_total_uses_freezing_rain_mask_not_icetk(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    step_apcp = {
        3: np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
        6: np.array([[10.0, 10.0, 10.0]], dtype=np.float32),
    }
    cfrzr_samples = {
        0: np.array([[0.0, 0.0, 0.0]], dtype=np.float32),
        3: np.array([[0.0, 0.0, 1.0]], dtype=np.float32),
        6: np.array([[1.0, 0.0, 1.0]], dtype=np.float32),
    }
    fetched_components: list[str] = []

    def _fake_resolve_apcp_step_data(**kwargs):
        step_fh = int(kwargs["step_fh"])
        data = step_apcp[step_fh]
        return data, np.isfinite(data), crs, transform, "exact_step"

    def _fake_fetch_step_component(**kwargs):
        var_key = str(kwargs["var_key"])
        fetched_components.append(var_key)
        if var_key == "icetk":
            raise AssertionError("ice_total must not fetch ICETK sea/lake ice thickness")
        if var_key != "cfrzr":
            raise AssertionError(f"unexpected component fetch: {var_key}")
        return cfrzr_samples[int(kwargs["step_fh"])], crs, transform

    monkeypatch.setattr(derive_module, "_resolve_apcp_step_data", _fake_resolve_apcp_step_data)
    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)

    values, out_crs, out_transform = derive_module._derive_ptype_accumulation_cumulative(
        model_id="gfs",
        var_key="ice_total",
        product="pgrb2.0p25",
        run_date=datetime(2026, 5, 15, 0, 0),
        fh=6,
        var_spec_model=_ice_total_var_spec(),
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert "icetk" not in fetched_components
    expected = np.array([[0.39370078, 0.0, 0.78740156]], dtype=np.float32)
    np.testing.assert_allclose(values, expected, rtol=1e-5, atol=1e-5)
