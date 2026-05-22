from __future__ import annotations

import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ndfd_source import NDFDSourceField, _derive_window_max, _rolling_max


def _field(*, valid_hour: int, values: np.ndarray) -> NDFDSourceField:
    valid_time = datetime(2026, 5, 22, valid_hour, 0, tzinfo=timezone.utc)
    return NDFDSourceField(
        valid_time=valid_time,
        issue_time=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
        values=np.asarray(values, dtype=np.float32),
        transform=None,
        crs="EPSG:4326",
        source_url="https://example.com/ds.wgust.bin",
        source_filename="ds.wgust.bin",
        source_units="[m/s]",
    )


def test_rolling_max_preserves_nan_without_warning() -> None:
    fields = [
        _field(valid_hour=0, values=np.array([[np.nan, 1.0], [np.nan, 2.0]], dtype=np.float32)),
        _field(valid_hour=6, values=np.array([[np.nan, 3.0], [np.nan, 4.0]], dtype=np.float32)),
    ]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", category=RuntimeWarning)
        derived = _rolling_max(fields, window_size=2)

    assert caught == []
    assert len(derived) == 1
    np.testing.assert_allclose(derived[0].values[:, 1], np.array([3.0, 4.0], dtype=np.float32))
    assert np.isnan(derived[0].values[0, 0])
    assert np.isnan(derived[0].values[1, 0])


def test_window_max_preserves_nan_without_warning() -> None:
    fields = [
        _field(valid_hour=1, values=np.array([[np.nan, 5.0]], dtype=np.float32)),
        _field(valid_hour=6, values=np.array([[np.nan, 7.0]], dtype=np.float32)),
    ]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", category=RuntimeWarning)
        derived = _derive_window_max(fields, window_hours=6, cadence_hours=6)

    assert caught == []
    assert len(derived) == 1
    assert np.isnan(derived[0].values[0, 0])
    assert float(derived[0].values[0, 1]) == 7.0