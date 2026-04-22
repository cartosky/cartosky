from __future__ import annotations

import sys
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

from app.services import climatology
from scripts import build_climatology_baseline_assets as build_script


def _write_source(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0.0, 20.0, 10.0, 10.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def test_build_climatology_assets_writes_tmp2m_baseline(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    data_root = tmp_path / "data"
    climatology.configure_data_root(data_root)

    _write_source(source_root / "1991010100_tmp2m.tif", np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32))
    _write_source(source_root / "1992010100_tmp2m.tif", np.array([[10.0, 20.0], [30.0, 40.0]], dtype=np.float32))

    monkeypatch.setattr(
        build_script,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )

    files_written, missing_buckets = build_script.build_climatology_assets(
        source_root=source_root,
        data_root=data_root,
        version="v1",
        baseline_source="era5",
        field="tmp2m",
        region="conus",
        reference_period="1991-2020",
        units_in="C",
        smoothing_window_days=1,
        resampling="bilinear",
        start_year=1991,
        end_year=1992,
        require_complete=False,
    )

    assert files_written == 1
    assert missing_buckets == 1463

    baseline_path = climatology.climatology_baseline_path(
        version="v1",
        baseline_source="era5",
        field="tmp2m",
        region="conus",
        reference_period="1991-2020",
        valid_time=build_script.datetime(2026, 1, 1, 0, tzinfo=build_script.timezone.utc),
    )
    assert baseline_path.is_file()

    with rasterio.open(baseline_path) as ds:
        loaded = ds.read(1)
        assert ds.crs.to_epsg() == 3857
        assert ds.tags(1)["reference_period"] == "1991-2020"
        assert ds.tags(1)["sample_count"] == "2"

    expected = np.array([[41.0, 59.0], [77.0, 95.0]], dtype=np.float32)
    assert np.allclose(loaded, expected)
