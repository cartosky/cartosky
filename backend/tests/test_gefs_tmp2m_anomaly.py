from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.gefs import GEFS_MODEL
from app.services import climatology
from app.services.builder.cog_writer import compute_transform_and_shape
from app.services.builder.derive import FetchContext, derive_variable


def _write_baseline(path: Path, data: np.ndarray, transform) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform,
        nodata=float("nan"),
    ) as ds:
        ds.write(data.astype(np.float32), 1)


def test_load_climatology_baseline_validates_expected_grid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(climatology, "get_grid_params", lambda model, region: ((0.0, 0.0, 20.0, 20.0), 10.0))
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    assert (height, width) == data.shape
    baseline_path = climatology.climatology_baseline_path(
        version="v1",
        model_family="gefs",
        field="tmp2m",
        valid_time=valid_time,
    )
    _write_baseline(baseline_path, data, transform)

    loaded, crs, loaded_transform, meta = climatology.load_climatology_baseline(
        version="v1",
        model_family="gefs",
        field="tmp2m",
        valid_time=valid_time,
        region="conus",
        reference_period="1991-2020",
    )

    assert np.array_equal(loaded, data)
    assert crs.to_epsg() == 3857
    assert loaded_transform == transform
    assert meta["baseline_version"] == "v1"
    assert meta["baseline_model_family"] == "gefs"
    assert meta["reference_period"] == "1991-2020"


def test_derive_gefs_tmp2m_anomaly_records_sidecar_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(climatology, "get_grid_params", lambda model, region: ((0.0, 0.0, 20.0, 20.0), 10.0))
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[50.0, 48.0], [46.0, 44.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            model_family="gefs",
            field="tmp2m",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[10.0, 9.0], [8.0, 7.0]], dtype=np.float32)

    def _fake_fetch_component_warped(**kwargs):
        del kwargs
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = GEFS_MODEL.get_var("tmp2m_anom")
    var_capability = GEFS_MODEL.get_var_capability("tmp2m_anom")
    assert var_spec is not None
    assert var_capability is not None

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="gefs",
        var_key="tmp2m_anom",
        product="atmos.5",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=GEFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "conus", "id": "gefs:conus:10.0m"},
        derive_component_resampling="bilinear",
    )

    expected_forecast_f = forecast_data_c * np.float32(9.0 / 5.0) + np.float32(32.0)
    expected = expected_forecast_f - baseline_data_f
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("tmp2m_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_model_family"] == "gefs"
    assert sidecar_metadata["reference_period"] == "1991-2020"