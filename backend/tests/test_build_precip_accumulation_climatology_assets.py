from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_precip_accumulation_climatology_assets as build_script


def _write_source(path: Path, value: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.array([[value]], dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0.0, 10.0, 10.0, 10.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values, 1)


def _iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def test_parse_valid_date_and_leap_day_doy() -> None:
    assert build_script._parse_valid_date(Path("19920229_precip_daily.tif")) == date(1992, 2, 29)
    assert build_script._month_day_to_doy(2, 28) == 59
    assert build_script._month_day_to_doy(2, 29) == 60
    assert build_script._month_day_to_doy(3, 1) == 61


def test_rolling_accumulations_wrap_year_boundary() -> None:
    normals = [np.array([[float(doy)]], dtype=np.float32) for doy in range(1, 367)]
    rolled = build_script._rolling_accumulations(normals, window_days=5)
    assert rolled[0] is not None
    assert np.allclose(rolled[0], np.array([[1.0 + 2.0 + 3.0 + 4.0 + 5.0]], dtype=np.float32))
    assert rolled[364] is not None
    assert np.allclose(rolled[364], np.array([[365.0 + 366.0 + 1.0 + 2.0 + 3.0]], dtype=np.float32))


def test_build_precip_accumulation_outputs_all_windows_and_inches(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "stage" / "era5" / "single-levels" / "precip_daily"
    data_root = tmp_path / "data"
    for valid_date in _iter_dates(date(1991, 1, 1), date(1992, 12, 31)):
        value_mm = float(build_script._month_day_to_doy(valid_date.month, valid_date.day))
        _write_source(source_root / f"{valid_date:%Y}" / f"{valid_date:%Y%m%d}_precip_daily.tif", value_mm)

    monkeypatch.setattr(
        build_script,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 10.0, 10.0), 10.0),
    )

    files_written, files_by_window, missing_dates = build_script.build_precip_accumulation_climatology_assets(
        source_root=source_root,
        data_root=data_root,
        version="v1",
        baseline_source="era5",
        region="na",
        reference_period="1991-2020",
        windows=(5, 7, 10, 15),
        units_in="mm",
        start_year=1991,
        end_year=1992,
        resampling="nearest",
        require_complete=True,
    )

    assert files_written == 1464
    assert files_by_window == {5: 366, 7: 366, 10: 366, 15: 366}
    assert missing_dates == []

    first_5d = data_root / "climatology" / "v1" / "era5" / "baseline" / "precip_5d" / "na" / "1991-2020" / "doy_001.tif"
    assert first_5d.is_file()
    with rasterio.open(first_5d) as ds:
        assert ds.crs.to_epsg() == 3857
        assert ds.tags(1)["units"] == "inches"
        assert ds.tags(1)["accumulation_window_days"] == "5"
        loaded = ds.read(1)
    expected_inches = (1.0 + 2.0 + 3.0 + 4.0 + 5.0) / 25.4
    assert np.allclose(loaded, np.array([[expected_inches]], dtype=np.float32), atol=1.0e-6)

    feb29_5d = data_root / "climatology" / "v1" / "era5" / "baseline" / "precip_5d" / "na" / "1991-2020" / "doy_060.tif"
    with rasterio.open(feb29_5d) as ds:
        assert ds.tags(1)["sample_count_min"] == "1"
        loaded = ds.read(1)
    expected_feb29_inches = (60.0 + 61.0 + 62.0 + 63.0 + 64.0) / 25.4
    assert np.allclose(loaded, np.array([[expected_feb29_inches]], dtype=np.float32), atol=1.0e-6)

    dec31_5d = data_root / "climatology" / "v1" / "era5" / "baseline" / "precip_5d" / "na" / "1991-2020" / "doy_366.tif"
    with rasterio.open(dec31_5d) as ds:
        loaded = ds.read(1)
    expected_dec31_inches = (366.0 + 1.0 + 2.0 + 3.0 + 4.0) / 25.4
    assert np.allclose(loaded, np.array([[expected_dec31_inches]], dtype=np.float32), atol=1.0e-6)


def test_require_complete_rejects_missing_daily_source(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "stage"
    _write_source(source_root / "19910101_precip_daily.tif", 1.0)
    monkeypatch.setattr(
        build_script,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 10.0, 10.0), 10.0),
    )

    try:
        build_script.build_precip_accumulation_climatology_assets(
            source_root=source_root,
            data_root=tmp_path / "data",
            version="v1",
            baseline_source="era5",
            region="na",
            reference_period="1991-2020",
            windows=(5,),
            units_in="inches",
            start_year=1991,
            end_year=1991,
            resampling="nearest",
            require_complete=True,
        )
    except ValueError as exc:
        assert "Missing staged daily precip source coverage" in str(exc)
        assert "1991-01-02" in str(exc)
    else:
        raise AssertionError("Expected missing daily source coverage to fail")
