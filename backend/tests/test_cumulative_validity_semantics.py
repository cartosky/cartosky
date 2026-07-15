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


def test_cumulative_step_validity_distinguishes_scalars_from_masks() -> None:
    values = np.array(
        [[-1.0, 0.0, 0.5, 1.0, 1.5, np.nan, np.inf]],
        dtype=np.float32,
    )

    np.testing.assert_array_equal(
        derive_module._cumulative_step_validity(values),
        np.array([[False, True, True, True, True, False, False]]),
    )
    np.testing.assert_array_equal(
        derive_module._cumulative_step_validity(values, is_mask=True),
        np.array([[False, True, True, True, False, False, False]]),
    )


def test_precip_total_treats_negative_step_pixels_as_invalid(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    step_data = np.array([[-1.0, 2.0, np.nan]], dtype=np.float32)

    def _fake_cumulative_apcp_loop(**kwargs):
        contribution, step_valid = kwargs["process_step"](
            3,
            step_data,
            None,
            crs,
            transform,
        )
        cumulative = np.where(step_valid, contribution, np.nan).astype(np.float32)
        return cumulative, crs, transform, step_valid

    monkeypatch.setattr(
        derive_module,
        "_cumulative_apcp_loop",
        _fake_cumulative_apcp_loop,
    )
    monkeypatch.setattr(
        derive_module,
        "_kuchera_store_cumulative_cache",
        lambda **_kwargs: None,
    )

    values, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="gfs",
        var_key="precip_total",
        product="pgrb2.0p25",
        run_date=datetime(2026, 7, 15, 0, 0),
        fh=3,
        var_spec_model=SimpleNamespace(
            selectors=SimpleNamespace(
                hints={"apcp_component": "apcp_step", "step_hours": "3"}
            )
        ),
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert np.isnan(values[0, 0])
    np.testing.assert_allclose(values[0, 1], np.float32(2.0 * 0.03937007874015748))
    assert np.isnan(values[0, 2])
