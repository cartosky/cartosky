from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder.derive import (  # noqa: E402
    _relative_humidity_from_temp_dewpoint_c,
    _relative_humidity_from_specific_humidity_temp_pressure,
    _specific_humidity_to_kgkg,
    _temperature_to_celsius,
)


def test_relative_humidity_from_temperature_and_dewpoint_uses_magnus_ratio() -> None:
    temp_c = np.array([[20.0, 30.0, 20.0]], dtype=np.float32)
    dewpoint_c = np.array([[20.0, 20.0, 10.0]], dtype=np.float32)

    rh = _relative_humidity_from_temp_dewpoint_c(temp_c, dewpoint_c)

    assert rh.dtype == np.float32
    np.testing.assert_allclose(rh[0, 0], 100.0, rtol=0.0, atol=0.01)
    np.testing.assert_allclose(rh[0, 1], 55.05, rtol=0.0, atol=0.05)
    np.testing.assert_allclose(rh[0, 2], 52.51, rtol=0.0, atol=0.05)


def test_relative_humidity_masks_invalid_values_and_caps_supersaturation() -> None:
    temp_c = np.array([[10.0, np.nan, 5.0]], dtype=np.float32)
    dewpoint_c = np.array([[12.0, 0.0, np.nan]], dtype=np.float32)

    rh = _relative_humidity_from_temp_dewpoint_c(temp_c, dewpoint_c)

    assert rh[0, 0] == 100.0
    assert np.isnan(rh[0, 1])
    assert np.isnan(rh[0, 2])


def test_temperature_to_celsius_supports_common_component_units() -> None:
    values = np.array([[273.15, 32.0, 0.0]], dtype=np.float32)

    np.testing.assert_allclose(_temperature_to_celsius(values[:, :1], "K"), [[0.0]], atol=0.001)
    np.testing.assert_allclose(_temperature_to_celsius(values[:, 1:2], "F"), [[0.0]], atol=0.001)
    np.testing.assert_allclose(_temperature_to_celsius(values[:, 2:], "C"), [[0.0]], atol=0.001)

    with pytest.raises(ValueError, match="Unsupported temperature units"):
        _temperature_to_celsius(values, "rankine")


def test_relative_humidity_from_specific_humidity_temperature_and_pressure() -> None:
    q_kgkg = np.array([[0.00423, 0.00933]], dtype=np.float32)
    temp_c = np.array([[0.0, 10.0]], dtype=np.float32)

    rh = _relative_humidity_from_specific_humidity_temp_pressure(q_kgkg, temp_c, 700.0)

    assert rh.dtype == np.float32
    np.testing.assert_allclose(rh[0, 0], 77.9, rtol=0.0, atol=0.2)
    np.testing.assert_allclose(rh[0, 1], 85.16, rtol=0.0, atol=0.2)


def test_specific_humidity_to_kgkg_supports_common_units() -> None:
    values = np.array([[4.0, 0.004]], dtype=np.float32)

    np.testing.assert_allclose(_specific_humidity_to_kgkg(values[:, :1], "g/kg"), [[0.004]], atol=0.000001)
    np.testing.assert_allclose(_specific_humidity_to_kgkg(values[:, 1:], "kg/kg"), [[0.004]], atol=0.000001)

    with pytest.raises(ValueError, match="Unsupported specific humidity units"):
        _specific_humidity_to_kgkg(values, "percent")