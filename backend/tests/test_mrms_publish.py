from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import mrms_publish


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
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def _configure_small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mrms_publish, "_expected_target_shape", lambda: (2, 3))
    monkeypatch.setattr(
        mrms_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)),
    )
    monkeypatch.setattr(
        mrms_publish,
        "write_rgba_cog",
        lambda rgba, output_path, **_: Path(output_path).write_bytes(b"rgba") or Path(output_path),
    )
    monkeypatch.setattr(
        mrms_publish,
        "write_value_cog",
        lambda values, output_path, **_: _write_test_value_raster(Path(output_path), np.asarray(values, dtype=np.float32))
        or Path(output_path),
    )
    monkeypatch.setattr(
        mrms_publish,
        "pregenerate_loop_webp_for_run",
        lambda **_: (0, 0),
    )


def test_publish_mrms_bundle_writes_manifest_and_latest_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    frames = [
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time,
            values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
            source_url="https://example.com/scan0.grib2.gz",
            source_filename="scan0.grib2.gz",
        ),
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time + timedelta(minutes=2),
            values=np.array([[21.0, 23.0, 25.0], [27.0, 29.0, 31.0]], dtype=np.float32),
            source_url="https://example.com/scan1.grib2.gz",
            source_filename="scan1.grib2.gz",
        ),
    ]

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    assert result.run_id == "20260327_1206z"
    assert result.frame_count == 2
    assert result.published_run_dir.is_dir()
    latest_payload = json.loads((tmp_path / "published" / "mrms" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260327_1206z"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "mrms"
    assert manifest["metadata"]["time_axis_mode"] == "observed"
    assert manifest["metadata"]["latest_scan_valid_time"] == "2026-03-27T12:02:00Z"
    assert manifest["metadata"]["target_frame_count"] == 2
    assert manifest["metadata"]["available_frame_count"] == 2
    reflectivity = manifest["variables"]["reflectivity"]
    assert reflectivity["expected_frames"] == 2
    assert reflectivity["available_frames"] == 2
    assert reflectivity["frames"] == [
        {"fh": 0, "valid_time": "2026-03-27T12:00:00Z"},
        {"fh": 1, "valid_time": "2026-03-27T12:02:00Z"},
    ]

    sidecar = json.loads((result.published_run_dir / "reflectivity" / "fh001.json").read_text())
    assert sidecar["valid_time"] == "2026-03-27T12:02:00Z"
    assert sidecar["source_filename"] == "scan1.grib2.gz"


def test_publish_mrms_bundle_warps_native_grid_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)
    captured: dict[str, tuple[int, int]] = {}

    def _warp(values, *args, **kwargs):
        captured["input_shape"] = np.asarray(values).shape
        return np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)

    monkeypatch.setattr(mrms_publish, "warp_to_target_grid", _warp)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.ones((4, 5), dtype=np.float32),
        source_crs="EPSG:4326",
        source_transform=from_origin(-130.0, 55.0, 0.01, 0.01),
    )

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    assert result.frame_count == 1
    assert captured["input_shape"] == (4, 5)


def test_publish_mrms_bundle_smooths_display_only_not_value_grid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)
    captured: dict[str, np.ndarray] = {}

    monkeypatch.setattr(
        mrms_publish,
        "_display_values_for_colorize",
        lambda values, **_: np.asarray(values, dtype=np.float32) + np.float32(1.5),
    )

    def _float_to_rgba(values, *_args, **_kwargs):
        captured["rgba_input"] = np.asarray(values, dtype=np.float32).copy()
        rgba = np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8)
        return rgba, {"legend_title": "MRMS Reflectivity (dBZ)"}

    def _write_value(values, output_path, **_kwargs):
        captured["value_input"] = np.asarray(values, dtype=np.float32).copy()
        return _write_test_value_raster(Path(output_path), captured["value_input"]) or Path(output_path)

    monkeypatch.setattr(mrms_publish, "float_to_rgba", _float_to_rgba)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _write_value)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )

    mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    np.testing.assert_allclose(
        captured["rgba_input"],
        np.array([[11.5, 13.5, 15.5], [17.5, 19.5, 21.5]], dtype=np.float32),
    )
    np.testing.assert_allclose(
        captured["value_input"],
        np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )


def test_failed_publish_preserves_previous_latest_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )
    first = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )
    assert first.run_id == "20260327_1206z"

    def _fail_rgba(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mrms_publish, "write_rgba_cog", _fail_rgba)

    with pytest.raises(RuntimeError, match="boom"):
        mrms_publish.publish_mrms_bundle(
            data_root=tmp_path,
            frames=[frame],
            publish_time=datetime(2026, 3, 27, 12, 8, tzinfo=timezone.utc),
        )

    latest_payload = json.loads((tmp_path / "published" / "mrms" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260327_1206z"
    assert not (tmp_path / "published" / "mrms" / "20260327_1208z").exists()
