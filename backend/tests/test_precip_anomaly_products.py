from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.aigfs import AIGFS_MODEL
from app.models.gfs import GFS_MODEL
from app.models.nam import NAM_MODEL
from app.models.nbm import NBM_MODEL
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


def test_precip_accumulation_baseline_path_uses_doy_without_hour(tmp_path: Path) -> None:
    path = climatology.climatology_accumulation_baseline_path(
        data_root=tmp_path,
        version="v1",
        baseline_source="era5",
        field="precip_5d",
        region="na",
        reference_period="1991-2020",
        reference_date=datetime(2026, 1, 1, 18, tzinfo=timezone.utc),
    )

    assert path == tmp_path / "climatology" / "v1" / "era5" / "baseline" / "precip_5d" / "na" / "1991-2020" / "doy_001.tif"
    assert "_h" not in path.name


def test_precip_anomaly_uses_init_doy_target_lead_and_inches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        climatology,
        "get_baseline_grid_params",
        lambda baseline_source, region: ((0.0, 0.0, 20.0, 20.0), 10.0),
    )
    climatology.configure_data_root(tmp_path)
    transform, height, width = compute_transform_and_shape((0.0, 0.0, 20.0, 20.0), 10.0)
    assert (height, width) == (2, 2)

    run_date = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
    ending_valid_date = datetime(2026, 1, 6, 0, tzinfo=timezone.utc)
    _write_baseline(
        climatology.climatology_accumulation_baseline_path(
            version="v1",
            baseline_source="era5",
            field="precip_5d",
            region="na",
            reference_period="1991-2020",
            reference_date=run_date,
        ),
        np.full((2, 2), 0.25, dtype=np.float32),
        transform,
    )
    _write_baseline(
        climatology.climatology_accumulation_baseline_path(
            version="v1",
            baseline_source="era5",
            field="precip_5d",
            region="na",
            reference_period="1991-2020",
            reference_date=ending_valid_date,
        ),
        np.full((2, 2), 99.0, dtype=np.float32),
        transform,
    )

    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        return np.full((2, 2), 25.4, dtype=np.float32), rasterio.crs.CRS.from_epsg(3857), transform

    monkeypatch.setattr("app.services.builder.derive._fetch_component_warped", _fake_fetch_component_warped)

    ctx = FetchContext()
    var_spec = AIGFS_MODEL.get_var("precip_5d_anom")
    var_capability = AIGFS_MODEL.get_var_capability("precip_5d_anom")
    assert var_spec is not None
    assert var_capability is not None

    anomaly, crs, anomaly_transform = derive_variable(
        model_id="aigfs",
        var_key="precip_5d_anom",
        product="sfc",
        run_date=run_date,
        fh=120,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=AIGFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "aigfs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert fetch_calls
    assert fetch_calls[0]["var_key"] == "precip_total"
    assert fetch_calls[0]["fh"] == 120
    assert np.allclose(anomaly, np.full((2, 2), 0.75, dtype=np.float32), atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    sidecar_metadata = ctx.derive_quality[("precip_5d_anom", 120)]["sidecar_metadata"]
    assert sidecar_metadata["anomaly_kind"] == "accumulated_precip_departure"
    assert sidecar_metadata["baseline_field"] == "precip_5d"
    assert sidecar_metadata["baseline_alignment"] == "init_date"
    assert sidecar_metadata["baseline_reference_doy"] == 1
    assert sidecar_metadata["target_fh"] == 120
    assert sidecar_metadata["model_accumulation_units"] == "in"


def test_precip_anomaly_target_lead_constraints_and_unsupported_model_gating() -> None:
    expected = {
        "precip_5d_anom": 120,
        "precip_7d_anom": 168,
        "precip_10d_anom": 240,
        "precip_15d_anom": 360,
    }
    for var_key, target_fh in expected.items():
        capability = GFS_MODEL.get_var_capability(var_key)
        assert capability is not None
        assert capability.default_fh == target_fh
        assert capability.derive_strategy_id == "precip_accum_anomaly_departure"
        assert capability.constraints == {"min_fh": target_fh, "max_fh": target_fh}
        assert GFS_MODEL.scheduled_fhs_for_var(var_key, 0) == [target_fh]

    for unsupported_model in (NAM_MODEL, NBM_MODEL):
        for var_key in expected:
            assert unsupported_model.get_var_capability(var_key) is None