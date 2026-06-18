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

from app.services import goes_publish


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
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def _configure_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(goes_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        goes_publish,
        "write_value_cog",
        lambda values, output_path, **_: _write_test_value_raster(Path(output_path), np.asarray(values, dtype=np.float32)) or Path(output_path),
    )
    monkeypatch.setattr(
        goes_publish,
        "float_to_rgba",
        lambda values, *_args, **_kwargs: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {
                "kind": "continuous",
                "units": "K",
                "min": float(np.nanmin(values)),
                "max": float(np.nanmax(values)),
                "display_name": "Clean IR",
            },
        ),
    )


def test_publish_goes_bundle_writes_midpoint_valid_time_and_source_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_publish(monkeypatch)
    slot = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    midpoint = datetime(2026, 5, 21, 12, 2, 36, tzinfo=timezone.utc)
    frame = goes_publish.GOESBundleFrame(
        valid_time=midpoint,
        slot_time=slot,
        values=np.array([[250.0, 260.0], [270.0, 280.0]], dtype=np.float32),
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
        source_bucket="noaa-goes19",
        source_key="ABI-L2-CMIPC/2026/141/12/file.nc",
        source_filename="file.nc",
        source_size_bytes=4_000_000,
        source_last_modified=datetime(2026, 5, 21, 12, 4, tzinfo=timezone.utc),
        source_metadata={
            "time_coverage_start": "2026-05-21T12:01:17.5Z",
            "time_coverage_end": "2026-05-21T12:03:56.0Z",
            "slot_time": "2026-05-21T12:00:00Z",
            "satellite": "goes19",
            "product": "ABI-L2-CMIPC",
            "sector": "C",
            "band": 13,
        },
    )
    result = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
        expected_frame_count=13,
    )
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "goes-east"
    assert manifest["metadata"]["target_frame_count"] == 13
    assert manifest["metadata"]["latest_scan_valid_time"] == "2026-05-21T12:02:36Z"
    assert manifest["metadata"]["satellite"] == "goes19"
    assert manifest["variables"]["ir13"]["expected_frames"] == 13
    assert manifest["variables"]["ir13"]["available_frames"] == 1
    assert manifest["variables"]["ir13"]["frames"] == [{"fh": 0, "valid_time": "2026-05-21T12:02:36Z"}]
    sidecar = json.loads((result.published_run_dir / "ir13" / "fh000.json").read_text())
    assert sidecar["valid_time"] == "2026-05-21T12:02:36Z"
    assert sidecar["source_metadata"]["time_coverage_start"] == "2026-05-21T12:01:17.5Z"
    assert sidecar["source_metadata"]["time_coverage_end"] == "2026-05-21T12:03:56.0Z"
    assert sidecar["source_metadata"]["size_bytes"] == 4_000_000


def test_publish_goes_bundle_reuses_previous_frame_by_slot_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_publish(monkeypatch)
    slot = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    first = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 250.0,
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
        source_metadata={"slot_time": "2026-05-21T12:00:00Z"},
    )
    first_result = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[first],
        publish_time=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
    )
    _, previous = goes_publish.load_latest_published_goes_frames(tmp_path)
    assert previous[0].slot_time == slot

    second_result = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[],
        previous_frames=previous,
        publish_time=datetime(2026, 5, 21, 12, 10, tzinfo=timezone.utc),
        target_frame_count=1,
    )
    assert second_result.run_id == "20260521_1210z"
    assert (second_result.published_run_dir / "ir13" / "fh000.val.cog.tif").exists()
    assert first_result.run_id != second_result.run_id


def test_publish_goes_bundle_seeds_new_run_with_previous_latest_sibling_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_publish(monkeypatch)
    slot = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    ir_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 250.0,
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
        source_metadata={"slot_time": "2026-05-21T12:00:00Z"},
    )
    first_result = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[ir_frame],
        publish_time=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
    )
    assert first_result.run_id == "20260521_1205z"

    vis_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=17),
        slot_time=slot + timedelta(minutes=15),
        values=np.array([[0.05, 0.15], [0.30, 0.50]], dtype=np.float32),
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
        source_metadata={"slot_time": "2026-05-21T12:15:00Z"},
    )
    second_result = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[vis_frame],
        publish_time=datetime(2026, 5, 21, 12, 10, tzinfo=timezone.utc),
        band_config=goes_publish.BAND_CONFIG_VIS2,
    )

    assert second_result.run_id == "20260521_1210z"
    assert (second_result.published_run_dir / "ir13" / "fh000.val.cog.tif").exists()
    assert (second_result.published_run_dir / "vis2" / "fh000.val.cog.tif").exists()

    manifest = json.loads(second_result.manifest_path.read_text())
    assert set(manifest["variables"]) == {"ir13", "vis2"}
    assert manifest["variables"]["ir13"]["frames"] == [{"fh": 0, "valid_time": "2026-05-21T12:02:00Z"}]
    assert manifest["variables"]["vis2"]["frames"] == [{"fh": 0, "valid_time": "2026-05-21T12:17:00Z"}]

    preserved_sidecar = json.loads((second_result.published_run_dir / "ir13" / "fh000.json").read_text())
    assert preserved_sidecar["run"] == second_result.run_id
