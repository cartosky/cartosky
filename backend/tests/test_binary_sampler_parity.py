"""Layer 2 binary-sampler tests for the value COG -> grid binary migration."""

from __future__ import annotations

import json
import os
import sys
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

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_binary_sampler_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app.services.grid import write_grid_frame_for_run_root
from app.services.grid_display_prep import prepare_grid_display_values
from app.services.sampling import (
    read_binary_sample_value,
    sample_binary_value,
    sample_binary_point_value,
    sample_point_value,
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
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=np.nan,
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def _write_pair(
    tmp_path: Path,
    *,
    model: str,
    var: str,
    values: np.ndarray,
) -> tuple[Path, Path, Path]:
    run_root = tmp_path / "published" / model / "20260630_00z"
    var_dir = run_root / var
    cog_path = var_dir / "fh000.val.cog.tif"
    _write_value_cog(cog_path, values)
    write_grid_frame_for_run_root(
        run_root=run_root,
        model=model,
        var=var,
        fh=0,
        values=values,
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        projection="EPSG:4326",
    )
    return (
        cog_path,
        var_dir / "grid" / "fh000.l0.u16.bin",
        var_dir / "grid" / "fh000.l0.meta.json",
    )


def _meta_index(meta_path: Path, *, lon: float, lat: float) -> tuple[int, int]:
    meta = json.loads(meta_path.read_text())
    transform = rasterio.Affine(*meta["transform"])
    col_f, row_f = ~transform * (lon, lat)
    return int(np.floor(row_f)), int(np.floor(col_f))


def test_binary_sampler_matches_cog_for_unscaled_variable_and_oob(tmp_path: Path) -> None:
    values = np.array(
        [
            [1.34, 2.21, 3.09],
            [4.04, np.nan, 6.52],
            [7.77, 8.88, 9.99],
        ],
        dtype=np.float32,
    )
    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="tmp2m",
        values=values,
    )

    assert sample_binary_point_value(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        lat=45.5,
        lon=-100.5,
    ) == sample_point_value(cog_path, lat=45.5, lon=-100.5)

    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        lat=44.5,
        lon=-99.5,
    )
    assert raw is None
    assert no_data is True

    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        lat=60.0,
        lon=-120.0,
    )
    assert raw is None
    assert no_data is True


def test_binary_sampler_reads_display_prepped_continuous_upscale(tmp_path: Path) -> None:
    values = np.array(
        [
            [0.00, 0.30, 0.60],
            [0.90, 1.20, 1.50],
            [1.80, 2.10, 2.40],
        ],
        dtype=np.float32,
    )
    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="precip_total",
        values=values,
    )

    lat = 45.75
    lon = -100.25
    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="precip_total",
        lat=lat,
        lon=lon,
    )

    display_values, prep_meta = prepare_grid_display_values(model="gfs", var="precip_total", values=values)
    row, col = _meta_index(meta_path, lon=lon, lat=lat)
    expected = float(display_values[row, col])
    assert prep_meta is not None
    assert prep_meta["upscale_factor"] == 3
    assert no_data is False
    assert raw == pytest.approx(expected, abs=0.005)

    # The COG still samples the original lower-resolution field during canary.
    # For continuous 3x display-prepped vars, exact equality is not required.
    cog_value = sample_point_value(cog_path, lat=lat, lon=lon)
    assert cog_value is not None
    assert raw is not None
    assert abs(round(raw, 1) - cog_value) <= 0.5


def test_binary_sampler_reads_display_prepped_categorical_upscale(tmp_path: Path) -> None:
    values = np.array(
        [
            [10.0, 20.0],
            [30.0, 40.0],
        ],
        dtype=np.float32,
    )
    _cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="ptype_intensity",
        values=values,
    )

    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="ptype_intensity",
        lat=44.25,
        lon=-99.25,
    )

    display_values, prep_meta = prepare_grid_display_values(model="gfs", var="ptype_intensity", values=values)
    row, col = _meta_index(meta_path, lon=-99.25, lat=44.25)
    assert prep_meta is not None
    assert prep_meta["upscale_factor"] == 3
    assert prep_meta["categorical_nearest"] is True
    assert no_data is False
    assert raw == float(display_values[row, col])
    assert raw == 40.0


def test_binary_sampler_rejects_unknown_meta_format_version(tmp_path: Path) -> None:
    values = np.array([[32.0]], dtype=np.float32)
    _cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="tmp2m",
        values=values,
    )
    meta = json.loads(meta_path.read_text())
    meta["format_version"] = 999
    meta_path.write_text(json.dumps(meta))

    with pytest.raises(ValueError, match="Unsupported grid frame format_version"):
        read_binary_sample_value(
            frame_path,
            meta_path,
            model="gfs",
            var="tmp2m",
            lat=45.5,
            lon=-100.5,
        )


def test_sample_binary_value_resolves_published_grid_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = "gfs"
    run = "20260630_00z"
    var = "tmp2m"
    values = np.array([[32.0, 40.5]], dtype=np.float32)
    _write_pair(tmp_path, model=model, var=var, values=values)

    manifests_root = tmp_path / "manifests"
    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run}.json").write_text(
        json.dumps({"variables": {var: {"expected_frames": 1, "available_frames": 1, "frames": [{"fh": 0}]}}})
    )
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    main_module._manifest_cache.clear()

    present, value = sample_binary_value(
        model,
        run,
        var,
        0,
        lat=45.5,
        lon=-100.5,
    )

    assert present is True
    assert value == 32.0


def test_sample_binary_value_decodes_alias_under_runtime_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test for the requested-vs-runtime variable id split: "t2m" is
    # a real GFS alias for "tmp2m". The frame is published (and packed) under
    # the runtime id, so the decode packing lookup must use that id too — with
    # the requested alias, the packing entry ("gfs", "t2m") does not exist and
    # the sample silently degrades to (True, None) instead of a value.
    model = "gfs"
    run = "20260630_00z"
    values = np.array([[32.0, 40.5]], dtype=np.float32)
    _write_pair(tmp_path, model=model, var="tmp2m", values=values)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", tmp_path / "manifests")
    main_module._manifest_cache.clear()

    canonical = sample_binary_value(model, run, "tmp2m", 0, lat=45.5, lon=-100.5)
    alias = sample_binary_value(model, run, "t2m", 0, lat=45.5, lon=-100.5)

    assert canonical == (True, 32.0)
    assert alias == canonical
