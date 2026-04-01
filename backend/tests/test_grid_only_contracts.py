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
os.environ.setdefault("TWF_CLIENT_ID", "test-client")
os.environ.setdefault("TWF_CLIENT_SECRET", "test-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("TWF_SCOPES", "profile forums_posts")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_grid_only_contracts.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import config as config_module
from app import main as main_module
from app.services.grid_v1 import build_grid_v1_for_run

pytestmark = pytest.mark.anyio


def _write_value_cog(path: Path, values: np.ndarray, *, pixel_size: float = 3000.0) -> None:
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
        transform=from_origin(-14920000.0, 7362000.0, pixel_size, pixel_size),
        nodata=np.nan,
    ) as ds:
        ds.write(values.astype(np.float32), 1)


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


def _enable_grid_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_GRID_V1_ENABLED", "1")
    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_enabled.cache_clear()
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()


def _configure_main_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_root: Path,
    manifests_root: Path,
    published_root: Path,
    loop_cache_root: Path,
) -> None:
    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    monkeypatch.setattr(main_module, "LOOP_CACHE_ROOT", loop_cache_root)
    _reset_main_caches()


def _write_manifest(manifests_root: Path, *, model: str, run_id: str, variable: str, frames: list[dict[str, object]]) -> None:
    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "model": model,
                "run": run_id,
                "variables": {
                    variable: {
                        "expected_frames": len(frames),
                        "available_frames": len(frames),
                        "frames": frames,
                    }
                },
            }
        )
    )


@pytest.fixture
async def forecast_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    loop_cache_root = tmp_path / "loop-cache"
    model = "hrrr"
    run_id = "20260330_12z"
    variable = "tmp2m"
    var_dir = published_root / model / run_id / variable

    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "kind": "continuous", "valid_time": "2026-03-30T12:00:00Z"})
    )

    (published_root / model / "LATEST.json").parent.mkdir(parents=True, exist_ok=True)
    (published_root / model / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    _write_manifest(
        manifests_root,
        model=model,
        run_id=run_id,
        variable=variable,
        frames=[{"fh": 0, "valid_time": "2026-03-30T12:00:00Z"}],
    )

    _enable_grid_v1(monkeypatch)
    build_grid_v1_for_run(data_root=data_root, model=model, run=run_id, workers=1, variables=(variable,))
    _configure_main_paths(
        monkeypatch,
        data_root=data_root,
        manifests_root=manifests_root,
        published_root=published_root,
        loop_cache_root=loop_cache_root,
    )

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


@pytest.fixture
async def observed_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    loop_cache_root = tmp_path / "loop-cache"
    model = "mrms"
    run_id = "20260330_1206z"
    variable = "reflectivity"
    var_dir = published_root / model / run_id / variable

    values = np.array([[0.0, 12.0], [24.0, np.nan]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values, pixel_size=1000.0)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "dBZ", "kind": "discrete", "valid_time": "2026-03-30T12:06:00Z"})
    )

    (published_root / model / "LATEST.json").parent.mkdir(parents=True, exist_ok=True)
    (published_root / model / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    _write_manifest(
        manifests_root,
        model=model,
        run_id=run_id,
        variable=variable,
        frames=[{"fh": 0, "valid_time": "2026-03-30T12:06:00Z"}],
    )

    _enable_grid_v1(monkeypatch)
    build_grid_v1_for_run(data_root=data_root, model=model, run=run_id, workers=1, variables=(variable,))
    _configure_main_paths(
        monkeypatch,
        data_root=data_root,
        manifests_root=manifests_root,
        published_root=published_root,
        loop_cache_root=loop_cache_root,
    )

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


async def test_forecast_grid_supported_selection_bootstraps_without_loop_artifacts(
    forecast_client: httpx.AsyncClient,
) -> None:
    capabilities_response = await forecast_client.get("/api/v4/capabilities")
    assert capabilities_response.status_code == 200
    capabilities_payload = capabilities_response.json()
    tmp2m = capabilities_payload["model_catalog"]["hrrr"]["variables"]["tmp2m"]
    assert tmp2m["render_substrates"] == ["legacy", "grid_webgl_v1"]
    assert capabilities_payload["model_catalog"]["hrrr"]["defaults"]["default_render_substrate"] == "grid_webgl_v1"

    bootstrap_response = await forecast_client.get(
        "/api/v4/bootstrap",
        params={"model": "hrrr", "run": "latest", "var": "tmp2m", "region": "conus"},
    )
    assert bootstrap_response.status_code == 200
    bootstrap_payload = bootstrap_response.json()
    assert bootstrap_payload["selection"] == {
        "model": "hrrr",
        "run": "20260330_12z",
        "variable": "tmp2m",
        "region": "conus",
    }
    assert [row["fh"] for row in bootstrap_payload["frames"]] == [0]

    frames_response = await forecast_client.get("/api/v4/hrrr/latest/tmp2m/frames")
    assert frames_response.status_code == 200
    frames_payload = frames_response.json()
    assert [row["fh"] for row in frames_payload] == [0]
    assert frames_payload[0]["meta"]["meta"]["valid_time"] == "2026-03-30T12:00:00Z"

    grid_manifest_response = await forecast_client.get("/api/v4/hrrr/20260330_12z/tmp2m/grid-manifest")
    assert grid_manifest_response.status_code == 200
    grid_manifest = grid_manifest_response.json()
    assert grid_manifest["subtype"] == "grid_webgl_v1"
    assert [frame["fh"] for frame in grid_manifest["lods"][0]["frames"]] == [0]

    loop_cache_root = Path(main_module.LOOP_CACHE_ROOT)
    assert not loop_cache_root.exists() or not any(loop_cache_root.rglob("*.loop.webp"))


async def test_observed_grid_supported_selection_bootstraps_without_loop_artifacts(
    observed_client: httpx.AsyncClient,
) -> None:
    capabilities_response = await observed_client.get("/api/v4/capabilities")
    assert capabilities_response.status_code == 200
    capabilities_payload = capabilities_response.json()
    reflectivity = capabilities_payload["model_catalog"]["mrms"]["variables"]["reflectivity"]
    assert reflectivity["render_substrates"] == ["legacy", "grid_webgl_v1"]
    assert capabilities_payload["model_catalog"]["mrms"]["defaults"]["default_render_substrate"] == "grid_webgl_v1"

    bootstrap_response = await observed_client.get(
        "/api/v4/bootstrap",
        params={"model": "mrms", "run": "latest", "var": "reflectivity", "region": "conus"},
    )
    assert bootstrap_response.status_code == 200
    bootstrap_payload = bootstrap_response.json()
    assert bootstrap_payload["selection"] == {
        "model": "mrms",
        "run": "20260330_1206z",
        "variable": "reflectivity",
        "region": "conus",
    }
    assert [row["fh"] for row in bootstrap_payload["frames"]] == [0]

    frames_response = await observed_client.get("/api/v4/mrms/latest/reflectivity/frames")
    assert frames_response.status_code == 200
    frames_payload = frames_response.json()
    assert [row["fh"] for row in frames_payload] == [0]
    assert frames_payload[0]["meta"]["meta"]["valid_time"] == "2026-03-30T12:06:00Z"

    grid_manifest_response = await observed_client.get("/api/v4/mrms/20260330_1206z/reflectivity/grid-manifest")
    assert grid_manifest_response.status_code == 200
    grid_manifest = grid_manifest_response.json()
    assert grid_manifest["subtype"] == "grid_webgl_v1"
    assert [frame["fh"] for frame in grid_manifest["lods"][0]["frames"]] == [0]

    loop_cache_root = Path(main_module.LOOP_CACHE_ROOT)
    assert not loop_cache_root.exists() or not any(loop_cache_root.rglob("*.loop.webp"))
