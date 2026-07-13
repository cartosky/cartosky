from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.grid_display_prep import (
    GridDisplayPrepConfig,
    prepare_grid_display_values,
    sampling_tolerance_group,
)


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
    assert meta["preserve_zero_support"] is True
    assert meta["support_min_value"] == 0.01
    assert meta["support_coverage_threshold"] == 0.5
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


def test_goes_ir13_display_prep_converts_kelvin_to_celsius() -> None:
    values = np.array([[273.15, 233.15], [np.nan, 313.15]], dtype=np.float32)

    prepared, meta = prepare_grid_display_values(model="goes-east", var="ir13", values=values)

    assert meta is not None
    assert meta["id"] == "goes_ir13_display_celsius_v1"
    assert meta["unit_conversion"] == "K_to_C"
    assert prepared.dtype == np.float32
    assert float(prepared[0, 0]) == 0.0
    assert float(prepared[0, 1]) == -40.0
    assert float(prepared[1, 1]) == 40.0
    assert np.isnan(prepared[1, 0])


def test_goes_wv9_display_prep_converts_kelvin_to_celsius() -> None:
    values = np.array([[273.15, 243.15], [np.nan, 303.15]], dtype=np.float32)

    prepared, meta = prepare_grid_display_values(model="goes-east", var="wv9", values=values)

    assert meta is not None
    assert meta["id"] == "goes_wv9_display_celsius_v1"
    assert meta["unit_conversion"] == "K_to_C"
    assert prepared.dtype == np.float32
    assert float(prepared[0, 0]) == 0.0
    assert float(prepared[0, 1]) == -30.0
    assert float(prepared[1, 1]) == 30.0
    assert np.isnan(prepared[1, 0])


def test_ecmwf_snowfall_display_prep_keeps_native_grid_resolution() -> None:
    values = np.array(
        [
            [0.0, 1.5],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="ecmwf", var="snowfall_total", values=values)
    gfs_prepared, gfs_meta = prepare_grid_display_values(model="gfs", var="snowfall_total", values=values)

    assert meta is not None
    assert meta["id"] == "ecmwf_snowfall_total_display_v2"
    assert meta["upscale_factor"] == 1
    assert meta["preserve_zero_support"] is True
    assert prepared.shape == values.shape
    assert prepared.dtype == np.float32
    assert float(prepared[0, 1]) == 1.5
    assert gfs_meta is not None
    assert gfs_meta["upscale_factor"] == 3
    assert gfs_prepared.shape == (6, 6)


def test_ecmwf_kuchera_snowfall_display_prep_keeps_native_grid_resolution() -> None:
    values = np.array(
        [
            [0.0, 2.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(
        model="ecmwf",
        var="snowfall_kuchera_total",
        values=values,
    )
    gfs_prepared, gfs_meta = prepare_grid_display_values(
        model="gfs",
        var="snowfall_kuchera_total",
        values=values,
    )

    assert meta is not None
    assert meta["id"] == "ecmwf_snowfall_total_display_v2"
    assert meta["upscale_factor"] == 1
    assert meta["preserve_zero_support"] is True
    assert prepared.shape == values.shape
    assert prepared.dtype == np.float32
    assert float(prepared[0, 1]) == 2.0
    assert gfs_meta is not None
    assert gfs_meta["upscale_factor"] == 3
    assert gfs_prepared.shape == (6, 6)


def test_ecmwf_ptype_intensity_display_prep_keeps_native_grid_resolution() -> None:
    values = np.array(
        [
            [0.0, 16.0],
            [26.0, 42.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="ecmwf", var="ptype_intensity", values=values)

    assert meta is not None
    assert meta["id"] == "ecmwf_ptype_intensity_display_v2"
    assert meta["upscale_factor"] == 1
    assert meta["categorical_nearest"] is True
    assert prepared.shape == values.shape
    np.testing.assert_array_equal(prepared, values)


def test_hrrr_radar_ptype_display_prep_keeps_packed_grid_single_resolution() -> None:
    values = np.array(
        [
            [0.0, 1.0],
            [2.0, 9.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="hrrr", var="radar_ptype", values=values)

    assert meta is not None
    assert meta["id"] == "hrrr_radar_ptype_display_v3"
    assert "categorical_nearest" not in meta
    assert prepared.shape == values.shape
    np.testing.assert_array_equal(prepared, values)


def test_hrrr_radar_ptype_component_display_prep_uses_continuous_upscale() -> None:
    values = np.array(
        [
            [0.0, 40.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="hrrr", var="radar_ptype_rain", values=values)

    assert meta is not None
    assert meta["id"] == "hrrr_radar_ptype_component_display_v1"
    assert meta["preserve_zero_support"] is True
    assert meta["support_min_value"] == 10.0
    assert meta["support_coverage_threshold"] == 0.15
    assert "categorical_nearest" not in meta
    assert prepared.shape == (6, 6)
    assert prepared.dtype == np.float32
    assert prepared.max() > 0.0


def test_gfs_ptype_intensity_display_prep_upscales_categorically() -> None:
    values = np.array(
        [
            [0.0, 16.0],
            [26.0, 42.0],
        ],
        dtype=np.float32,
    )

    prepared, meta = prepare_grid_display_values(model="gfs", var="ptype_intensity", values=values)

    assert meta is not None
    assert meta["id"] == "gfs_ptype_intensity_display_v1"
    assert meta["categorical_nearest"] is True
    assert prepared.shape == (6, 6)
    np.testing.assert_array_equal(prepared[:3, :3], np.zeros((3, 3), dtype=np.float32))
    np.testing.assert_array_equal(prepared[:3, 3:], np.full((3, 3), 16.0, dtype=np.float32))
    np.testing.assert_array_equal(prepared[3:, :3], np.full((3, 3), 26.0, dtype=np.float32))
    np.testing.assert_array_equal(prepared[3:, 3:], np.full((3, 3), 42.0, dtype=np.float32))


def test_sampling_tolerance_group_covers_all_four_config_shapes() -> None:
    """The shared classifier must derive the group purely from the config shape
    (upscale_factor x categorical_nearest), never from model/variable names.
    Synthetic configs cover all four shapes plus the config-absent case."""
    # No display-prep config at all -> Group 1.
    assert sampling_tolerance_group(None) == 1
    # Config present but no upscale and not categorical -> still Group 1.
    assert sampling_tolerance_group(
        GridDisplayPrepConfig(id="synthetic_identity_v1", upscale_factor=1)
    ) == 1
    # Continuous upscale -> Group 2.
    assert sampling_tolerance_group(
        GridDisplayPrepConfig(id="synthetic_continuous_v1", upscale_factor=3)
    ) == 2
    # Categorical upscale -> Group 3.
    assert sampling_tolerance_group(
        GridDisplayPrepConfig(
            id="synthetic_categorical_upscale_v1",
            upscale_factor=3,
            categorical_nearest=True,
        )
    ) == 3
    # Categorical without upscale -> Group 4 (strict equality group).
    assert sampling_tolerance_group(
        GridDisplayPrepConfig(
            id="synthetic_categorical_v1",
            upscale_factor=1,
            categorical_nearest=True,
        )
    ) == 4
    # render_categorical_nearest is a rendering hint only; it must not affect
    # the sampling group (hrrr/nam radar_ptype set it False while remaining
    # categorical for sampling purposes).
    assert sampling_tolerance_group(
        GridDisplayPrepConfig(
            id="synthetic_categorical_render_off_v1",
            upscale_factor=1,
            categorical_nearest=True,
            render_categorical_nearest=False,
        )
    ) == 4


def test_mrms_reflectivity_display_prep_preserves_negative_dbz() -> None:
    # Real echo can be negative dBZ; the negative-noise clamp is disabled for
    # mrms/reflectivity and the smoothing support includes values >= -35.
    values = np.full((6, 6), -18.0, dtype=np.float32)
    values[0, 0] = np.nan  # masked sentinel / outside coverage

    prepared, meta = prepare_grid_display_values(model="mrms", var="reflectivity", values=values)

    assert meta is not None
    assert meta["id"] == "mrms_reflectivity_display_v2"
    assert np.isnan(prepared[0, 0])
    finite = prepared[np.isfinite(prepared)]
    # Smoothing a constant field must return the constant, not zero it.
    assert finite.size == values.size - 1
    assert np.allclose(finite, -18.0, atol=1e-4)


def test_mrms_reflectivity_display_prep_keeps_nan_out_of_smoothing() -> None:
    values = np.full((6, 6), 30.0, dtype=np.float32)
    values[:, 3:] = np.nan

    prepared, _ = prepare_grid_display_values(model="mrms", var="reflectivity", values=values)

    assert np.isnan(prepared[:, 3:]).all()
    assert np.allclose(prepared[:, 0], 30.0, atol=1e-4)


def test_clamped_display_prep_variables_still_zero_negatives() -> None:
    # Default clamp behavior must be unchanged for physically non-negative
    # variables (negative inputs are numeric noise).
    values = np.array([[2.0, -0.5], [-0.5, 2.0]], dtype=np.float32)

    prepared, _ = prepare_grid_display_values(model="ecmwf", var="snowfall_total", values=values)

    assert float(prepared.min()) >= 0.0


def test_mrms_reflectivity_display_prep_sets_edge_fade_render_hint() -> None:
    values = np.full((4, 4), 20.0, dtype=np.float32)

    _, meta = prepare_grid_display_values(model="mrms", var="reflectivity", values=values)
    assert meta is not None
    assert meta.get("edge_fade") is True
    assert meta.get("edge_fill_value") == 0.0

    _, other_meta = prepare_grid_display_values(model="gfs", var="precip_total", values=values)
    assert other_meta is not None
    assert "edge_fade" not in other_meta
