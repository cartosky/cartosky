from __future__ import annotations

import sys
from collections import defaultdict
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

from app.services.climatology import BaselineGrid, get_baseline_grid
from scripts import build_precip_accumulation_climatology_assets as build_script

_TINY_TRANSFORM = from_origin(0.0, 10.0, 10.0, 10.0)


def _tiny_grid(height: int = 1, width: int = 1) -> BaselineGrid:
    """A 1x1 EPSG:3857 stand-in for the NA baseline grid."""
    return BaselineGrid(
        baseline_source="era5",
        region="na",
        crs="EPSG:3857",
        bbox=(0.0, 10.0 - 10.0 * height, 10.0 * width, 10.0),
        resolution=10.0,
        transform=_TINY_TRANSFORM,
        height=height,
        width=width,
        grid_id="climatology:era5:na:10.0m",
    )


def _patch_tiny_grid(monkeypatch, height: int = 1, width: int = 1) -> None:
    monkeypatch.setattr(
        build_script,
        "get_baseline_grid",
        lambda baseline_source, region: _tiny_grid(height, width),
    )


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


def test_window_accumulation_wraps_year_boundary() -> None:
    normals = [np.array([[float(doy)]], dtype=np.float32) for doy in range(1, 367)]
    first = build_script._window_accumulation(normals, doy_index=0, window_days=5)
    assert first is not None
    assert np.allclose(first, np.array([[1.0 + 2.0 + 3.0 + 4.0 + 5.0]], dtype=np.float32))
    wrapped = build_script._window_accumulation(normals, doy_index=364, window_days=5)
    assert wrapped is not None
    assert np.allclose(wrapped, np.array([[365.0 + 366.0 + 1.0 + 2.0 + 3.0]], dtype=np.float32))


def test_window_accumulation_is_none_when_a_day_is_missing() -> None:
    normals: list[np.ndarray | None] = [np.array([[1.0]], dtype=np.float32) for _ in range(366)]
    normals[3] = None
    assert build_script._window_accumulation(normals, doy_index=0, window_days=5) is None
    assert build_script._window_accumulation(normals, doy_index=4, window_days=5) is not None


def test_window_accumulation_matches_stacked_sum_including_signed_zeros() -> None:
    """Pins the zero-initialised accumulator against a `total = first.copy()` seed.

    Both forms are numerically identical; only an all--0.0 window tells them
    apart, and `np.sum(stack, axis=0)` reduces that to +0.0.
    """
    for values in (
        [np.array([[-0.0]], dtype=np.float32) for _ in range(5)],
        [np.array([[-0.0]], dtype=np.float32), np.array([[0.0]], dtype=np.float32)] * 2 + [
            np.array([[-0.0]], dtype=np.float32)
        ],
        [np.array([[1.5]], dtype=np.float32) for _ in range(5)],
    ):
        normals = list(values) + [np.array([[9.0]], dtype=np.float32)] * (366 - len(values))
        window = len(values)
        actual = build_script._window_accumulation(normals, doy_index=0, window_days=window)
        expected = np.sum(np.stack(values, axis=0), axis=0, dtype=np.float32)
        assert actual is not None
        _assert_bit_identical(actual, expected, f"window sum of {[float(v[0, 0]) for v in values]}")


def test_window_accumulation_does_not_mutate_daily_normals() -> None:
    normals = [np.array([[float(doy)]], dtype=np.float32) for doy in range(1, 367)]
    build_script._window_accumulation(normals, doy_index=0, window_days=5)
    assert float(normals[0][0, 0]) == 1.0


def test_build_precip_accumulation_outputs_all_windows_and_inches(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "stage" / "era5" / "single-levels" / "precip_daily"
    data_root = tmp_path / "data"
    for valid_date in _iter_dates(date(1991, 1, 1), date(1992, 12, 31)):
        value_mm = float(build_script._month_day_to_doy(valid_date.month, valid_date.day))
        _write_source(source_root / f"{valid_date:%Y}" / f"{valid_date:%Y%m%d}_precip_daily.tif", value_mm)

    _patch_tiny_grid(monkeypatch)

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
    _patch_tiny_grid(monkeypatch)

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


# ---------------------------------------------------------------------------
# NA byte-identity pin
#
# `_daily_normals_by_doy` used to stack every warped daily raster in RAM and
# average the stack (sizing doc R2: ~42 GiB resident at global). The streaming
# rewrite must not move a single bit of the NA output, so the pre-streaming
# arithmetic is reconstructed here and compared against the real build's
# written assets bit-for-bit.
# ---------------------------------------------------------------------------


def _reference_daily_normals_by_doy(
    sources_by_date,
    *,
    grid,
    units_in: str,
    resampling: str,
):
    """Pre-streaming `_daily_normals_by_doy`: stack every raster, then nanmean."""
    buckets: dict[int, list[np.ndarray]] = defaultdict(list)
    sample_counts = [0] * 366
    for valid_date, source in sorted(sources_by_date.items()):
        doy = build_script._month_day_to_doy(valid_date.month, valid_date.day)
        buckets[doy].append(
            build_script._load_and_warp_daily(
                source,
                grid=grid,
                units_in=units_in,
                resampling=resampling,
            )
        )
    daily_normals: list[np.ndarray | None] = [None] * 366
    for doy in range(1, 367):
        arrays = buckets.get(doy, [])
        sample_counts[doy - 1] = len(arrays)
        if arrays:
            stack = np.stack(arrays, axis=0).astype(np.float32, copy=False)
            daily_normals[doy - 1] = np.nanmean(stack, axis=0).astype(np.float32, copy=False)
    return daily_normals, sample_counts


def _reference_rolling_accumulations(daily_normals, *, window_days: int):
    """Pre-streaming `_rolling_accumulations`: stack the window, then sum."""
    accumulations: list[np.ndarray | None] = [None] * 366
    for doy_index in range(366):
        window_arrays: list[np.ndarray] = []
        for offset in range(int(window_days)):
            values = daily_normals[(doy_index + offset) % 366]
            if values is None:
                window_arrays = []
                break
            window_arrays.append(values)
        if window_arrays:
            accumulations[doy_index] = np.sum(
                np.stack(window_arrays, axis=0), axis=0, dtype=np.float32
            )
    return accumulations


def _assert_bit_identical(actual: np.ndarray, expected: np.ndarray, label: str) -> None:
    assert actual.dtype == np.float32 and expected.dtype == np.float32, label
    assert np.array_equal(
        np.ascontiguousarray(actual).view(np.uint32),
        np.ascontiguousarray(expected).view(np.uint32),
    ), f"{label}: float32 bit patterns differ"


def test_bucket_mean_is_bit_identical_to_stacked_nanmean(tmp_path: Path) -> None:
    """The streaming mean reproduces `np.nanmean(stack)` exactly, NaNs included.

    A float64 accumulator (the instantaneous build's choice) would not: this
    pins the deliberate float32 running sum.
    """
    rng = np.random.default_rng(20260802)
    grid = _tiny_grid(height=7, width=9)
    for sample_count in (1, 2, 3, 5, 30):
        sources = []
        arrays = []
        for index in range(sample_count):
            values = (rng.random((7, 9), dtype=np.float32) * 3.0).astype(np.float32)
            values[rng.random((7, 9)) < 0.2] = np.nan
            values[0, 0] = np.nan  # an all-NaN cell across the whole bucket
            # Signed-zero cells. `np.add.reduce` starts from the +0.0 identity,
            # so an all--0.0 cell must come out +0.0. These pin the *form* of
            # the streaming fold: a `sum += np.where(finite, x, 0)` fold (which
            # is numerically identical and passes every other assertion here)
            # carries the sign through and yields -0.0 instead.
            values[1, 0] = np.float32(-0.0)  # -0.0 in every sample
            values[1, 1] = np.float32(-0.0) if index % 2 else np.nan  # -0.0 mixed with NaN
            values[1, 2] = np.float32(-0.0) if index else np.float32(0.0)
            path = tmp_path / f"n{sample_count}" / f"1991010{1}_{index}_precip_daily.tif"
            path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                height=7,
                width=9,
                count=1,
                dtype="float32",
                crs="EPSG:3857",
                transform=_TINY_TRANSFORM,
                nodata=float("nan"),
            ) as ds:
                ds.write(values, 1)
            sources.append(build_script.DailyPrecipRaster(path=path, valid_date=date(1991, 1, 1)))
            arrays.append(values)

        streamed = build_script._bucket_mean(
            sources,
            grid=grid,
            units_in="inches",
            resampling="nearest",
        )
        reference = np.nanmean(
            np.stack(arrays, axis=0).astype(np.float32, copy=False), axis=0
        ).astype(np.float32, copy=False)
        _assert_bit_identical(streamed, reference, f"bucket_mean n={sample_count}")


def test_na_build_is_byte_identical_to_pre_streaming_reference(monkeypatch, tmp_path: Path) -> None:
    """End-to-end NA pin: every written asset matches the stacked reference bit-for-bit.

    The sources are on a coarser, offset EPSG:3857 grid so the bilinear warp is
    a real interpolation (not the identity fast path), the values carry NaNs so
    the NaN-aware mean matters, and coverage is three full years so every
    day-of-year bucket has multiple samples and every rolling window closes.
    """
    rng = np.random.default_rng(7)
    source_root = tmp_path / "stage"
    data_root = tmp_path / "data"
    grid = _tiny_grid(height=6, width=8)
    _patch_tiny_grid(monkeypatch, height=6, width=8)

    source_transform = from_origin(-5.0, 12.0, 13.0, 11.0)
    for valid_date in _iter_dates(date(1991, 1, 1), date(1993, 12, 31)):
        values = (rng.random((5, 7), dtype=np.float32) * 20.0).astype(np.float32)
        values[rng.random((5, 7)) < 0.15] = np.nan
        path = source_root / f"{valid_date:%Y}" / f"{valid_date:%Y%m%d}_precip_daily.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=5,
            width=7,
            count=1,
            dtype="float32",
            crs="EPSG:3857",
            transform=source_transform,
            nodata=float("nan"),
        ) as ds:
            ds.write(values, 1)

    windows = (5, 15)
    files_written, files_by_window, missing_dates = build_script.build_precip_accumulation_climatology_assets(
        source_root=source_root,
        data_root=data_root,
        version="v1",
        baseline_source="era5",
        region="na",
        reference_period="1991-2020",
        windows=windows,
        units_in="mm",
        start_year=1991,
        end_year=1993,
        resampling="bilinear",
        require_complete=True,
    )
    assert missing_dates == []
    assert files_by_window == {5: 366, 15: 366}
    assert files_written == 732

    sources_by_date = build_script._scan_daily_precip_rasters(
        source_root, start_year=1991, end_year=1993
    )
    reference_normals, reference_counts = _reference_daily_normals_by_doy(
        sources_by_date,
        grid=grid,
        units_in="mm",
        resampling="bilinear",
    )
    assert reference_counts == [3] * 366 or reference_counts[59] == 1

    checked = 0
    for window in windows:
        reference_accumulations = _reference_rolling_accumulations(
            reference_normals, window_days=window
        )
        root = (
            data_root / "climatology" / "v1" / "era5" / "baseline"
            / f"precip_{window}d" / "na" / "1991-2020"
        )
        for doy in range(1, 367):
            expected = reference_accumulations[doy - 1]
            assert expected is not None
            with rasterio.open(root / f"doy_{doy:03d}.tif") as ds:
                actual = ds.read(1)
            _assert_bit_identical(
                actual.astype(np.float32, copy=False),
                expected,
                f"precip_{window}d doy_{doy:03d}",
            )
            checked += 1
    assert checked == 732


# ---------------------------------------------------------------------------
# Global (native EPSG:4326) target
# ---------------------------------------------------------------------------


def _global_grid():
    return get_baseline_grid(baseline_source="era5", region="global")


def _write_global_source(path: Path, values: np.ndarray) -> None:
    grid = _global_grid()
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=grid.height,
        width=grid.width,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=grid.transform,
        nodata=float("nan"),
        tiled=True,
        compress="deflate",
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def test_global_baseline_grid_matches_the_domain_contract() -> None:
    grid = _global_grid()
    assert grid.crs == "EPSG:4326"
    assert (grid.height, grid.width) == (721, 1440)
    assert grid.bbox == (-180.125, -90.125, 179.875, 90.125)
    assert [round(float(value), 10) for value in grid.transform[:6]] == [
        0.25,
        0.0,
        -180.125,
        0.0,
        -0.25,
        90.125,
    ]


def test_staged_global_precip_raster_is_recognised_as_the_identity_grid() -> None:
    """The stage script's own transform arithmetic must land on the contract grid.

    If this drifts, the build silently falls back to a bilinear warp instead of
    the identity copy, which is the difference between an exact baseline and a
    resampled one.
    """
    from scripts import stage_era5_precip_daily_source as stage_script

    longitudes = np.arange(0.0, 360.0, 0.25)
    latitudes = np.arange(90.0, -90.25, -0.25)
    values = np.zeros((latitudes.size, longitudes.size), dtype=np.float32)
    _, normalized_lons = stage_script._normalize_longitudes(values, longitudes)
    _, normalized_lats = stage_script._normalize_latitudes(values, latitudes)
    staged_transform = stage_script._transform_from_latlon(normalized_lons, normalized_lats)

    assert build_script._is_same_grid(
        src_crs="EPSG:4326",
        src_transform=staged_transform,
        src_shape=(latitudes.size, longitudes.size),
        grid=_global_grid(),
    )


def test_global_build_takes_the_identity_path_and_never_reprojects(monkeypatch, tmp_path: Path) -> None:
    grid = _global_grid()
    rng = np.random.default_rng(11)
    source_root = tmp_path / "stage"
    data_root = tmp_path / "data"

    def _forbidden_reproject(*args, **kwargs):
        raise AssertionError("global precip build must not reproject the staged grid")

    monkeypatch.setattr(build_script, "reproject", _forbidden_reproject)

    inputs: dict[date, np.ndarray] = {}
    for valid_date in (date(1991, 1, 1), date(1992, 1, 1), date(1991, 1, 2)):
        values = (rng.random((grid.height, grid.width), dtype=np.float32) * 2.0).astype(np.float32)
        values[0, :] = np.nan  # north pole row unobserved
        if valid_date in inputs:
            raise AssertionError("duplicate test date")
        inputs[valid_date] = values
        _write_global_source(
            source_root / f"{valid_date:%Y}" / f"{valid_date:%Y%m%d}_precip_daily.tif",
            values,
        )

    files_written, files_by_window, _missing = build_script.build_precip_accumulation_climatology_assets(
        source_root=source_root,
        data_root=data_root,
        version="v1",
        baseline_source="era5",
        region="global",
        reference_period="1991-2020",
        windows=(1,),
        units_in="inches",
        start_year=1991,
        end_year=1992,
        resampling="bilinear",
        require_complete=False,
    )
    assert files_written == 2
    assert files_by_window == {1: 2}

    root = (
        data_root / "climatology" / "v1" / "era5" / "baseline"
        / "precip_1d" / "global" / "1991-2020"
    )
    with rasterio.open(root / "doy_002.tif") as ds:
        assert ds.crs.to_epsg() == 4326
        assert (ds.height, ds.width) == (grid.height, grid.width)
        assert ds.tags(1)["units"] == "inches"
        assert ds.tags(1)["sample_count_min"] == "1"
        doy2 = ds.read(1)
    # doy 002 has a single sample: a 1-day window over a 1-sample mean is the
    # staged value itself, bit-for-bit. Anything else means an interpolation
    # crept in.
    _assert_bit_identical(doy2.astype(np.float32, copy=False), inputs[date(1991, 1, 2)], "global doy_002")
    assert np.isnan(doy2[0, :]).all()

    with rasterio.open(root / "doy_001.tif") as ds:
        doy1 = ds.read(1)
    expected_doy1 = np.nanmean(
        np.stack([inputs[date(1991, 1, 1)], inputs[date(1992, 1, 1)]], axis=0), axis=0
    ).astype(np.float32, copy=False)
    _assert_bit_identical(doy1.astype(np.float32, copy=False), expected_doy1, "global doy_001")


def test_unsupported_region_is_rejected(tmp_path: Path) -> None:
    source_root = tmp_path / "stage"
    _write_source(source_root / "19910101_precip_daily.tif", 1.0)
    try:
        build_script.build_precip_accumulation_climatology_assets(
            source_root=source_root,
            data_root=tmp_path / "data",
            version="v1",
            baseline_source="era5",
            region="conus",
            reference_period="1991-2020",
            windows=(5,),
            units_in="inches",
            start_year=1991,
            end_year=1991,
            resampling="nearest",
            require_complete=False,
        )
    except ValueError as exc:
        assert "region in na, global" in str(exc)
    else:
        raise AssertionError("Expected unsupported region to be rejected")


# ---------------------------------------------------------------------------
# Memory proof (sizing doc R2)
# ---------------------------------------------------------------------------

#: Runs the real `build_precip_accumulation_climatology_assets` at global
#: 721x1440 with a synthetic loader and a no-op writer, so the only thing
#: measured is the algorithm's resident footprint. Executed in a subprocess
#: because peak RSS is a high-water mark for the whole process.
_RSS_PROBE = r"""
import resource
import sys
import tracemalloc
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, "__BACKEND_ROOT__")

from scripts import build_precip_accumulation_climatology_assets as build_script

YEARS = int(sys.argv[1])
START_YEAR = 1992  # leap, so a 3-year window still covers doy 366
END_YEAR = START_YEAR + YEARS - 1
GRID = build_script.get_baseline_grid(baseline_source="era5", region="global")

_TEMPLATE = np.linspace(0.0, 4.0, GRID.height * GRID.width, dtype=np.float32).reshape(
    GRID.height, GRID.width
)


def _fake_scan(source_root, *, start_year, end_year):
    sources = {}
    current = date(START_YEAR, 1, 1)
    end = date(END_YEAR, 12, 31)
    while current <= end:
        sources[current] = build_script.DailyPrecipRaster(
            path=Path(f"/synthetic/{current:%Y%m%d}_precip_daily.tif"),
            valid_date=current,
        )
        current += timedelta(days=1)
    return sources


def _fake_load(source, *, grid, units_in, resampling):
    values = _TEMPLATE + np.float32(source.valid_date.toordinal() % 97)
    values = values.astype(np.float32, copy=True)
    values[0, :] = np.nan
    return values


written = []


def _fake_write(**kwargs):
    written.append(kwargs["path"].name)


build_script._scan_daily_precip_rasters = _fake_scan
build_script._load_and_warp_daily = _fake_load
build_script._write_baseline_asset = _fake_write

tracemalloc.start()
files_written, files_by_window, _missing = build_script.build_precip_accumulation_climatology_assets(
    source_root=Path("/synthetic"),
    data_root=Path("/synthetic-out"),
    version="v1",
    baseline_source="era5",
    region="global",
    reference_period="1991-2020",
    windows=(5, 7, 10, 16),
    units_in="inches",
    start_year=START_YEAR,
    end_year=END_YEAR,
    resampling="bilinear",
    require_complete=True,
)

_current, traced_peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
peak_bytes = peak if sys.platform == "darwin" else peak * 1024
print(f"RESULT files_written={files_written} peak_bytes={peak_bytes} traced_bytes={traced_peak}")
"""


def _run_rss_probe(years: int) -> tuple[int, int, int]:
    import subprocess

    completed = subprocess.run(
        [sys.executable, "-c", _RSS_PROBE.replace("__BACKEND_ROOT__", str(BACKEND_ROOT)), str(years)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stderr
    line = [row for row in completed.stdout.splitlines() if row.startswith("RESULT")][-1]
    fields = dict(item.split("=", 1) for item in line.split()[1:])
    return (
        int(fields["files_written"]),
        int(fields["peak_bytes"]),
        int(fields["traced_bytes"]),
    )


def test_global_scale_build_peak_memory_is_bounded_and_flat_in_source_years() -> None:
    """Sizing doc R2: the pre-streaming build held ~42 GiB resident at global.

    Two claims, two measurements at two source-year counts:

    * **Bounded.** Peak is dominated by the 366 daily normals the circular
      rolling window genuinely requires (721 x 1440 float32 = 1.415 GiB),
      comfortably inside 2 GiB.
    * **Flat in source years.** Years are the term that used to blow up
      (10 958 stacked rasters over the 30-year reference period). Peak must
      not grow with them.

    Flatness is asserted on `tracemalloc`'s peak, not RSS. `tracemalloc`
    tracks numpy's allocation domain exactly and is deterministic; macOS
    `ru_maxrss` swings ~0.4 GiB run to run because memory compression evicts
    cold normals, which is enough to make a tight ratio flaky. RSS is still
    checked, as a loose real-memory ceiling.
    """
    small_files, small_rss, small_traced = _run_rss_probe(3)
    large_files, large_rss, large_traced = _run_rss_probe(10)
    assert small_files == large_files == 4 * 366

    for label, traced in (("3-year", small_traced), ("10-year", large_traced)):
        gib = traced / (1024**3)
        assert gib < 2.0, f"{label} peak traced memory {gib:.2f} GiB exceeds the 2 GiB budget"
    for label, rss in (("3-year", small_rss), ("10-year", large_rss)):
        gib = rss / (1024**3)
        assert gib < 2.0, f"{label} peak RSS {gib:.2f} GiB exceeds the 2 GiB budget"

    # Measured growth over this 3.3x span is ~0.1%; 2% is generous slack that
    # still fails hard on any O(dataset) term (the old algorithm's 10-year
    # peak was ~3.3x its 3-year peak).
    assert large_traced <= small_traced * 1.02, (
        "peak memory grew with source years: "
        f"3y={small_traced / 1024**3:.3f} GiB 10y={large_traced / 1024**3:.3f} GiB"
    )
