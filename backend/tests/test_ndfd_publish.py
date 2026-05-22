from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import ndfd_poller, ndfd_publish
from app.services.grid import grid_code_supported
from app.models.ndfd import NDFD_VARIABLE_CATALOG
from app.services.ndfd_source import NDFDSourceField
from app.services.run_ids import format_run_id


def _write_test_value_raster(path: Path, values: np.ndarray) -> None:
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
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def test_publish_ndfd_bundle_warps_native_grid_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(ndfd_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        ndfd_publish,
        "_build_sidecar_json",
        lambda **kwargs: {
            "model": kwargs["model"],
            "run": kwargs["run_id"],
            "variable": kwargs["var_id"],
            "fh": kwargs["fh"],
            "valid_time": kwargs["valid_time_override"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    def _warp(values, *args, **kwargs):
        captured["input_shape"] = np.asarray(values).shape
        return np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)

    def _write_value(values, output_path, **_kwargs):
        captured["written_shape"] = np.asarray(values).shape
        return _write_test_value_raster(Path(output_path), np.asarray(values, dtype=np.float32)) or Path(output_path)

    monkeypatch.setattr(ndfd_publish, "warp_to_target_grid", _warp)
    monkeypatch.setattr(ndfd_publish, "write_value_cog", _write_value)

    issue_time = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
    frame = NDFDSourceField(
        valid_time=datetime(2026, 5, 23, 0, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.ones((4, 5), dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/ds.mint.bin",
        source_filename="ds.mint.bin",
        source_units="[C]",
    )

    result = ndfd_publish.publish_ndfd_bundle(
        data_root=tmp_path,
        issue_time=issue_time,
        frames_by_var={"mint": [frame]},
    )

    assert result.run_id == "20260522_1700z"
    assert captured["input_shape"] == (4, 5)
    assert captured["written_shape"] == (2, 3)
    latest_payload = json.loads((tmp_path / "published" / "ndfd" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == result.run_id


def test_run_once_noops_when_latest_bundle_matches_formatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_time = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
    run_id = format_run_id(issue_time, include_minutes=True)
    model_root = tmp_path / "published" / "ndfd" / run_id
    model_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "published" / "ndfd" / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    manifests_dir = tmp_path / "manifests" / "ndfd"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    (manifests_dir / f"{run_id}.json").write_text(json.dumps({"variables": {"mint": {}}}))

    frame = NDFDSourceField(
        valid_time=datetime(2026, 5, 23, 0, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.ones((2, 2), dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/ds.mint.bin",
        source_filename="ds.mint.bin",
        source_units="[C]",
    )

    monkeypatch.setattr(ndfd_poller, "collect_latest_ndfd_fields", lambda timeout_seconds: (issue_time, {"mint": [frame]}))
    monkeypatch.setattr(
        ndfd_poller,
        "publish_ndfd_bundle",
        lambda **kwargs: pytest.fail("publish_ndfd_bundle should not run when the latest bundle already matches"),
    )

    result = ndfd_poller.run_once(
        ndfd_poller.NDFDPollerConfig(
            data_root=tmp_path,
            poll_seconds=1800,
            keep_runs=8,
            timeout_seconds=60.0,
        )
    )

    assert result.action == "noop"
    assert result.published_run_id == run_id


def test_publish_ndfd_bundle_fails_fast_when_grid_dependencies_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ndfd_publish, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(ndfd_publish, "build_grid_manifests_for_run_root", None)
    monkeypatch.setattr(ndfd_publish, "write_grid_frames_for_run_root", None)
    monkeypatch.setattr(ndfd_publish, "warp_to_target_grid", lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)))
    monkeypatch.setattr(ndfd_publish, "write_value_cog", lambda *args, **kwargs: pytest.fail("write_value_cog should not run when grid support is unavailable"))

    issue_time = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
    frame = NDFDSourceField(
        valid_time=datetime(2026, 5, 23, 0, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.ones((2, 2), dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/ds.mint.bin",
        source_filename="ds.mint.bin",
        source_units="[C]",
    )

    with pytest.raises(RuntimeError, match="Grid publishing requires optional brotli-backed grid dependencies"):
        ndfd_publish.publish_ndfd_bundle(
            data_root=tmp_path,
            issue_time=issue_time,
            frames_by_var={"mint": [frame]},
        )


def test_ndfd_variables_are_registered_for_grid_packing() -> None:
    unsupported = [var_id for var_id in NDFD_VARIABLE_CATALOG if not grid_code_supported("ndfd", var_id)]
    assert unsupported == []