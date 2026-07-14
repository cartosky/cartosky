from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
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

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app.services.grid import build_grid_for_run

pytestmark = pytest.mark.anyio


def _reset_main_caches() -> None:
    with main_module._ds_cache_lock:
        for ds in main_module._ds_cache.values():
            try:
                ds.close()
            except Exception:
                pass
        main_module._ds_cache.clear()

    with main_module._sample_lock:
        main_module._sample_cache.clear()
        main_module._sample_inflight.clear()
        main_module._sample_rate_window.clear()

    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()
    main_module._sample_transformer.cache_clear()


def _write_value_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [10.0, 20.0, 30.0],
            [40.0, 50.0, 60.0],
            [70.0, 80.0, 90.0],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_precip_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [0.10, 0.20, 0.30],
            [0.40, 0.50, 0.60],
            [0.70, 0.80, 0.90],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_wind_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [12.3, 15.6, 18.9],
            [21.0, 24.5, 27.8],
            [30.2, 33.4, 36.7],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_wind850_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [25.0, 30.0, 35.0],
            [40.0, 45.0, 50.0],
            [55.0, 60.0, 65.0],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_wind300_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [70.0, 75.0, 80.0],
            [85.0, 90.0, 95.0],
            [100.0, 105.0, 110.0],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_snow_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [1.2, 2.4, 3.6],
            [4.8, 6.0, 7.2],
            [8.4, 9.6, 10.8],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_cape_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [100.0, 200.0, 300.0],
            [400.0, 500.0, 600.0],
            [700.0, 800.0, 900.0],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


def _write_tmp850_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [-8.0, -7.0, -6.0],
            [-5.0, -4.0, -3.0],
            [-2.0, -1.0, 0.0],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(data, 1)


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"

    # The fixture publishes COG-only frames: opt the model out of the (now
    # default) binary-only substrate.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "gefs")

    model = "gefs"
    run_id = "20260330_12z"
    variable = "tmp2m"
    runtime_var = "tmp2m__mean"
    tmp850_variable = "tmp850"
    tmp850_runtime_var = "tmp850__mean"
    wspd850_variable = "wspd850"
    wspd850_runtime_var = "wspd850__mean"
    wspd300_variable = "wspd300"
    wspd300_runtime_var = "wspd300__mean"
    sbcape_variable = "sbcape"
    sbcape_runtime_var = "sbcape__mean"
    snowfall_variable = "snowfall_total"
    snowfall_runtime_var = "snowfall_total__mean"
    wspd_variable = "wspd10m"
    wspd_runtime_var = "wspd10m__mean"
    pwat_variable = "pwat"
    pwat_runtime_var = "pwat__mean"
    precip_variable = "precip_total"
    precip_runtime_var = "precip_total__mean"

    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "model": model,
                "run": run_id,
                "variables": {
                    variable: {
                        "display_name": "Surface Temp (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    tmp850_variable: {
                        "display_name": "850mb Temp (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    wspd850_variable: {
                        "display_name": "850mb Heights + Winds (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    wspd300_variable: {
                        "display_name": "300mb Heights + Winds (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    sbcape_variable: {
                        "display_name": "Surface-Based CAPE (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    snowfall_variable: {
                        "display_name": "Total Snowfall (10:1) (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 6, "valid_time": "2026-03-30T18:00:00Z"},
                        ],
                    },
                    wspd_variable: {
                        "display_name": "10m Wind Speed (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    pwat_variable: {
                        "display_name": "Precipitable Water (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-30T12:00:00Z"},
                        ],
                    },
                    precip_variable: {
                        "display_name": "Total Precip (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 6, "valid_time": "2026-03-30T18:00:00Z"},
                        ],
                    },
                },
            }
        )
    )

    model_root = published_root / model
    model_root.mkdir(parents=True, exist_ok=True)
    (model_root / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    var_dir = model_root / run_id / runtime_var
    var_dir.mkdir(parents=True, exist_ok=True)
    _write_value_raster(var_dir / "fh000.val.cog.tif")
    (var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "F",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "Surface Temp (Mean)",
            }
        )
    )

    tmp850_var_dir = model_root / run_id / tmp850_runtime_var
    tmp850_var_dir.mkdir(parents=True, exist_ok=True)
    _write_tmp850_raster(tmp850_var_dir / "fh000.val.cog.tif")
    (tmp850_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "C",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "850mb Temp (Mean)",
            }
        )
    )

    wspd850_var_dir = model_root / run_id / wspd850_runtime_var
    wspd850_var_dir.mkdir(parents=True, exist_ok=True)
    _write_wind850_raster(wspd850_var_dir / "fh000.val.cog.tif")
    (wspd850_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "kt",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "850mb Heights + Winds (Mean)",
            }
        )
    )

    wspd300_var_dir = model_root / run_id / wspd300_runtime_var
    wspd300_var_dir.mkdir(parents=True, exist_ok=True)
    _write_wind300_raster(wspd300_var_dir / "fh000.val.cog.tif")
    (wspd300_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "kt",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "300mb Heights + Winds (Mean)",
            }
        )
    )

    sbcape_var_dir = model_root / run_id / sbcape_runtime_var
    sbcape_var_dir.mkdir(parents=True, exist_ok=True)
    _write_cape_raster(sbcape_var_dir / "fh000.val.cog.tif")
    (sbcape_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "J/kg",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "Surface-Based CAPE (Mean)",
            }
        )
    )

    snowfall_var_dir = model_root / run_id / snowfall_runtime_var
    snowfall_var_dir.mkdir(parents=True, exist_ok=True)
    _write_snow_raster(snowfall_var_dir / "fh006.val.cog.tif")
    (snowfall_var_dir / "fh006.json").write_text(
        json.dumps(
            {
                "units": "in",
                "valid_time": "2026-03-30T18:00:00Z",
                "kind": "continuous",
                "display_name": "Total Snowfall (10:1) (Mean)",
            }
        )
    )

    wspd_var_dir = model_root / run_id / wspd_runtime_var
    wspd_var_dir.mkdir(parents=True, exist_ok=True)
    _write_wind_raster(wspd_var_dir / "fh000.val.cog.tif")
    (wspd_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "mph",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "10m Wind Speed (Mean)",
            }
        )
    )

    pwat_var_dir = model_root / run_id / pwat_runtime_var
    pwat_var_dir.mkdir(parents=True, exist_ok=True)
    _write_precip_raster(pwat_var_dir / "fh000.val.cog.tif")
    (pwat_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "in",
                "valid_time": "2026-03-30T12:00:00Z",
                "kind": "continuous",
                "display_name": "Precipitable Water (Mean)",
            }
        )
    )

    precip_var_dir = model_root / run_id / precip_runtime_var
    precip_var_dir.mkdir(parents=True, exist_ok=True)
    _write_precip_raster(precip_var_dir / "fh006.val.cog.tif")
    (precip_var_dir / "fh006.json").write_text(
        json.dumps(
            {
                "units": "in",
                "valid_time": "2026-03-30T18:00:00Z",
                "kind": "continuous",
                "display_name": "Total Precip (Mean)",
            }
        )
    )

    build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(runtime_var, tmp850_runtime_var, wspd850_runtime_var, wspd300_runtime_var, sbcape_runtime_var, snowfall_runtime_var, wspd_runtime_var, pwat_runtime_var, precip_runtime_var),
    )

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    _reset_main_caches()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


async def test_gefs_bootstrap_defaults_to_mean_ensemble_view(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v4/bootstrap",
        params={"model": "gefs", "run": "latest", "var": "tmp2m", "region": "conus"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"] == {
        "model": "gefs",
        "run": "20260330_12z",
        "variable": "tmp2m",
        "ensemble_view": "mean",
        "region": "conus",
    }
    assert [frame["fh"] for frame in payload["frames"]] == [0]
    assert payload["frames"][0]["meta"]["meta"]["valid_time"] == "2026-03-30T12:00:00Z"


async def test_gefs_frames_and_sample_default_to_mean_without_explicit_query(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/tmp2m/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True
    assert frames[0]["meta"]["meta"]["valid_time"] == "2026-03-30T12:00:00Z"

    explicit_frames_response = await client.get(
        "/api/v4/gefs/latest/tmp2m/frames",
        params={"ensemble_view": "mean"},
    )
    assert explicit_frames_response.status_code == 200
    assert explicit_frames_response.json() == frames

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "tmp2m",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "tmp2m"
    assert sample_payload["valid_time"] == "2026-03-30T12:00:00Z"
    assert sample_payload["units"] == "F"
    assert sample_payload["value"] == 10.0


async def test_gefs_grid_manifest_keeps_canonical_var_but_runtime_artifact_urls(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v4/gefs/latest/tmp2m/grid-manifest",
        params={"ensemble_view": "mean"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subtype"] == "grid"
    assert payload["var"] == "tmp2m"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/tmp2m__mean/fh000.l0.u16.bin?v=20260330_12z-tmp2m__mean-"
    )


async def test_gefs_tmp850_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/tmp850/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/tmp850/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "tmp850"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/tmp850__mean/fh000.l0.u16.bin?v=20260330_12z-tmp850__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "tmp850",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "tmp850"
    assert sample_payload["valid_time"] == "2026-03-30T12:00:00Z"
    assert sample_payload["units"] == "C"
    assert sample_payload["value"] == -8.0


async def test_gefs_wspd850_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/wspd850/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/wspd850/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "wspd850"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/wspd850__mean/fh000.l0.u16.bin?v=20260330_12z-wspd850__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "wspd850",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "wspd850"
    assert sample_payload["valid_time"] == "2026-03-30T12:00:00Z"
    assert sample_payload["units"] == "kt"
    assert sample_payload["value"] == 25.0


async def test_gefs_wspd300_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/wspd300/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/wspd300/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "wspd300"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/wspd300__mean/fh000.l0.u16.bin?v=20260330_12z-wspd300__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "wspd300",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "wspd300"
    assert sample_payload["valid_time"] == "2026-03-30T12:00:00Z"
    assert sample_payload["units"] == "kt"
    assert sample_payload["value"] == 70.0


async def test_gefs_sbcape_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/sbcape/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/sbcape/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "sbcape"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/sbcape__mean/fh000.l0.u16.bin?v=20260330_12z-sbcape__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "sbcape",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "sbcape"
    assert sample_payload["valid_time"] == "2026-03-30T12:00:00Z"
    assert sample_payload["units"] == "J/kg"
    assert sample_payload["value"] == 100.0


async def test_gefs_snowfall_total_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/snowfall_total/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [6]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/snowfall_total/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "snowfall_total"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 6
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/snowfall_total__mean/fh006.l0.u16.bin?v=20260330_12z-snowfall_total__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "snowfall_total",
            "fh": 6,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "snowfall_total"
    assert sample_payload["valid_time"] == "2026-03-30T18:00:00Z"
    assert sample_payload["units"] == "in"
    assert sample_payload["value"] == 1.2


async def test_gefs_wspd10m_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/wspd10m/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/wspd10m/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "wspd10m"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/wspd10m__mean/fh000.l0.u16.bin?v=20260330_12z-wspd10m__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "wspd10m",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "wspd10m"
    assert sample_payload["valid_time"] == "2026-03-30T12:00:00Z"
    assert sample_payload["units"] == "mph"
    assert sample_payload["value"] == 12.3


async def test_gefs_pwat_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/pwat/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True

    manifest_response = await client.get(
        "/api/v4/gefs/latest/pwat/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "pwat"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/pwat__mean/fh000.l0.u16.bin?v=20260330_12z-pwat__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "pwat",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "pwat"
    assert sample_payload["units"] == "in"
    assert sample_payload["value"] == 0.1


async def test_gefs_precip_total_mean_uses_canonical_api_var_and_runtime_artifacts(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/gefs/latest/precip_total/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [6]
    assert frames[0]["has_cog"] is True
    assert frames[0]["meta"]["meta"]["valid_time"] == "2026-03-30T18:00:00Z"

    manifest_response = await client.get(
        "/api/v4/gefs/latest/precip_total/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "precip_total"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 6
    assert frame["url"].startswith(
        "/api/v4/grid/gefs/20260330_12z/precip_total__mean/fh006.l0.u16.bin?v=20260330_12z-precip_total__mean-"
    )

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "gefs",
            "run": "latest",
            "var": "precip_total",
            "ensemble_view": "mean",
            "fh": 6,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260330_12z"
    assert sample_payload["var"] == "precip_total"
    assert sample_payload["valid_time"] == "2026-03-30T18:00:00Z"
    assert sample_payload["units"] == "in"
    assert sample_payload["value"] == 0.1


async def test_gefs_rejects_unsupported_ensemble_view(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v4/gefs/latest/tmp2m/frames",
        params={"ensemble_view": "spread"},
    )

    assert response.status_code == 404
    assert "Unsupported ensemble_view" in response.text