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

from app.models.aigfs import AIGFS_MODEL
from app.models.aifs import AIFS_MODEL
from app.models.ecmwf import ECMWF_MODEL
from app.models.eps import EPS_MODEL
from app.models.gfs import GFS_MODEL
from app.models.gefs import GEFS_MODEL
from app.services import climatology
from app.services.builder.cog_writer import compute_transform_and_shape
from app.services.builder.derive import FetchContext, _warp_component_to_target_grid, derive_variable
from app.services.builder.pipeline import _resolve_derive_target_grid


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
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    assert (height, width) == data.shape
    baseline_path = climatology.climatology_baseline_path(
        version="v1",
        baseline_source="era5",
        field="tmp2m",
        region="conus",
        reference_period="1991-2020",
        valid_time=valid_time,
    )
    _write_baseline(baseline_path, data, transform)

    loaded, crs, loaded_transform, meta = climatology.load_climatology_baseline(
        version="v1",
        baseline_source="era5",
        field="tmp2m",
        valid_time=valid_time,
        region="conus",
        reference_period="1991-2020",
    )

    assert np.array_equal(loaded, data)
    assert crs.to_epsg() == 3857
    assert loaded_transform == transform
    assert meta["baseline_version"] == "v1"
    assert meta["baseline_source"] == "era5"
    assert meta["baseline_region"] == "conus"
    assert meta["reference_period"] == "1991-2020"


def test_shared_baseline_path_is_reused_across_model_families(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, _, _ = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    shared_path = climatology.climatology_baseline_path(
        version="v1",
        baseline_source="era5",
        field="tmp2m",
        region="na",
        reference_period="1991-2020",
        valid_time=valid_time,
    )
    _write_baseline(shared_path, np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), transform)

    for consumer_model_family in ("gefs", "eps"):
        loaded, _, _, meta = climatology.load_climatology_baseline(
            version="v1",
            baseline_source="era5",
            field="tmp2m",
            valid_time=valid_time,
            region="na",
            reference_period="1991-2020",
            legacy_model_family_fallback=consumer_model_family,
        )
        assert np.array_equal(loaded, np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32))
        assert meta["baseline_source"] == "era5"
        assert meta["baseline_legacy_fallback"] is False

    assert list((tmp_path / "climatology").rglob("*.tif")) == [shared_path]


def test_load_climatology_baseline_falls_back_to_synoptic_hour_bucket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    requested_valid_time = datetime(2026, 4, 24, 9, tzinfo=timezone.utc)
    synoptic_valid_time = datetime(2026, 4, 24, 6, tzinfo=timezone.utc)
    transform, _, _ = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    baseline_path = climatology.climatology_baseline_path(
        version="v1",
        baseline_source="era5",
        field="hgt500",
        region="na",
        reference_period="1991-2020",
        valid_time=synoptic_valid_time,
    )
    _write_baseline(baseline_path, np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32), transform)

    loaded, crs, loaded_transform, meta = climatology.load_climatology_baseline(
        version="v1",
        baseline_source="era5",
        field="hgt500",
        valid_time=requested_valid_time,
        region="na",
        reference_period="1991-2020",
    )

    assert np.array_equal(loaded, np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32))
    assert crs.to_epsg() == 3857
    assert loaded_transform == transform
    assert meta["baseline_requested_hour"] == 9
    assert meta["baseline_resolved_hour"] == 6
    assert meta["baseline_legacy_fallback"] is False


def test_derive_gefs_tmp2m_anomaly_records_sidecar_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[50.0, 48.0], [46.0, 44.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp2m",
            region="na",
            reference_period="1991-2020",
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
    assert var_spec.selectors.hints["baseline_region"] == "na"

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
        derive_component_target_grid={"region": "na", "id": "gefs:na:10.0m"},
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
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_region"] == "na"
    assert sidecar_metadata["reference_period"] == "1991-2020"


def test_derive_gefs_hgt500_anomaly_uses_mean_height_component_and_dam_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_dam = np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="hgt500",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_dam,
        transform,
    )

    forecast_data_m = np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_m, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = GEFS_MODEL.get_var("hgt500_anom")
    var_capability = GEFS_MODEL.get_var_capability("hgt500_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500__mean"
    assert var_spec.selectors.hints["contour_conversion"] == "m_to_dam"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="gefs",
        var_key="hgt500_anom",
        product="atmos.5",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=GEFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "gefs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "hgt500__mean"
    expected_forecast_dam = forecast_data_m / np.float32(10.0)
    expected = expected_forecast_dam - baseline_data_dam
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("hgt500_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_field"] == "hgt500"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_eps_hgt500_anomaly_uses_mean_height_component_and_dam_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_dam = np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="hgt500",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_dam,
        transform,
    )

    forecast_data_m = np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_m, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = EPS_MODEL.get_var("hgt500_anom")
    var_capability = EPS_MODEL.get_var_capability("hgt500_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500__mean"
    assert var_spec.selectors.hints["contour_conversion"] == "m_to_dam"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="eps",
        var_key="hgt500_anom",
        product="enfo",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=EPS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "eps:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "hgt500__mean"
    expected_forecast_dam = forecast_data_m / np.float32(10.0)
    expected = expected_forecast_dam - baseline_data_dam
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("hgt500_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_field"] == "hgt500"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_eps_tmp2m_anomaly_uses_mean_tmp2m_component_and_era5_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[50.0, 48.0], [46.0, 44.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp2m",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[10.0, 9.0], [8.0, 7.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = EPS_MODEL.get_var("tmp2m_anom__mean")
    var_capability = EPS_MODEL.get_var_capability("tmp2m_anom__mean")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="eps",
        var_key="tmp2m_anom__mean",
        product="enfo",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=EPS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "eps:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "tmp2m__mean"
    expected_forecast_f = forecast_data_c * np.float32(9.0 / 5.0) + np.float32(32.0)
    expected = expected_forecast_f - baseline_data_f
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("tmp2m_anom__mean", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_field"] == "tmp2m"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_gfs_hgt500_anomaly_uses_raw_height_component_and_dam_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_dam = np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="hgt500",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_dam,
        transform,
    )

    forecast_data_m = np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_m, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = GFS_MODEL.get_var("hgt500_anom")
    var_capability = GFS_MODEL.get_var_capability("hgt500_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500"
    assert var_spec.selectors.hints["contour_conversion"] == "m_to_dam"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="gfs",
        var_key="hgt500_anom",
        product="pgrb2.0p25",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=GFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "gfs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "hgt500"
    expected_forecast_dam = forecast_data_m / np.float32(10.0)
    expected = expected_forecast_dam - baseline_data_dam
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("hgt500_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_field"] == "hgt500"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_gfs_tmp2m_anomaly_uses_raw_tmp2m_component_and_era5_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[50.0, 48.0], [46.0, 44.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp2m",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[10.0, 9.0], [8.0, 7.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = GFS_MODEL.get_var("tmp2m_anom")
    var_capability = GFS_MODEL.get_var_capability("tmp2m_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="gfs",
        var_key="tmp2m_anom",
        product="pgrb2.0p25",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=GFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "gfs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "tmp2m"
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
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_field"] == "tmp2m"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_gfs_tmp850_anomaly_uses_raw_tmp850_component_and_era5_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[35.0, 32.0], [29.0, 26.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp850",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[2.0, 1.0], [0.0, -1.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = GFS_MODEL.get_var("tmp850_anom")
    var_capability = GFS_MODEL.get_var_capability("tmp850_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="gfs",
        var_key="tmp850_anom",
        product="pgrb2.0p25",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=GFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "gfs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "tmp850"
    expected_forecast_f = forecast_data_c * np.float32(9.0 / 5.0) + np.float32(32.0)
    expected = expected_forecast_f - baseline_data_f
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("tmp850_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_field"] == "tmp850"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_ecmwf_hgt500_anomaly_uses_raw_height_component_and_dam_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_dam = np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="hgt500",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_dam,
        transform,
    )

    forecast_data_m = np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_m, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = ECMWF_MODEL.get_var("hgt500_anom")
    var_capability = ECMWF_MODEL.get_var_capability("hgt500_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500"
    assert var_spec.selectors.hints["contour_conversion"] == "m_to_dam"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="ecmwf",
        var_key="hgt500_anom",
        product="oper",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=ECMWF_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "ecmwf:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "hgt500"
    expected_forecast_dam = forecast_data_m / np.float32(10.0)
    expected = expected_forecast_dam - baseline_data_dam
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("hgt500_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_field"] == "hgt500"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_ecmwf_tmp2m_anomaly_uses_raw_tmp2m_component_and_era5_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[50.0, 48.0], [46.0, 44.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp2m",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[10.0, 9.0], [8.0, 7.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = ECMWF_MODEL.get_var("tmp2m_anom")
    var_capability = ECMWF_MODEL.get_var_capability("tmp2m_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="ecmwf",
        var_key="tmp2m_anom",
        product="oper",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=ECMWF_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "ecmwf:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "tmp2m"
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
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_field"] == "tmp2m"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_ecmwf_tmp850_anomaly_uses_raw_tmp850_component_and_era5_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[35.0, 32.0], [29.0, 26.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp850",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[2.0, 1.0], [0.0, -1.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = ECMWF_MODEL.get_var("tmp850_anom")
    var_capability = ECMWF_MODEL.get_var_capability("tmp850_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="ecmwf",
        var_key="tmp850_anom",
        product="oper",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=ECMWF_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "ecmwf:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "tmp850"
    expected_forecast_f = forecast_data_c * np.float32(9.0 / 5.0) + np.float32(32.0)
    expected = expected_forecast_f - baseline_data_f
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("tmp850_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_field"] == "tmp850"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_aifs_hgt500_anomaly_uses_geopotential_height_component_and_dam_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_dam = np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="hgt500",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_dam,
        transform,
    )

    forecast_data_geopotential = np.array(
        [[54730.987, 54142.087], [53553.187, 52964.287]],
        dtype=np.float32,
    )
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_geopotential, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = AIFS_MODEL.get_var("hgt500_anom")
    var_capability = AIFS_MODEL.get_var_capability("hgt500_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500"
    assert var_spec.selectors.hints["contour_conversion"] == "geopotential_to_height_dam"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="aifs",
        var_key="hgt500_anom",
        product="oper",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=AIFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "aifs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "hgt500"
    expected_forecast_dam = (forecast_data_geopotential / np.float32(9.80665)) / np.float32(10.0)
    expected = expected_forecast_dam - baseline_data_dam
    assert np.allclose(anomaly, expected, atol=1.0e-3)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("hgt500_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_field"] == "hgt500"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_aifs_tmp850_anomaly_uses_raw_tmp850_component_and_era5_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_f = np.array([[35.0, 32.0], [29.0, 26.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="tmp850",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_f,
        transform,
    )

    forecast_data_c = np.array([[2.0, 1.0], [0.0, -1.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_c, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = AIFS_MODEL.get_var("tmp850_anom")
    var_capability = AIFS_MODEL.get_var_capability("tmp850_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="aifs",
        var_key="tmp850_anom",
        product="oper",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=AIFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "aifs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "tmp850"
    expected_forecast_f = forecast_data_c * np.float32(9.0 / 5.0) + np.float32(32.0)
    expected = expected_forecast_f - baseline_data_f
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("tmp850_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_source"] == "era5"
    assert sidecar_metadata["baseline_field"] == "tmp850"
    assert sidecar_metadata["baseline_region"] == "na"


def test_derive_aigfs_hgt500_anomaly_uses_raw_height_component_and_dam_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    baseline_data_dam = np.array([[552.0, 546.0], [540.0, 534.0]], dtype=np.float32)
    _write_baseline(
        climatology.climatology_baseline_path(
            version="v1",
            baseline_source="era5",
            field="hgt500",
            region="na",
            reference_period="1991-2020",
            valid_time=valid_time,
        ),
        baseline_data_dam,
        transform,
    )

    forecast_data_m = np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32)
    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return forecast_data_m, rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = AIGFS_MODEL.get_var("hgt500_anom")
    var_capability = AIGFS_MODEL.get_var_capability("hgt500_anom")
    assert var_spec is not None
    assert var_capability is not None
    assert var_spec.selectors.hints["baseline_region"] == "na"
    assert var_spec.selectors.hints["contour_component"] == "hgt500"
    assert var_spec.selectors.hints["contour_conversion"] == "m_to_dam"
    assert var_spec.selectors.hints["product"] == "pres"

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="aigfs",
        var_key="hgt500_anom",
        product="sfc",
        run_date=valid_time,
        fh=0,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=AIGFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "aigfs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "hgt500"
    expected_forecast_dam = forecast_data_m / np.float32(10.0)
    expected = expected_forecast_dam - baseline_data_dam
    assert np.allclose(anomaly, expected, atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    quality_meta = ctx.derive_quality[("hgt500_anom", 0)]
    sidecar_metadata = quality_meta.get("sidecar_metadata")
    assert isinstance(sidecar_metadata, dict)
    assert sidecar_metadata["anomaly_kind"] == "departure"
    assert sidecar_metadata["baseline_kind"] == "climatology"
    assert sidecar_metadata["baseline_version"] == "v1"
    assert sidecar_metadata["baseline_field"] == "hgt500"
    assert sidecar_metadata["baseline_region"] == "na"


def test_resolve_derive_target_grid_uses_baseline_region_for_anomaly_cache() -> None:
    target_grid, matches_output = _resolve_derive_target_grid(
        model="gefs",
        region="conus",
        hints={"baseline_source": "era5", "baseline_region": "na"},
        derive_component_warp_cache=True,
    )

    assert target_grid == {"region": "na", "id": "climatology:era5:na:25000.0m"}
    assert matches_output is False


def test_warp_component_to_target_grid_honors_climatology_grid_id() -> None:
    data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    src_transform = from_origin(0.0, 20.0, 10.0, 10.0)

    warped_data, warped_transform = _warp_component_to_target_grid(
        raw_data=data,
        raw_crs=rasterio.crs.CRS.from_epsg(3857),
        raw_transform=src_transform,
        model_id="eps",
        target_region="na",
        target_grid_id="climatology:era5:na:25000.0m",
        resampling="bilinear",
    )

    expected_transform, expected_height, expected_width = compute_transform_and_shape(
        climatology.REGION_BBOX_3857["na"],
        25000.0,
    )
    assert warped_data.shape == (expected_height, expected_width)
    assert warped_transform == expected_transform
