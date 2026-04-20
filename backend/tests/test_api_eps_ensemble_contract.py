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


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"

    model = "eps"
    run_id = "20260419_00z"
    variable = "tmp2m"
    runtime_var = "tmp2m__mean"
    wind_variable = "wspd10m"
    wind_runtime_var = "wspd10m__mean"

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
                            {"fh": 0, "valid_time": "2026-04-19T00:00:00Z"},
                        ],
                    },
                    wind_variable: {
                        "display_name": "10m Wind Speed (Mean)",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-04-19T00:00:00Z"},
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
                "valid_time": "2026-04-19T00:00:00Z",
                "kind": "continuous",
                "display_name": "Surface Temp (Mean)",
            }
        )
    )

    wind_var_dir = model_root / run_id / wind_runtime_var
    wind_var_dir.mkdir(parents=True, exist_ok=True)
    _write_value_raster(wind_var_dir / "fh000.val.cog.tif")
    (wind_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "units": "mph",
                "valid_time": "2026-04-19T00:00:00Z",
                "kind": "continuous",
                "display_name": "10m Wind Speed (Mean)",
            }
        )
    )

    build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(runtime_var, wind_runtime_var),
    )

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    _reset_main_caches()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


async def test_eps_bootstrap_defaults_to_mean_ensemble_view(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v4/bootstrap",
        params={"model": "eps", "run": "latest", "var": "tmp2m", "region": "conus"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"] == {
        "model": "eps",
        "run": "20260419_00z",
        "variable": "tmp2m",
        "ensemble_view": "mean",
        "region": "conus",
    }
    assert [frame["fh"] for frame in payload["frames"]] == [0]


async def test_eps_frames_sample_and_grid_manifest_use_canonical_var_with_runtime_mean_artifact(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/eps/latest/tmp2m/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True
    assert frames[0]["meta"]["meta"]["valid_time"] == "2026-04-19T00:00:00Z"

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "eps",
            "run": "latest",
            "var": "tmp2m",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260419_00z"
    assert sample_payload["var"] == "tmp2m"
    assert sample_payload["units"] == "F"
    assert sample_payload["value"] == 10.0

    manifest_response = await client.get(
        "/api/v4/eps/latest/tmp2m/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "tmp2m"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/eps/20260419_00z/tmp2m__mean/fh000.l0.u16.bin?v=20260419_00z-tmp2m__mean-"
    )


async def test_eps_wspd10m_frames_sample_and_grid_manifest_use_canonical_var_with_runtime_mean_artifact(client: httpx.AsyncClient) -> None:
    frames_response = await client.get("/api/v4/eps/latest/wspd10m/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["has_cog"] is True
    assert frames[0]["meta"]["meta"]["valid_time"] == "2026-04-19T00:00:00Z"

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "eps",
            "run": "latest",
            "var": "wspd10m",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    sample_payload = sample_response.json()
    assert sample_payload["run"] == "20260419_00z"
    assert sample_payload["var"] == "wspd10m"
    assert sample_payload["units"] == "mph"
    assert sample_payload["value"] == 10.0

    manifest_response = await client.get(
        "/api/v4/eps/latest/wspd10m/grid-manifest",
        params={"ensemble_view": "mean"},
    )
    assert manifest_response.status_code == 200
    payload = manifest_response.json()
    assert payload["var"] == "wspd10m"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith(
        "/api/v4/grid/eps/20260419_00z/wspd10m__mean/fh000.l0.u16.bin?v=20260419_00z-wspd10m__mean-"
    )


async def test_eps_rejects_unsupported_ensemble_view(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v4/eps/latest/tmp2m/frames",
        params={"ensemble_view": "spread"},
    )

    assert response.status_code == 404
    assert "Unsupported ensemble_view" in response.text