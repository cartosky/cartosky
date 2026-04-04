import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import numpy as np
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
os.environ.setdefault("TWM_ADMIN_MEMBER_IDS", "42")

from app import main as main_module
from app.services.grid import build_grid_for_run

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


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"

    run_id = "20260224_14z"
    incomplete_run_id = "20260224_15z"
    model = "hrrr"
    nam_model = "nam"
    nam_run_id = "20260224_12z"
    var = "radar_ptype"
    temp_var = "tmp2m"
    gfs_model = "gfs"
    gfs_run_id = "20260224_12z"
    gfs_invalid_run_id = "20260224_20z"
    gfs_var = "tmp2m"

    model_manifest_dir = manifests_root / model
    model_manifest_dir.mkdir(parents=True, exist_ok=True)
    (model_manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    var: {
                        "expected_frames": 2,
                        "available_frames": 2,
                        "frames": [
                            {"fh": 0},
                            {"fh": 1},
                        ]
                    },
                    temp_var: {
                        "expected_frames": 2,
                        "available_frames": 2,
                        "frames": [
                            {"fh": 0},
                            {"fh": 1},
                        ]
                    }
                }
            }
        )
    )
    (model_manifest_dir / f"{incomplete_run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    var: {
                        "expected_frames": 2,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0},
                        ],
                    }
                }
            }
        )
    )
    nam_manifest_dir = manifests_root / nam_model
    nam_manifest_dir.mkdir(parents=True, exist_ok=True)
    (nam_manifest_dir / f"{nam_run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    var: {
                        "expected_frames": 2,
                        "available_frames": 2,
                        "frames": [
                            {"fh": 0},
                            {"fh": 1},
                        ]
                    }
                }
            }
        )
    )
    gfs_manifest_dir = manifests_root / gfs_model
    gfs_manifest_dir.mkdir(parents=True, exist_ok=True)
    (gfs_manifest_dir / f"{gfs_run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    gfs_var: {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-02-24T12:00:00Z"}],
                    }
                }
            }
        )
    )
    (gfs_manifest_dir / f"{gfs_invalid_run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    gfs_var: {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0},
                        ],
                    }
                }
            }
        )
    )
    gfs_newer_run_id = "20260224_18z"
    (gfs_manifest_dir / f"{gfs_newer_run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    gfs_var: {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-02-24T18:00:00Z"},
                        ],
                    }
                }
            }
        )
    )

    model_published_dir = published_root / model
    (model_published_dir / run_id).mkdir(parents=True, exist_ok=True)
    (model_published_dir / incomplete_run_id).mkdir(parents=True, exist_ok=True)
    (model_published_dir / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    ready_tmp2m_dir = model_published_dir / run_id / temp_var
    ready_radar_dir = model_published_dir / run_id / var
    ready_values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    radar_values = np.array([[0.0, 1.0], [2.0, np.nan]], dtype=np.float32)
    _write_value_cog(ready_tmp2m_dir / "fh000.val.cog.tif", ready_values)
    _write_value_cog(ready_tmp2m_dir / "fh001.val.cog.tif", ready_values)
    (ready_tmp2m_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-02-24T14:00:00Z"})
    )
    (ready_tmp2m_dir / "fh001.json").write_text(
        json.dumps({"fh": 1, "units": "F", "valid_time": "2026-02-24T15:00:00Z"})
    )
    _write_value_cog(ready_radar_dir / "fh000.val.cog.tif", radar_values)
    _write_value_cog(ready_radar_dir / "fh001.val.cog.tif", radar_values)
    (ready_radar_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "dBZ", "valid_time": "2026-02-24T14:00:00Z"})
    )
    (ready_radar_dir / "fh001.json").write_text(
        json.dumps({"fh": 1, "units": "dBZ", "valid_time": "2026-02-24T15:00:00Z"})
    )
    build_grid_for_run(data_root=data_root, model=model, run=run_id, workers=1, variables=(var, temp_var))
    nam_published_dir = published_root / nam_model
    (nam_published_dir / nam_run_id).mkdir(parents=True, exist_ok=True)
    (nam_published_dir / "LATEST.json").write_text(json.dumps({"run_id": nam_run_id}))
    gfs_published_dir = published_root / gfs_model
    (gfs_published_dir / gfs_run_id).mkdir(parents=True, exist_ok=True)
    (gfs_published_dir / gfs_invalid_run_id).mkdir(parents=True, exist_ok=True)
    gfs_newer_var_dir = gfs_published_dir / gfs_newer_run_id / gfs_var
    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    _write_value_cog(gfs_newer_var_dir / "fh000.val.cog.tif", values)
    (gfs_newer_var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-02-24T18:00:00Z"})
    )
    # Intentionally point at an invalid GFS cycle hour to ensure API-side filtering
    # still resolves latest to a valid 6-hour cycle.
    (gfs_published_dir / "LATEST.json").write_text(json.dumps({"run_id": gfs_invalid_run_id}))

    gfs_ready_var_dir = gfs_published_dir / gfs_run_id / gfs_var
    _write_value_cog(gfs_ready_var_dir / "fh000.val.cog.tif", values)
    (gfs_ready_var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-02-24T12:00:00Z"})
    )
    build_grid_for_run(data_root=data_root, model=gfs_model, run=gfs_run_id, workers=1, variables=(gfs_var,))

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)

    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def test_frames_latest_cache_control_is_short(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/latest/radar_ptype/frames")

    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=60" in cache_control
    assert "immutable" not in cache_control
    assert response.headers.get("etag")


async def test_frames_historical_cache_control_is_immutable(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/20260224_14z/radar_ptype/frames")

    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=31536000" in cache_control
    assert "immutable" in cache_control
    assert response.headers.get("etag")


async def test_frames_incomplete_historical_cache_control_is_short(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/20260224_15z/radar_ptype/frames")

    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=60" in cache_control
    assert "immutable" not in cache_control
    assert response.headers.get("etag")


async def test_frame_payload_omits_legacy_loop_urls_for_radar_ptype(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/latest/radar_ptype/frames")

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list) and rows
    first = rows[0]
    assert "loop_webp_url" not in first
    assert "loop_webp_tier0_url" not in first


async def test_frame_payload_omits_legacy_loop_urls_for_nam_radar_ptype(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v4/nam/20260224_12z/radar_ptype/frames")

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list) and rows
    first = rows[0]
    assert "loop_webp_url" not in first
    assert "loop_webp_tier0_url" not in first


async def test_frame_payload_omits_legacy_loop_urls_for_tmp2m(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/latest/tmp2m/frames")

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list) and rows
    first = rows[0]
    assert "loop_webp_url" not in first
    assert "loop_webp_tier0_url" not in first


async def test_frame_payload_omits_legacy_loop_urls_for_incomplete_run(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v4/hrrr/20260224_15z/radar_ptype/frames")

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list) and rows
    first = rows[0]
    assert first["fh"] == 0
    assert "loop_webp_url" not in first
    assert "loop_webp_tier0_url" not in first


async def test_loop_manifest_endpoint_is_retired(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v4/hrrr/20260224_15z/radar_ptype/loop-manifest")
    assert response.status_code == 404


async def test_loop_webp_endpoint_is_retired(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/20260224_14z/radar_ptype/0/loop.webp?tier=0")
    assert response.status_code == 404


async def test_legacy_runtime_routes_are_retired(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/legacy/hrrr/latest/radar_ptype/frames")
    assert response.status_code == 404


async def test_v4_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True


async def test_capabilities_invariant_supported_models_matches_catalog(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/capabilities")

    assert response.status_code == 200
    assert "capabilities_total;dur=" in response.headers.get("server-timing", "")
    exposed_headers = response.headers.get("access-control-expose-headers", "")
    assert "CF-Cache-Status" in exposed_headers
    payload = response.json()
    supported_models = payload["supported_models"]
    model_catalog = payload["model_catalog"]
    availability = payload["availability"]

    assert sorted(supported_models) == sorted(model_catalog.keys())
    assert sorted(supported_models) == sorted(availability.keys())
    assert payload["contract_version"] == "v1"

    for model_id, model_payload in model_catalog.items():
        variables = model_payload.get("variables", {})
        assert isinstance(variables, dict)
        for var_key, var_payload in variables.items():
            assert var_payload["var_key"] == var_key
            assert "buildable" in var_payload


async def test_capabilities_availability_readiness_fields(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/capabilities")

    assert response.status_code == 200
    payload = response.json()
    availability = payload["availability"]

    hrrr = availability["hrrr"]
    assert hrrr["latest_run"] == "20260224_14z"
    assert hrrr["latest_run_ready"] is True
    assert hrrr["latest_run_ready_vars"] == ["radar_ptype", "tmp2m"]
    assert hrrr["latest_run_ready_frame_count"] == 4

    gfs = availability["gfs"]
    assert gfs["latest_run"] == "20260224_12z"
    assert gfs["latest_run_ready"] is True
    assert gfs["latest_run_ready_vars"] == ["tmp2m"]
    assert gfs["latest_run_ready_frame_count"] == 1


async def test_latest_run_skips_newer_grid_unsupported_published_run(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/gfs/latest/tmp2m/grid-manifest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"] == "20260224_12z"


async def test_runs_and_manifest_reject_out_of_cycle_gfs_run(client: httpx.AsyncClient) -> None:
    runs_resp = await client.get("/api/v4/gfs/runs")
    assert runs_resp.status_code == 200
    assert runs_resp.json() == ["20260224_18z", "20260224_12z"]

    invalid_manifest_resp = await client.get("/api/v4/gfs/20260224_20z/manifest")
    assert invalid_manifest_resp.status_code == 404


async def test_bootstrap_endpoint_includes_selection_and_frames(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/bootstrap?model=hrrr&run=latest&var=radar_ptype&region=conus")

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "v1"
    assert "capabilities" in payload
    assert "regions" in payload
    selection = payload["selection"]
    assert selection["model"] == "hrrr"
    assert selection["run"] == "20260224_14z"
    assert selection["variable"] == "radar_ptype"
    assert selection["region"] == "conus"
    assert isinstance(payload.get("frames"), list)
    assert payload["frames"]
    server_timing = response.headers.get("server-timing", "")
    assert "bootstrap_total;dur=" in server_timing


async def test_region_presets_include_server_timing(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/regions")

    assert response.status_code == 200
    assert "regions_total;dur=" in response.headers.get("server-timing", "")


async def test_manifest_and_frames_include_server_timing(client: httpx.AsyncClient) -> None:
    manifest_response = await client.get("/api/v4/hrrr/latest/manifest")
    assert manifest_response.status_code == 200
    assert "manifest_total;dur=" in manifest_response.headers.get("server-timing", "")

    frames_response = await client.get("/api/v4/hrrr/latest/radar_ptype/frames")
    assert frames_response.status_code == 200
    assert "frames_total;dur=" in frames_response.headers.get("server-timing", "")
