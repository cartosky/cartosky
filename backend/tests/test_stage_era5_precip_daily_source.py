from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import stage_era5_precip_daily_source as stage_script


def test_output_path_uses_daily_precip_stage_contract(tmp_path: Path) -> None:
    path = stage_script._output_path(tmp_path, valid_date=date(1991, 1, 5))
    expected = (
        tmp_path
        / "era5"
        / "single-levels"
        / "precip_daily"
        / "1991"
        / "19910105_precip_daily.tif"
    )
    assert path == expected


def test_convert_precip_to_inches_from_era5_meters() -> None:
    values = np.array([[0.001, 0.0254]], dtype=np.float32)
    converted = stage_script._convert_precip_to_inches(values, units_in="meters")
    assert np.allclose(converted, np.array([[0.03937008, 1.0]], dtype=np.float32), atol=1.0e-6)


def test_stage_hourly_total_precipitation_sums_to_utc_daily_inches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    written_payloads: list[tuple[Path, np.ndarray, int]] = []

    monkeypatch.setattr(stage_script, "_iter_input_files", lambda input_root: [input_root / "era5_tp.nc"])
    monkeypatch.setattr(stage_script, "_import_xarray", lambda: object())

    class FakeDataArray:
        dims = ("time", "latitude", "longitude")
        coords = {"time": True}

        def __init__(self, values: dict[np.datetime64, np.ndarray]):
            self._values = values
            self.time = self

        def __getitem__(self, key: str):
            assert key == "time"
            return self

        @property
        def values(self):
            return np.array(list(self._values.keys()))

        def sel(self, selector):
            time_value = selector["time"]

            class Slice:
                values = self._values[time_value]

            return Slice()

    class Coord:
        def __init__(self, values):
            self.values = values

    class FakeDataset:
        def __init__(self):
            self.array = FakeDataArray(
                {
                    np.datetime64("1991-01-01T00:00:00"): np.array([[0.001]], dtype=np.float32),
                    np.datetime64("1991-01-01T01:00:00"): np.array([[0.002]], dtype=np.float32),
                    np.datetime64("1991-01-02T00:00:00"): np.array([[0.004]], dtype=np.float32),
                }
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __contains__(self, key: str) -> bool:
            return key in {"tp", "longitude", "latitude"}

        def __getitem__(self, key: str):
            if key == "tp":
                return self.array
            if key == "longitude":
                return Coord(np.array([-100.0], dtype=np.float64))
            if key == "latitude":
                return Coord(np.array([40.0], dtype=np.float64))
            raise KeyError(key)

    class FakeXarray:
        @staticmethod
        def open_dataset(_path):
            return FakeDataset()

    monkeypatch.setattr(stage_script, "_import_xarray", lambda: FakeXarray)

    def fake_write(path: Path, *, values_inches: np.ndarray, longitudes, latitudes, source_hours: int) -> None:
        written_payloads.append((path, values_inches.copy(), source_hours))

    monkeypatch.setattr(stage_script, "_write_daily_raster", fake_write)

    written, skipped = stage_script.stage_era5_precip_daily_source(
        input_root=tmp_path / "raw",
        stage_root=tmp_path / "stage",
        start_year=1991,
        end_year=1991,
        units_in="meters",
        overwrite=True,
        require_24_hours=False,
    )

    assert (written, skipped) == (2, 0)
    assert written_payloads[0][0].name == "19910101_precip_daily.tif"
    assert written_payloads[0][2] == 2
    assert np.allclose(written_payloads[0][1], np.array([[0.003 * 39.37007874015748]], dtype=np.float32))
    assert written_payloads[1][0].name == "19910102_precip_daily.tif"
    assert written_payloads[1][2] == 1

