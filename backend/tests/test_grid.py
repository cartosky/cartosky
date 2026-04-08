from __future__ import annotations

import gzip
import brotli
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
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_grid_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app.services.grid import (
    build_grid_for_run,
    build_grid_manifests_for_run_root,
    grid_dir,
    grid_manifest_path_for_run_root,
    resolved_grid_dir_for_run_root,
    write_grid_frame_for_run_root,
)


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


def _grid_artifact_dir(data_root: Path, model: str, run_id: str, var: str) -> Path:
    return grid_dir(data_root, model, run_id, var)


def test_write_grid_frame_for_run_root_writes_manifest_without_value_cog(tmp_path: Path) -> None:
    run_root = tmp_path / "staging" / "hrrr" / "20260330_12z"
    var_dir = run_root / "tmp2m"
    var_dir.mkdir(parents=True, exist_ok=True)
    values = np.array([[32.0, 40.5], [np.nan, -12.3]], dtype=np.float32)

    write_grid_frame_for_run_root(
        run_root=run_root,
        model="hrrr",
        var="tmp2m",
        fh=0,
        values=values,
        transform=from_origin(-14920000.0, 7362000.0, 3000.0, 3000.0),
    )
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-03-30T12:00:00Z"})
    )

    manifest_ok = build_grid_manifests_for_run_root(
        run_root=run_root,
        model="hrrr",
        run="20260330_12z",
        variables=("tmp2m",),
    )

    assert manifest_ok == 1
    manifest = json.loads(grid_manifest_path_for_run_root(run_root, "tmp2m").read_text())
    assert manifest["subtype"] == "grid"
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"
    assert manifest["lods"][0]["frames"][0]["valid_time"] == "2026-03-30T12:00:00Z"
    frame_path = run_root / "tmp2m" / "grid" / "fh000.l0.u16.bin"
    sidecar_path = frame_path.with_name(f"{frame_path.name}.gz")
    brotli_sidecar_path = frame_path.with_name(f"{frame_path.name}.br")
    assert sidecar_path.is_file()
    assert brotli_sidecar_path.is_file()
    assert gzip.decompress(sidecar_path.read_bytes()) == frame_path.read_bytes()
    assert brotli.decompress(brotli_sidecar_path.read_bytes()) == frame_path.read_bytes()


def test_build_grid_for_run_writes_manifest_and_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    sidecar_path = artifacts_dir / "fh000.l0.u16.bin.gz"
    brotli_sidecar_path = artifacts_dir / "fh000.l0.u16.bin.br"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert sidecar_path.is_file()
    assert brotli_sidecar_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert gzip.decompress(sidecar_path.read_bytes()) == frame_path.read_bytes()
    assert brotli.decompress(brotli_sidecar_path.read_bytes()) == frame_path.read_bytes()
    assert encoded[0, 0] == 1320
    assert encoded[0, 1] == 1405
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 877

    manifest = json.loads(manifest_path.read_text())
    assert manifest["subtype"] == "grid"
    assert manifest["grid"]["dtype"] == "uint16"
    assert manifest["grid"]["scale"] == 0.01
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


def test_grid_dir_resolves_legacy_grid_v1_for_published_runs(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    legacy_dir = data_root / "published" / "hrrr" / "20260330_12z" / "tmp2m" / "grid_v1"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "manifest.json").write_text("{}")

    resolved = grid_dir(data_root, "hrrr", "20260330_12z", "tmp2m")

    assert resolved == legacy_dir


def test_resolved_grid_dir_prefers_new_grid_over_legacy(tmp_path: Path) -> None:
    run_root = tmp_path / "published" / "hrrr" / "20260330_12z"
    legacy_dir = run_root / "tmp2m" / "grid_v1"
    new_dir = run_root / "tmp2m" / "grid"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolved_grid_dir_for_run_root(run_root, "tmp2m")

    assert resolved == new_dir


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
def test_build_grid_for_run_supports_temperature_family_targets(
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 1320
    assert encoded[0, 1] == 1405
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 877

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == var
    assert manifest["grid"]["scale"] == 0.01
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
def test_build_grid_for_run_supports_wind_family_targets(
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 486

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == var
    assert manifest["grid"]["scale"] == 0.01
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "mph"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


@pytest.mark.parametrize(
    ("var", "expected_color_map_id"),
    [
        ("snowfall_total", "snowfall_total"),
        ("snowfall_kuchera_total", "snowfall_total"),
    ],
)
def test_build_grid_for_run_supports_gfs_snowfall_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    expected_color_map_id: str,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    frame_meta_path = artifacts_dir / "fh000.l0.meta.json"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert frame_meta_path.is_file()
    assert manifest_path.is_file()

    frame_meta = json.loads(frame_meta_path.read_text())
    assert frame_meta["width"] == values.shape[1] * 3
    assert frame_meta["height"] == values.shape[0] * 3
    assert frame_meta["display_prep"]["id"] == "gfs_snowfall_total_display_v1"

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == expected_color_map_id
    assert manifest["grid"]["scale"] == 0.01
    assert manifest["palette"]["kind"] == "discrete"
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


def test_build_grid_for_run_supports_gfs_precip_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var = "precip_total"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    frame_meta_path = artifacts_dir / "fh000.l0.meta.json"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert frame_meta_path.is_file()
    assert manifest_path.is_file()

    frame_meta = json.loads(frame_meta_path.read_text())
    assert frame_meta["width"] == values.shape[1] * 3
    assert frame_meta["height"] == values.shape[0] * 3
    assert frame_meta["display_prep"]["id"] == "gfs_precip_total_display_v2"

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "precip_total"
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "in"
    assert manifest["grid"]["width"] == values.shape[1] * 3
    assert manifest["grid"]["height"] == values.shape[0] * 3
    assert manifest["display_prep"]["id"] == "gfs_precip_total_display_v2"

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


def test_build_grid_for_run_supports_gfs_mlcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var = "mlcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1250.0], [np.nan, 3200.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1250
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 3200

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


@pytest.mark.parametrize("model", ["gfs", "hrrr", "nam"])
def test_build_grid_for_run_supports_pwat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    data_root = tmp_path / "data"
    run_id = "20260330_12z"
    var = "pwat"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1.23], [np.nan, 2.86]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 286

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "pwat"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 0.01
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "in"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]


def test_build_grid_for_run_supports_gfs_sbcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var = "sbcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1750.0], [np.nan, 4100.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1750
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 4100

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_gfs_mucape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var = "mucape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1500.0], [np.nan, 3600.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1500
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 3600

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u16.bin"


def test_build_grid_for_run_supports_gfs_precip_ptype(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260330_12z"
    var = "precip_ptype"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 2.0], [np.nan, 9.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in/hr", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 2
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 9

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "precip_ptype"
    assert manifest["palette"]["kind"] == "indexed"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "index"


@pytest.mark.parametrize(
    ("var", "expected_color_map_id"),
    [
        ("precip_total", "precip_total"),
        ("snowfall_total", "snowfall_total"),
        ("snowfall_kuchera_total", "snowfall_total"),
    ],
)
def test_build_grid_for_run_supports_hrrr_accumulation_targets(
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 486

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == expected_color_map_id
    if expected_color_map_id == "snowfall_total":
        assert manifest["palette"]["kind"] == "discrete"
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
        ("snowfall_kuchera_total", "snowfall_total"),
    ],
)
def test_build_grid_for_run_supports_nam_accumulation_targets(
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 123
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 486

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == expected_color_map_id
    if expected_color_map_id == "snowfall_total":
        assert manifest["palette"]["kind"] == "discrete"
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
def test_build_grid_for_run_supports_nbm_accumulation_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    expected_color_map_id: str,
) -> None:
    data_root = tmp_path / "data"
    model = "nbm"
    run_id = "20260330_12z"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 12.3], [np.nan, 48.6]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "in", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    frame_meta_path = artifacts_dir / "fh000.l0.meta.json"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == expected_color_map_id
    if expected_color_map_id == "snowfall_total":
        assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 0.1
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "in"

    expected_prep_id = "nbm_precip_total_display_v2" if var == "precip_total" else "nbm_snowfall_total_display_v1"
    assert frame_meta_path.is_file()
    frame_meta = json.loads(frame_meta_path.read_text())
    assert frame_meta["width"] == values.shape[1] * 3
    assert frame_meta["height"] == values.shape[0] * 3
    assert frame_meta["display_prep"]["id"] == expected_prep_id
    assert manifest["grid"]["width"] == values.shape[1] * 3
    assert manifest["grid"]["height"] == values.shape[0] * 3
    assert manifest["display_prep"]["id"] == expected_prep_id
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


def test_build_grid_for_run_supports_nbm_sbcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "nbm"
    run_id = "20260330_12z"
    var = "sbcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1750.0], [np.nan, 4100.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1750
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 4100

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_hrrr_radar_ptype(
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
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


def test_build_grid_for_run_supports_hrrr_mlcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "mlcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1250.0], [np.nan, 3200.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1250
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 3200

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_hrrr_sbcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "sbcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1750.0], [np.nan, 4100.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1750
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 4100

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_hrrr_mucape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260330_12z"
    var = "mucape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1500.0], [np.nan, 3600.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1500
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 3600

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_mrms_reflectivity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "mrms"
    run_id = "20260330_1205z"
    var = "reflectivity"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[10.0, 20.0], [np.nan, 60.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "dBZ", "valid_time": "2026-03-30T12:05:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u8.bin"
    lod1_frame_path = artifacts_dir / "fh000.l1.u8.bin"
    lod2_frame_path = artifacts_dir / "fh000.l2.u8.bin"
    frame_meta_path = artifacts_dir / "fh000.l0.meta.json"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert lod1_frame_path.is_file()
    assert lod2_frame_path.is_file()
    assert frame_meta_path.is_file()
    assert manifest_path.is_file()

    frame_meta = json.loads(frame_meta_path.read_text())
    assert frame_meta["width"] == values.shape[1]
    assert frame_meta["height"] == values.shape[0]
    assert frame_meta["display_prep"]["id"] == "mrms_reflectivity_display_v1"

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mrms_reflectivity"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["palette"]["transparent_below_min"] == 10.0
    assert manifest["grid"]["dtype"] == "uint8"
    assert manifest["grid"]["scale"] == 0.5
    assert manifest["grid"]["offset"] == -10.0
    assert manifest["grid"]["units"] == "dBZ"
    assert manifest["grid"]["width"] == values.shape[1]
    assert manifest["grid"]["height"] == values.shape[0]
    assert manifest["display_prep"]["id"] == "mrms_reflectivity_display_v1"
    assert [lod["level"] for lod in manifest["lods"]] == [0, 1, 2]
    assert manifest["lods"][1]["frames"][0]["file"] == "fh000.l1.u8.bin"
    assert manifest["lods"][2]["frames"][0]["file"] == "fh000.l2.u8.bin"

    encoded = np.frombuffer(frame_path.read_bytes(), dtype=np.uint8).reshape(values.shape)
    assert encoded.dtype == np.dtype(np.uint8)
    assert encoded[0, 0] >= 40
    assert encoded[0, 1] >= 60
    assert encoded[1, 0] == 255
    assert encoded[1, 1] > encoded[0, 1]
    assert encoded[1, 1] >= 100
    assert manifest["lods"][0]["frames"][0]["file"] == "fh000.l0.u8.bin"


def test_build_grid_for_run_supports_nam_radar_ptype(
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

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
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


def test_build_grid_for_run_supports_nam_mlcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "nam"
    run_id = "20260330_12z"
    var = "mlcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1250.0], [np.nan, 3200.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1250
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 3200

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_nam_sbcape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "nam"
    run_id = "20260330_12z"
    var = "sbcape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1750.0], [np.nan, 4100.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1750
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 4100

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


def test_build_grid_for_run_supports_nam_mucape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    model = "nam"
    run_id = "20260330_12z"
    var = "mucape"
    var_dir = data_root / "published" / model / run_id / var
    values = np.array([[0.0, 1500.0], [np.nan, 3600.0]], dtype=np.float32)
    _write_value_cog(var_dir / "fh000.val.cog.tif", values)
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "valid_time": "2026-03-30T12:00:00Z"})
    )

    ok, fail, manifest_ok = build_grid_for_run(
        data_root=data_root,
        model=model,
        run=run_id,
        workers=1,
        variables=(var,),
    )

    assert ok == 1
    assert fail == 0
    assert manifest_ok == 1

    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    frame_path = artifacts_dir / "fh000.l0.u16.bin"
    manifest_path = artifacts_dir / "manifest.json"
    assert frame_path.is_file()
    assert manifest_path.is_file()

    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(values.shape)
    assert encoded[0, 0] == 0
    assert encoded[0, 1] == 1500
    assert encoded[1, 0] == 65535
    assert encoded[1, 1] == 3600

    manifest = json.loads(manifest_path.read_text())
    assert manifest["palette"]["color_map_id"] == "mlcape"
    assert manifest["palette"]["kind"] == "discrete"
    assert manifest["grid"]["scale"] == 1.0
    assert manifest["grid"]["offset"] == 0.0
    assert manifest["grid"]["units"] == "J/kg"


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
        json.dumps(
            {
                "fh": 0,
                "units": "F",
                "valid_time": "2026-03-30T12:00:00Z",
                "contours": {
                    "freezing": {
                        "format": "geojson",
                        "path": "contours/fh000_freezing.geojson",
                    }
                },
            }
        )
    )
    (var_dir / "contours").mkdir(parents=True, exist_ok=True)
    (var_dir / "contours" / "fh000_freezing.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "level": 32,
                            "label": "Freezing line",
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-99.0, 35.0], [-97.0, 36.0]],
                        },
                    }
                ],
            }
        )
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

    build_grid_for_run(
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
    assert payload["subtype"] == "grid"
    frame = payload["lods"][0]["frames"][0]
    assert frame["fh"] == 0
    assert frame["url"].startswith("/api/v4/grid/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin?v=20260330_12z-tmp2m-")


async def test_grid_frame_endpoint_serves_binary_payload(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/grid/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "grid_file_total;dur=" in response.headers.get("server-timing", "")
    exposed_headers = response.headers.get("access-control-expose-headers", "")
    assert "Content-Length" in exposed_headers
    assert "Content-Encoding" in exposed_headers
    assert "ETag" in exposed_headers
    encoded = np.frombuffer(response.content, dtype="<u2")
    assert encoded.size == 4


async def test_grid_frame_endpoint_can_use_nginx_accel_redirect(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "GRID_ACCEL_REDIRECT_ENABLED", True)
    monkeypatch.setattr(main_module, "GRID_ACCEL_REDIRECT_PREFIX", "/_cartosky_grid_internal/")

    response = await client.get("/api/v4/grid/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "grid_file_total;dur=" in response.headers.get("server-timing", "")
    exposed_headers = response.headers.get("access-control-expose-headers", "")
    assert "Content-Length" in exposed_headers
    assert "Content-Encoding" in exposed_headers
    assert (
        response.headers["x-accel-redirect"]
        == "/_cartosky_grid_internal/hrrr/20260330_12z/tmp2m/grid/fh000.l0.u16.bin"
    )
    assert response.content == b""


async def test_contour_endpoint_serves_geojson_bytes(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/hrrr/20260330_12z/tmp2m/0/contours/freezing")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    assert "contour_total;dur=" in response.headers.get("server-timing", "")
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert payload["features"][0]["properties"]["label"] == "Freezing line"


async def test_grid_frame_endpoint_serves_legacy_grid_v1_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    legacy_grid_dir = published_root / model / run_id / var / "grid_v1"
    legacy_grid_dir.mkdir(parents=True, exist_ok=True)
    encoded = np.array([[1320, 1405], [65535, 877]], dtype="<u2")
    (legacy_grid_dir / "fh000.l0.u16.bin").write_bytes(encoded.tobytes(order="C"))
    (legacy_grid_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "subtype": "grid",
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
                "palette": {"color_map_id": "tmp2m"},
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

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        response = await test_client.get("/api/v4/grid/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin")

    assert response.status_code == 200
    assert np.frombuffer(response.content, dtype="<u2").size == 4


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
    artifacts_dir = _grid_artifact_dir(data_root, model, run_id, var)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "fh000.l0.u16.bin").write_bytes(b'{"bad":"frame"}')
    (artifacts_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "subtype": "grid",
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

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        response = await test_client.get("/api/v4/grid/hrrr/20260330_12z/tmp2m/fh000.l0.u16.bin")
        assert response.status_code == 404
