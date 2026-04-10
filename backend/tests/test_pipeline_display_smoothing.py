import numpy as np

from app.services.builder.colorize import float_to_rgba
from app.services.builder.pipeline import _prepare_display_data_for_colorize, _warp_resampling_for_variable


def test_continuous_vars_without_display_smoothing_remain_passthrough() -> None:
    data = np.zeros((9, 9), dtype=np.float32)
    data[4, 4] = 100.0
    spec = {"type": "continuous"}

    for model_id, var_key in (
        ("gfs", "tmp2m"),
        ("gfs", "dp2m"),
        ("gfs", "tmp850"),
        ("gfs", "wspd10m"),
        ("gfs", "wgst10m"),
        ("gfs", "qpf6h"),
        ("gfs", "precip_total"),
        ("gfs", "snowfall_total"),
        ("gfs", "pwat"),
        ("hrrr", "tmp2m"),
    ):
        display = _prepare_display_data_for_colorize(
            data,
            spec,
            model_id=model_id,
            var_key=var_key,
        )
        np.testing.assert_array_equal(display, data)


def test_discrete_kind_remains_passthrough() -> None:
    data = np.arange(16, dtype=np.float32).reshape(4, 4)
    spec = {"type": "indexed"}

    display = _prepare_display_data_for_colorize(
        data,
        spec,
        model_id="gfs",
        var_key="ptype_intensity",
    )
    np.testing.assert_array_equal(display, data)


def test_precip_and_snow_use_bilinear_warp_resampling_across_models() -> None:
    for model_id in ("hrrr", "nam", "gfs"):
        assert _warp_resampling_for_variable(
            model_id=model_id,
            var_key="snowfall_total",
            kind="continuous",
        ) == "bilinear"
        assert _warp_resampling_for_variable(
            model_id=model_id,
            var_key="precip_total",
            kind="continuous",
        ) == "bilinear"


def test_continuous_transparent_pixels_zero_rgb() -> None:
    data = np.array([[0.0, 0.05, 0.2]], dtype=np.float32)

    rgba, _ = float_to_rgba(data, "snowfall_total")

    assert tuple(int(v) for v in rgba[:, 0, 0]) == (0, 0, 0, 0)
    assert tuple(int(v) for v in rgba[:, 0, 1]) == (0, 0, 0, 0)
    assert int(rgba[3, 0, 2]) == 255


def test_continuous_power_norm_expands_low_end() -> None:
    data = np.array([[1.0]], dtype=np.float32)
    linear_spec = {
        "type": "continuous",
        "range": (0.0, 4.0),
        "colors": ["#000000", "#ffffff"],
        "units": "in",
    }
    power_spec = {
        **linear_spec,
        "power_norm_gamma": 0.72,
    }

    linear_rgba, linear_meta = float_to_rgba(data, "snowfall_total", spec_override=linear_spec)
    power_rgba, power_meta = float_to_rgba(data, "snowfall_total", spec_override=power_spec)

    assert int(power_rgba[0, 0, 0]) > int(linear_rgba[0, 0, 0])
    assert power_meta["power_norm_gamma"] == 0.72
    assert "power_norm_gamma" not in linear_meta
