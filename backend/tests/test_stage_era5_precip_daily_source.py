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


class Coord:
    def __init__(self, values):
        self.values = values


class FakeDataArray:
    dims = ("time", "latitude", "longitude")

    def __init__(
        self,
        values: dict[np.datetime64, np.ndarray],
        *,
        time_coord: str = "time",
        fail_on_values: bool = False,
    ):
        self._values = values
        self._time_coord = time_coord
        self._fail_on_values = fail_on_values
        self.coords = {time_coord: True}
        self.dims = (time_coord, "latitude", "longitude")
        first_value = next(iter(values.values()))
        self.sizes = {
            time_coord: len(values),
            "latitude": first_value.shape[0],
            "longitude": first_value.shape[1],
        }

    def __getitem__(self, key: str):
        assert key == self._time_coord
        return Coord(np.array(list(self._values.keys())))

    @property
    def values(self):
        if self._fail_on_values:
            raise AssertionError("Existing daily output should be skipped before loading monthly values")
        return np.stack([self._values[key] for key in self._values], axis=0)

    def rename(self, mapping):
        if self._time_coord in mapping:
            return FakeDataArray(self._values, time_coord=mapping[self._time_coord], fail_on_values=self._fail_on_values)
        return self

    def transpose(self, *dims):
        assert dims == self.dims
        return self


class FakeDataset:
    def __init__(
        self,
        values: dict[np.datetime64, np.ndarray],
        *,
        time_coord: str = "time",
        fail_on_values: bool = False,
    ):
        self.array = FakeDataArray(values, time_coord=time_coord, fail_on_values=fail_on_values)
        self.coords = {time_coord: True}

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

    class FakeXarray:
        @staticmethod
        def open_dataset(_path):
            return FakeDataset(
                {
                    np.datetime64("1991-01-01T00:00:00"): np.array([[0.001]], dtype=np.float32),
                    np.datetime64("1991-01-01T01:00:00"): np.array([[0.002]], dtype=np.float32),
                    np.datetime64("1991-01-02T00:00:00"): np.array([[0.004]], dtype=np.float32),
                }
            )

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


def test_stage_writes_each_file_before_opening_next_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    input_files = [tmp_path / "raw" / "era5_tp_199101.nc", tmp_path / "raw" / "era5_tp_199102.nc"]
    written_payloads: list[Path] = []
    opened_paths: list[Path] = []
    monkeypatch.setattr(stage_script, "_iter_input_files", lambda input_root: input_files)

    datasets = {
        input_files[0]: FakeDataset(
            {
                np.datetime64("1991-01-01T00:00:00"): np.array([[0.001]], dtype=np.float32),
                np.datetime64("1991-01-01T01:00:00"): np.array([[0.002]], dtype=np.float32),
            }
        ),
        input_files[1]: FakeDataset(
            {
                np.datetime64("1991-02-01T00:00:00"): np.array([[0.003]], dtype=np.float32),
                np.datetime64("1991-02-01T01:00:00"): np.array([[0.004]], dtype=np.float32),
            }
        ),
    }

    class FakeXarray:
        @staticmethod
        def open_dataset(path):
            opened_paths.append(path)
            if path == input_files[1]:
                assert written_payloads == [tmp_path / "stage" / "era5" / "single-levels" / "precip_daily" / "1991" / "19910101_precip_daily.tif"]
            return datasets[path]

    monkeypatch.setattr(stage_script, "_import_xarray", lambda: FakeXarray)

    def fake_write(path: Path, *, values_inches: np.ndarray, longitudes, latitudes, source_hours: int) -> None:
        written_payloads.append(path)

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
    assert opened_paths == input_files
    assert [path.name for path in written_payloads] == ["19910101_precip_daily.tif", "19910201_precip_daily.tif"]


def test_stage_skips_existing_daily_output_without_loading_hourly_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    input_file = tmp_path / "raw" / "era5_tp_199101.nc"
    existing_output = tmp_path / "stage" / "era5" / "single-levels" / "precip_daily" / "1991" / "19910101_precip_daily.tif"
    existing_output.parent.mkdir(parents=True)
    existing_output.touch()
    monkeypatch.setattr(stage_script, "_iter_input_files", lambda input_root: [input_file])

    class FakeXarray:
        @staticmethod
        def open_dataset(_path):
            return FakeDataset(
                {
                    np.datetime64("1991-01-01T00:00:00"): np.array([[0.001]], dtype=np.float32),
                    np.datetime64("1991-01-01T01:00:00"): np.array([[0.002]], dtype=np.float32),
                },
                fail_on_values=True,
            )

    monkeypatch.setattr(stage_script, "_import_xarray", lambda: FakeXarray)
    monkeypatch.setattr(stage_script, "_write_daily_raster", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not write")))

    written, skipped = stage_script.stage_era5_precip_daily_source(
        input_root=tmp_path / "raw",
        stage_root=tmp_path / "stage",
        start_year=1991,
        end_year=1991,
        units_in="meters",
        overwrite=False,
        require_24_hours=False,
    )

    assert (written, skipped) == (0, 1)


def test_stage_vectorized_monthly_processing_handles_valid_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    input_file = tmp_path / "raw" / "era5_tp_199103.nc"
    written_payloads: list[tuple[Path, np.ndarray, int]] = []
    monkeypatch.setattr(stage_script, "_iter_input_files", lambda input_root: [input_file])

    class FakeXarray:
        @staticmethod
        def open_dataset(_path):
            return FakeDataset(
                {
                    np.datetime64("1991-03-01T00:00:00"): np.array([[0.001]], dtype=np.float32),
                    np.datetime64("1991-03-01T01:00:00"): np.array([[0.002]], dtype=np.float32),
                    np.datetime64("1991-03-01T02:00:00"): np.array([[0.003]], dtype=np.float32),
                },
                time_coord="valid_time",
            )

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

    assert (written, skipped) == (1, 0)
    assert written_payloads[0][0].name == "19910301_precip_daily.tif"
    assert written_payloads[0][2] == 3
    assert np.allclose(written_payloads[0][1], np.array([[0.006 * 39.37007874015748]], dtype=np.float32))
