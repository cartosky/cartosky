from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.grid_display_prep import prepare_grid_display_values


def test_gfs_precip_total_display_prep_uses_threshold_aware_support_mask() -> None:
    values = np.array(
        [
            [0.0, 0.2],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="gfs", var="precip_total", values=values)

    assert meta is not None
    assert meta["id"] == "gfs_precip_total_display_v2"
    assert prepared.shape == (6, 6)
    assert prepared.dtype == np.float32
    assert int(np.count_nonzero(prepared > 0.0)) == 6
    assert np.isclose(float(prepared.max()), 0.2)
    assert float(prepared[0, 2]) == 0.0
    assert float(prepared[0, 3]) > 0.0


def test_gfs_precip_total_display_prep_keeps_values_below_visibility_threshold_inside_wet_core() -> None:
    values = np.array(
        [
            [0.0, 0.02],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    prepared, _ = prepare_grid_display_values(model="gfs", var="precip_total", values=values)

    assert prepared.shape == (6, 6)
    assert float(prepared[0, 4]) > 0.0
    assert float(prepared[1, 4]) > 0.0
    assert float(prepared[0, 2]) == 0.0


def test_non_display_prep_variable_remains_passthrough() -> None:
    values = np.array([[0.0, 1.23], [np.nan, 2.86]], dtype=np.float32)

    prepared, meta = prepare_grid_display_values(model="gfs", var="pwat", values=values)

    assert meta is None
    np.testing.assert_array_equal(prepared, values.astype(np.float32))


def test_hrrr_radar_ptype_display_prep_upscales_categorically() -> None:
    values = np.array(
        [
            [0.0, 1.0],
            [2.0, 9.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="hrrr", var="radar_ptype", values=values)

    assert meta is not None
    assert meta["id"] == "hrrr_radar_ptype_display_v1"
    assert meta["categorical_nearest"] is True
    assert prepared.shape == (6, 6)
    np.testing.assert_array_equal(prepared[:3, :3], np.zeros((3, 3), dtype=np.float32))
    np.testing.assert_array_equal(prepared[:3, 3:], np.ones((3, 3), dtype=np.float32))
    np.testing.assert_array_equal(prepared[3:, :3], np.full((3, 3), 2.0, dtype=np.float32))
    np.testing.assert_array_equal(prepared[3:, 3:], np.full((3, 3), 9.0, dtype=np.float32))
