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
    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    loop_cache_root = tmp_path / "loop-cache"

    model = "mrms"
    run_id = "20260327_1206z"
    variable = "reflectivity"

    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "model": model,
                "run": run_id,
                "variables": {
                    variable: {
                        "expected_frames": 2,
                        "available_frames": 2,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-03-27T12:00:00Z"},
                            {"fh": 1, "valid_time": "2026-03-27T12:02:00Z"},
                        ],
                    }
                }
            }
        )
    )

    model_root = published_root / model
    model_root.mkdir(parents=True, exist_ok=True)
    (model_root / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    var_dir = model_root / run_id / variable
    var_dir.mkdir(parents=True, exist_ok=True)
    for fh, valid_time in ((0, "2026-03-27T12:00:00Z"), (1, "2026-03-27T12:02:00Z")):
        _write_value_raster(var_dir / f"fh{fh:03d}.val.cog.tif")
        (var_dir / f"fh{fh:03d}.rgba.cog.tif").write_bytes(b"not-a-real-cog")
        (var_dir / f"fh{fh:03d}.json").write_text(
            json.dumps({"units": "dBZ", "valid_time": valid_time, "kind": "discrete"})
        )
        tier0_path = loop_cache_root / model / run_id / variable / "tier0" / f"fh{fh:03d}.loop.webp"
        tier0_path.parent.mkdir(parents=True, exist_ok=True)
        tier0_path.write_bytes(b"RIFFxxxxWEBPVP8 ")

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    monkeypatch.setattr(main_module, "LOOP_CACHE_ROOT", loop_cache_root)

    _reset_main_caches()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


async def test_mrms_latest_manifest_and_frames_resolve(client: httpx.AsyncClient) -> None:
    manifest_response = await client.get("/api/v4/mrms/latest/manifest")
    assert manifest_response.status_code == 200
    assert manifest_response.json()["run"] == "20260327_1206z"

    frames_response = await client.get("/api/v4/mrms/latest/reflectivity/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0, 1]
    assert frames[0]["run"] == "20260327_1206z"
    assert frames[0]["meta"]["meta"]["valid_time"] == "2026-03-27T12:00:00Z"
    assert frames[1]["meta"]["meta"]["valid_time"] == "2026-03-27T12:02:00Z"
    assert "/api/v4/mrms/20260327_1206z/reflectivity/0/loop.webp" in frames[0]["loop_webp_url"]


async def test_mrms_loop_manifest_and_sampling_use_minute_run_ids(client: httpx.AsyncClient) -> None:
    loop_response = await client.get("/api/v4/mrms/latest/reflectivity/loop-manifest")
    assert loop_response.status_code == 200
    payload = loop_response.json()
    tier0 = next(entry for entry in payload["loop_tiers"] if entry["tier"] == 0)
    assert [frame["fh"] for frame in tier0["frames"]] == [0, 1]
    assert tier0["frames"][0]["url"].startswith("/api/v4/mrms/20260327_1206z/reflectivity/0/loop.webp")

    sample_response = await client.get(
        "/api/v4/sample",
        params={
            "model": "mrms",
            "run": "latest",
            "var": "reflectivity",
            "fh": 0,
            "lat": 45.5,
            "lon": -100.5,
        },
    )
    assert sample_response.status_code == 200
    assert sample_response.json()["run"] == "20260327_1206z"
    assert sample_response.json()["valid_time"] == "2026-03-27T12:00:00Z"
    assert sample_response.json()["value"] == 10.0
