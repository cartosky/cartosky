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

from app.services import rtma_ru_publish


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
    monkeypatch.setattr(rtma_ru_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        rtma_ru_publish,
        "write_value_cog",
        lambda values, output_path, **_: _write_test_value_raster(Path(output_path), np.asarray(values, dtype=np.float32)) or Path(output_path),
    )

    def _float_to_rgba(values, color_map_id, **_kwargs):
        values_arr = np.asarray(values, dtype=np.float32)
        return (
            np.zeros((4, values_arr.shape[0], values_arr.shape[1]), dtype=np.uint8),
            {
                "kind": "continuous",
                "units": "hPa" if color_map_id == "mslp" else ("mph" if "w" in str(color_map_id) else "F"),
                "min": float(np.nanmin(values_arr)),
                "max": float(np.nanmax(values_arr)),
                "display_name": str(color_map_id),
            },
        )

    monkeypatch.setattr(rtma_ru_publish, "float_to_rgba", _float_to_rgba)


def _frame(valid_time: datetime, temp: float) -> rtma_ru_publish.CurrentAnalysisBundleFrame:
    values = np.array([[temp, temp + 1.0], [temp + 2.0, temp + 3.0]], dtype=np.float32)
    return rtma_ru_publish.CurrentAnalysisBundleFrame(
        valid_time=valid_time,
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
        values_by_var={
            "tmp2m": values,
            "dp2m": values - 4.0,
            "wspd10m": values + 10.0,
            "wgst10m": values + 14.0,
            "spres": values + 1000.0,
        },
        source_metadata={
            "provider": "noaa",
            "model_family": "rtma_ru",
        },
        source_metadata_by_var={
            "spres": {"inventory_line": ":PRES:surface:anl:"},
        },
        source_filename_by_var={
            "tmp2m": "rtma_tmp2m.grib2",
            "spres": "rtma_spres.grib2",
        },
    )


def test_publish_current_analysis_bundle_writes_manifest_and_latest_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_publish(monkeypatch)
    base_time = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    frames = [_frame(base_time, 70.0), _frame(base_time + timedelta(minutes=15), 72.0)]

    result = rtma_ru_publish.publish_current_analysis_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=datetime(2026, 5, 21, 12, 17, tzinfo=timezone.utc),
        expected_frame_count=8,
    )

    assert result.run_id == "20260521_1217z"
    assert result.frame_count == 2
    latest_payload = json.loads((tmp_path / "published" / "current_analysis" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260521_1217z"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "current_analysis"
    assert manifest["metadata"]["target_frame_count"] == 8
    assert manifest["metadata"]["available_frame_count"] == 2
    assert manifest["metadata"]["latest_scan_valid_time"] == "2026-05-21T12:15:00Z"
    assert manifest["metadata"]["variables_published"] == ["tmp2m", "dp2m", "wspd10m", "wgst10m", "spres"]

    tmp2m = manifest["variables"]["tmp2m"]
    assert tmp2m["expected_frames"] == 8
    assert tmp2m["available_frames"] == 2
    assert tmp2m["frames"] == [
        {"fh": 0, "valid_time": "2026-05-21T12:00:00Z"},
        {"fh": 1, "valid_time": "2026-05-21T12:15:00Z"},
    ]

    spres_sidecar = json.loads((result.published_run_dir / "spres" / "fh001.json").read_text())
    assert spres_sidecar["valid_time"] == "2026-05-21T12:15:00Z"
    assert spres_sidecar["units"] == "hPa"
    assert spres_sidecar["source_metadata"]["inventory_line"] == ":PRES:surface:anl:"
    assert spres_sidecar["source_filename"] == "rtma_spres.grib2"


def test_publish_current_analysis_bundle_reuses_previous_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_publish(monkeypatch)
    base_time = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    first_result = rtma_ru_publish.publish_current_analysis_bundle(
        data_root=tmp_path,
        frames=[_frame(base_time, 68.0)],
        publish_time=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
    )
    assert first_result.run_id == "20260521_1205z"

    _, previous_frames = rtma_ru_publish.load_latest_published_current_analysis_frames(tmp_path)
    assert len(previous_frames) == 1
    assert set(previous_frames[0].value_paths) == {"tmp2m", "dp2m", "wspd10m", "wgst10m", "spres"}

    second_result = rtma_ru_publish.publish_current_analysis_bundle(
        data_root=tmp_path,
        frames=[],
        previous_frames=previous_frames,
        publish_time=datetime(2026, 5, 21, 12, 10, tzinfo=timezone.utc),
        target_frame_count=1,
    )

    assert second_result.run_id == "20260521_1210z"
    assert (second_result.published_run_dir / "tmp2m" / "fh000.val.cog.tif").exists()
    reused_sidecar = json.loads((second_result.published_run_dir / "tmp2m" / "fh000.json").read_text())
    assert reused_sidecar["run"] == "20260521_1210z"
    assert reused_sidecar["fh"] == 0