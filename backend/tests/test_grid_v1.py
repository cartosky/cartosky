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
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_grid_v1_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app import config as config_module
from app.services.grid_v1 import build_grid_v1_for_run


def _write_value_cog(path: Path, values: np.ndarray) -> None:
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
        transform=from_origin(-14920000.0, 7362000.0, 3000.0, 3000.0),
        nodata=np.nan,
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def test_build_grid_v1_for_run_writes_manifest_and_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "tmp2m"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 1320
    assert encoded[0, 1] == 1405
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 877

    manifest = json.loads(manifest_path.read_text())
    assert manifest["subtype"] == "grid_webgl_v1"
    assert manifest["grid"]["dtype"] == "uint16"
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


@pytest.mark.parametrize(
    ("model", "var"),
    [
        ("hrrr", "dp2m"),
        ("hrrr", "tmp850"),
        ("gfs", "tmp2m"),
        ("gfs", "dp2m"),
        ("gfs", "tmp850"),
        ("nam", "tmp2m"),
        ("nam", "dp2m"),
        ("nam", "tmp850"),
        ("nbm", "tmp2m"),
    ],
)
def test_build_grid_v1_for_run_supports_temperature_family_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    var: str,
) -> None:
    data_root = tmp_path / "data"
    run_id = "20260330_12z"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 1320
    assert encoded[0, 1] == 1405
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 877

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == var
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == -100.0
    assert manifest["grid"]["units"] == "F"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


@pytest.mark.parametrize(
    ("model", "var"),
    [
        ("hrrr", "wspd10m"),
        ("hrrr", "wgst10m"),
        ("gfs", "wspd10m"),
        ("gfs", "wgst10m"),
        ("nam", "wspd10m"),
        ("nam", "wgst10m"),
        ("nbm", "wspd10m"),
    ],
)
def test_build_grid_v1_for_run_supports_wind_family_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    var: str,
) -> None:
    data_root = tmp_path / "data"
    run_id = "20260330_12z"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "mph", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 486

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == var
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "mph"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


def test_build_grid_v1_for_run_supports_gfs_snowfall_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var = "snowfall_total"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    frame_meta_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.meta.json"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert frame_meta_path.is_file()
    assert manifest_path.is_file()

    frame_meta = json.loads(frame_meta_path.read_text())
    assert frame_meta["width"] == values.shape[1] * 3
    assert frame_meta["height"] == values.shape[0] * 3
    assert frame_meta["display_prep"]["id"] == "gfs_snowfall_total_display_v1"

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "snowfall_total"
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "in"
    assert manifest["grid"]["width"] == values.shape[1] * 3
    assert manifest["grid"]["height"] == values.shape[0] * 3
    assert manifest["display_prep"]["id"] == "gfs_snowfall_total_display_v1"

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(
        manifest["grid"]["height"],
        manifest["grid"]["width"],
    )
    assert encoded.shape == (values.shape[0] * 3, values.shape[1] * 3)
    assert encoded.dtype == np.dtype("<u2")
    assert np.count_nonzero(encoded == 65535) > 0
    assert int(encoded.max()) >= 486
    assert int(encoded.min()) == 0

    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


@pytest.mark.parametrize(
    ("var", "expected_color_map_id"),
    [
        ("precip_total", "precip_total"),
        ("snowfall_total", "snowfall_total"),
        ("snowfall_kuchera_total", "snowfall_total"),
    ],
)
def test_build_grid_v1_for_run_supports_hrrr_accumulation_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    expected_color_map_id: str,
) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260330_12z"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 486

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == expected_color_map_id
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "in"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


@pytest.mark.parametrize(
    ("var", "expected_color_map_id"),
    [
        ("precip_total", "precip_total"),
        ("snowfall_total", "snowfall_total"),
    ],
)
def test_build_grid_v1_for_run_supports_nam_accumulation_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    expected_color_map_id: str,
) -> None:
    data_root = tmp_path / "data"
    model = "nam"
    run_id = "20260330_12z"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 486

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == expected_color_map_id
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "in"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


def test_build_grid_v1_for_run_supports_hrrr_radar_ptype(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "radar_ptype"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1.0], [np.nan, 15.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "dBZ", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 15

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "radar_ptype"
    assert manifest["palette"]["kind"] == "indexed"
    assert manifest["palette"]["transparent_zero"] is True
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "dBZ"


def test_build_grid_v1_for_run_supports_nam_radar_ptype(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "nam"
    run_id = "20260330_12z"
    var = "radar_ptype"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 2.0], [np.nan, 9.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "dBZ", "valid_time": "2026-03-30T12:00:00Z"})
    )

    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    ok, fail, manifest_ok = build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    frame_path = data_root / "published" / model / run_id / var / "grid_v1" / "fh000.l0.u16.bin"
    manifest_path = data_root / "published" / model / run_id / var / "grid_v1" / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 2
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 9

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "radar_ptype"
    assert manifest["palette"]["kind"] == "indexed"
    assert manifest["palette"]["transparent_zero"] is True
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "dBZ"


pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "tmp2m"
    var_dir = published_root / model / run_id / var
    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-03-30T12:00:00Z"})
    )
    (published_root / model / run_id).mkdir(parents=True, exist_ok=True)
    (published_root / model / "LATEST.json").parent.mkdir(parents=True, exist_ok=True)
    (published_root / model / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    var: {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-03-30T12:00:00Z"}],
                    }
                }
            }
        )
    )

    monkeypatch.setenv("CARTOSKY_GRID_V1_ENABLED", "1")
    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_enabled.cache_clear()
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    build_grid_v1_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def test_grid_manifest_endpoint_returns_urls_and_server_timing(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/20260330_12z/tmp2m/grid-manifest")

    assert response.status_code == 200
    assert "grid_manifest_total;dur=" in response.headers.get("server-timing", "")
    payload = response.json()
    assert payload["subtype"] == "grid_webgl_v1"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith("/api/v4/grid/v1/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin?v=20260330_12z-tmp2m-")


async def test_grid_frame_endpoint_serves_binary_payload(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/grid/v1/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    encoded = np.frombuffer(response.content, dtype="<u2")
    assert encoded.size == 4


async def test_grid_frame_endpoint_rejects_undersized_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "tmp2m"
    var_dir = published_root / model / run_id / var
    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-03-30T12:00:00Z"})
    )
    grid_dir = var_dir / "grid_v1"
    grid_dir.mkdir(parents=True, exist_ok=True)
    (grid_dir / "fh000.l0.u16.bin").write_bytes(b'{"bad":"frame"}')
    (grid_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "subtype": "grid_webgl_v1",
                "model": model,
                "run": run_id,
                "var": var,
                "projection": "EPSG:3857",
                "bbox": [-14920000.0, 7356000.0, -14914000.0, 7362000.0],
                "grid": {
                    "width": 2,
                    "height": 2,
                    "dtype": "uint16",
                    "endianness": "little",
                    "scale": 0.1,
                    "offset": -100.0,
                    "nodata": 65535,
                    "units": "F",
                },
                "palette": {"color_map_id": "temperature"},
                "lods": [{"level": 0, "width": 2, "height": 2, "frames": [{"fh": 0, "file": "fh000.l0.u16.bin"}]}],
            }
        )
    )
    write_manifest = manifests_root / model
    write_manifest.mkdir(parents=True, exist_ok=True)
    (write_manifest / f"{run_id}.json").write_text(
        json.dumps({"variables": {var: {"expected_frames": 1, "available_frames": 1, "frames": [{"fh": 0}]}}})
    )
    (published_root / model / "LATEST.json").parent.mkdir(parents=True, exist_ok=True)
    (published_root / model / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    monkeypatch.setenv("CARTOSKY_GRID_V1_ENABLED", "1")
    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_enabled.cache_clear()
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        response = await test_client.get("/api/v4/grid/v1/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin")
        assert response.status_code == 404


def test_grid_v1_allowlist_override_can_narrow_supported_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARTOSKY_GRID_V1_ALLOWLIST", "hrrr:tmp2m")
    monkeypatch.delenv("CARTOSKY_GRID_V1_DENYLIST", raising=False)
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    assert config_module.grid_v1_pair_enabled("hrrr", "tmp2m") is True
    assert config_module.grid_v1_pair_enabled("hrrr", "dp2m") is False


def test_grid_v1_denylist_can_disable_supported_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CARTOSKY_GRID_V1_ALLOWLIST", raising=False)
    monkeypatch.setenv("CARTOSKY_GRID_V1_DENYLIST", "gfs:snowfall_total")
    config_module.grid_v1_allowlist_override.cache_clear()
    config_module.grid_v1_denylist.cache_clear()

    assert config_module.grid_v1_pair_enabled("gfs", "tmp2m") is True
    assert config_module.grid_v1_pair_enabled("gfs", "snowfall_total") is False
