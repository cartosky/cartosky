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
    assert sidecar_metadata["window_start_fh"] == 0
    assert sidecar_metadata["window_end_fh"] == 120
    assert sidecar_metadata["accumulation_window_hours"] == 120
    assert sidecar_metadata["baseline_reference_fh"] == 0
    assert sidecar_metadata["model_accumulation_units"] == "in"


def test_precip_anomaly_rolls_accumulation_window_and_baseline_start_doy(
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
    window_start_date = datetime(2026, 1, 2, 0, tzinfo=timezone.utc)
    _write_baseline(
        climatology.climatology_accumulation_baseline_path(
            version="v1",
            baseline_source="era5",
            field="precip_5d",
            region="na",
            reference_period="1991-2020",
            reference_date=run_date,
        ),
        np.full((2, 2), 99.0, dtype=np.float32),
        transform,
    )
    _write_baseline(
        climatology.climatology_accumulation_baseline_path(
            version="v1",
            baseline_source="era5",
            field="precip_5d",
            region="na",
            reference_period="1991-2020",
            reference_date=window_start_date,
        ),
        np.full((2, 2), 0.25, dtype=np.float32),
        transform,
    )

    fetch_calls: list[dict[str, object]] = []

    def _fake_fetch_component_warped(**kwargs):
        fetch_calls.append(dict(kwargs))
        fh = int(kwargs["fh"])
        value_by_fh = {
            24: 25.4,
            144: 50.8,
        }
        return np.full((2, 2), value_by_fh[fh], dtype=np.float32), rasterio.crs.CRS.from_epsg(3857), transform

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
        fh=144,
        var_spec_model=var_spec,
        var_capability=var_capability,
        model_plugin=AIGFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid={"region": "na", "id": "aigfs:na:10.0m"},
        derive_component_resampling="bilinear",
    )

    assert [call["fh"] for call in fetch_calls] == [144, 24]
    assert np.allclose(anomaly, np.full((2, 2), 0.75, dtype=np.float32), atol=1.0e-5)
    assert crs.to_epsg() == 3857
    assert anomaly_transform == transform

    sidecar_metadata = ctx.derive_quality[("precip_5d_anom", 144)]["sidecar_metadata"]
    assert sidecar_metadata["baseline_alignment"] == "window_start_date"
    assert sidecar_metadata["baseline_reference_doy"] == 2
    assert sidecar_metadata["target_fh"] == 144
    assert sidecar_metadata["window_start_fh"] == 24
    assert sidecar_metadata["window_end_fh"] == 144
    assert sidecar_metadata["accumulation_window_hours"] == 120
    assert sidecar_metadata["baseline_reference_fh"] == 24


def test_precip_anomaly_target_lead_constraints_and_unsupported_model_gating() -> None:
    expected = {
        "precip_5d_anom": 120,
        "precip_7d_anom": 168,
        "precip_10d_anom": 240,
        "precip_16d_anom": 384,
    }
    for var_key, target_fh in expected.items():
        capability = GFS_MODEL.get_var_capability(var_key)
        assert capability is not None
        assert capability.default_fh == target_fh
        assert capability.derive_strategy_id == "precip_accum_anomaly_departure"
        if var_key == "precip_16d_anom":
            assert capability.constraints == {"min_fh": target_fh, "max_fh": target_fh}
            assert GFS_MODEL.scheduled_fhs_for_var(var_key, 0) == [target_fh]
        else:
            assert capability.constraints == {"min_fh": target_fh}
            scheduled_fhs = GFS_MODEL.scheduled_fhs_for_var(var_key, 0)
            assert scheduled_fhs[0] == target_fh
            assert scheduled_fhs[-1] == GFS_MODEL.target_fhs(0)[-1]
            assert len(scheduled_fhs) > 1

    for unsupported_model in (NAM_MODEL, NBM_MODEL):
        for var_key in expected:
            assert unsupported_model.get_var_capability(var_key) is None


def test_precip_anomaly_colormap_and_legend_steps() -> None:
    from app.services.colormaps import get_color_map_spec

    expected_top_down_steps = [
        (5.0, "#b5f1fb"),
        (4.5, "#97d3fb"),
        (4.0, "#78b9fb"),
        (3.5, "#50a5f5"),
        (3.0, "#3c97f5"),
        (2.5, "#3083f1"),
        (2.0, "#2b6eeb"),
        (1.8, "#2b6eeb"),
        (1.6, "#467847"),
        (1.4, "#4a874d"),
        (1.2, "#529d5a"),
        (1.0, "#5aaf62"),
        (0.8, "#7cc378"),
        (0.6, "#9bd18c"),
        (0.4, "#b7dfa7"),
        (0.2, "#c9e9b9"),
        (-0.2, "#ffffff"),
        (-0.4, "#efddcb"),
        (-0.6, "#e1c3ad"),
        (-0.8, "#c7ab95"),
        (-1.0, "#b39987"),
        (-1.2, "#9f8977"),
        (-1.4, "#8b7668"),
        (-1.6, "#776658"),
        (-1.8, "#64544a"),
        (-2.0, "#a62021"),
        (-2.5, "#b52828"),
        (-3.0, "#c93c3c"),
        (-3.5, "#d54f4f"),
        (-4.0, "#e16464"),
        (-4.5, "#e58281"),
        (-5.0, "#f5a1a1"),
        (-5.5, "#fbc9c9"),
    ]
    expected_ascending_steps = list(reversed(expected_top_down_steps))

    spec = get_color_map_spec("precip_anom")

    assert spec["range"] == (-5.5, 5.5)
    assert list(zip(spec["levels"], spec["colors"])) == expected_ascending_steps
    assert spec["legend_stops"] == expected_ascending_steps
    assert list(reversed(spec["legend_stops"])) == expected_top_down_steps

    color_by_level = dict(spec["legend_stops"])
    assert color_by_level[-0.4] == "#efddcb"
    assert color_by_level[-0.2] == "#ffffff"
    assert color_by_level[0.2] == "#c9e9b9"
    assert color_by_level[0.4] == "#b7dfa7"

def test_precip_anomaly_grid_packing_supported_for_exposed_products() -> None:
    pytest.importorskip("brotli")

    from app.services.grid import _PACKING_BY_MODEL_VAR, grid_code_supported

    expected_vars = (
        "precip_5d_anom",
        "precip_7d_anom",
        "precip_10d_anom",
        "precip_15d_anom",
        "precip_16d_anom",
    )
    for model_id in ("gfs", "ecmwf", "aigfs"):
        for var_key in expected_vars:
            if var_key == "precip_15d_anom":
                continue
            assert grid_code_supported(model_id, var_key)
            assert _PACKING_BY_MODEL_VAR[(model_id, var_key)] == {
                "scale": 0.01,
                "offset": -128.0,
                "nodata": 65535,
                "units": "in",
            }

    for var_key in ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_15d_anom"):
        assert grid_code_supported("aifs", var_key)
        assert _PACKING_BY_MODEL_VAR[("aifs", var_key)] == {
            "scale": 0.01,
            "offset": -128.0,
            "nodata": 65535,
            "units": "in",
        }

    for var_key in ("precip_15d_anom",):
        assert grid_code_supported("eps", var_key)
        assert grid_code_supported("eps", f"{var_key}__mean")

    for var_key in ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_16d_anom"):
        assert grid_code_supported("gefs", var_key)
        assert grid_code_supported("gefs", f"{var_key}__mean")