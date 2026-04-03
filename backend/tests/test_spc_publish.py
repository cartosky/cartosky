from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import spc_publish


def test_normalize_spc_geojson_maps_risks_and_orders_features() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "DN": 5,
                    "label": "ENH",
                    "label2": "Enhanced Risk",
                    "VALID": "2026-03-30T00:00:00Z",
                    "ISSUE": "2026-03-29T18:00:00Z",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "DN": 2,
                    "label": "TSTM",
                    "label2": "General Thunderstorms Risk",
                    "VALID": "2026-03-30T00:00:00Z",
                    "ISSUE": "2026-03-29T18:00:00Z",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-100.0, 34.0], [-99.0, 34.0], [-99.0, 35.0], [-100.0, 34.0]]],
                },
            },
        ],
    }

    frame = spc_publish.normalize_spc_geojson(payload, day_label="Day 1", fh=0)

    assert frame.fh == 0
    assert frame.day_label == "Day 1"
    assert frame.issue_time == datetime(2026, 3, 29, 18, 0, tzinfo=timezone.utc)
    assert frame.valid_time == datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc)
    assert [feature["properties"]["risk_code"] for feature in frame.features] == [1, 4]
    assert [feature["properties"]["risk_label"] for feature in frame.features] == ["T-Storms", "Enhanced"]
    assert frame.features[0]["properties"]["fill"] == "#808080"
    assert frame.features[1]["properties"]["fill"] == "#FFA500"
    assert all(feature["properties"]["stroke"] == "#000000" for feature in frame.features)


def test_publish_spc_bundle_writes_manifest_latest_pointer_and_vector_sidecars(tmp_path: Path) -> None:
    issue_time = datetime(2026, 4, 1, 6, 30, tzinfo=timezone.utc)
    frames = [
        spc_publish.SPCFramePayload(
            fh=0,
            day_label="Day 1",
            valid_time=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
            issue_time=issue_time,
            features=[
                {
                    "type": "Feature",
                    "properties": {
                        "risk_code": 3,
                        "risk_label": "Slight",
                        "fill": "#FFFF00",
                        "fill_opacity": 0.65,
                        "stroke": "#000000",
                        "stroke_width": 1.25,
                        "sort_rank": 3,
                        "day_label": "Day 1",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]],
                    },
                }
            ],
        ),
        spc_publish.SPCFramePayload(
            fh=1,
            day_label="Day 2",
            valid_time=datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
            issue_time=issue_time,
            features=[],
        ),
        spc_publish.SPCFramePayload(
            fh=2,
            day_label="Day 3",
            valid_time=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
            issue_time=issue_time,
            features=[],
        ),
    ]

    result = spc_publish.publish_spc_bundle(data_root=tmp_path, frames=frames, issue_time=issue_time)

    assert result.run_id == "20260401_0630z"
    assert result.frame_count == 3
    assert result.published_run_dir.is_dir()

    latest_payload = json.loads((tmp_path / "published" / "spc" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260401_0630z"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "spc"
    assert manifest["metadata"]["time_axis_mode"] == "valid"
    assert manifest["metadata"]["target_frame_count"] == 3
    assert manifest["variables"]["convective"]["frames"] == [
        {"fh": 0, "valid_time": "2026-04-01T12:00:00Z"},
        {"fh": 1, "valid_time": "2026-04-02T12:00:00Z"},
        {"fh": 2, "valid_time": "2026-04-03T12:00:00Z"},
    ]

    sidecar = json.loads((result.published_run_dir / "convective" / "fh000.json").read_text())
    assert sidecar["legend_entries"][0]["label"] == "T-Storms"
    assert sidecar["vector_layers"]["primary"]["path"] == "vectors/fh000.geojson"
    assert sidecar["day_label"] == "Day 1"
    assert sidecar["issue_time"] == "2026-04-01T06:30:00Z"

    vector_payload = json.loads((result.published_run_dir / "convective" / "vectors" / "fh000.geojson").read_text())
    assert vector_payload["type"] == "FeatureCollection"
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Slight"