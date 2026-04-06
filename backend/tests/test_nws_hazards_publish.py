from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import nws_hazards


def _write_county_reference(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "GEOID": "04013",
                            "NAME": "Maricopa",
                            "STUSPS": "AZ",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-112.8, 32.9], [-111.2, 32.9], [-111.2, 34.0], [-112.8, 34.0], [-112.8, 32.9]]],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "GEOID": "08031",
                            "NAME": "Denver",
                            "STUSPS": "CO",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-105.2, 39.6], [-104.6, 39.6], [-104.6, 39.95], [-105.2, 39.95], [-105.2, 39.6]]],
                        },
                    },
                ],
            }
        )
    )
    return path


def test_build_active_hazards_frame_rolls_alerts_up_to_counties_and_keeps_geometry_fallback(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "alert-1",
                    "status": "Actual",
                    "event": "Tornado Warning",
                    "headline": "Tornado Warning issued April 6",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T17:45:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-112.7, 33.0], [-111.5, 33.0], [-111.5, 33.8], [-112.7, 33.8], [-112.7, 33.0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "alert-2",
                    "status": "Actual",
                    "event": "Severe Thunderstorm Watch",
                    "headline": "Severe Thunderstorm Watch issued April 6",
                    "sent": "2026-04-06T16:55:00Z",
                    "effective": "2026-04-06T16:55:00Z",
                    "expires": "2026-04-06T19:00:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"UGC": ["AZC013"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-112.7, 33.0], [-111.5, 33.0], [-111.5, 33.8], [-112.7, 33.8], [-112.7, 33.0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "alert-3",
                    "status": "Actual",
                    "event": "Small Craft Advisory",
                    "headline": "Small Craft Advisory for coastal waters",
                    "sent": "2026-04-06T16:40:00Z",
                    "effective": "2026-04-06T16:40:00Z",
                    "expires": "2026-04-06T23:00:00Z",
                    "areaDesc": "Coastal waters",
                    "geocode": {"UGC": ["PZZ135"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-123.0, 37.0], [-122.0, 37.0], [-122.0, 38.0], [-123.0, 38.0], [-123.0, 37.0]]],
                },
            },
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(payload, county_reference_path=county_reference)
    assert frame.fh == 0
    assert frame.valid_time.isoformat() == "2026-04-06T17:30:00+00:00"

    county_feature = next(feature for feature in frame.features if feature["properties"].get("county_geoid") == "04013")
    assert county_feature["properties"]["risk_label"] == "Tornado Warning"
    assert county_feature["properties"]["alert_count"] == 2
    assert county_feature["properties"]["active_hazards"] == ["Tornado Warning", "Severe Thunderstorm Watch"]
    assert county_feature["properties"]["hover_label"] == "Maricopa: Tornado Warning +1 more"

    fallback_feature = next(feature for feature in frame.features if feature["properties"].get("area_description") == "Coastal waters")
    assert fallback_feature["properties"]["risk_label"] == "Small Craft Advisory"
    assert fallback_feature["geometry"]["type"] == "Polygon"


def test_publish_active_hazards_writes_manifest_latest_pointer_and_vector_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    county_reference = _write_county_reference(tmp_path / "hazards" / "county_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "alert-1",
                    "status": "Actual",
                    "event": "Tornado Warning",
                    "headline": "Tornado Warning issued April 6",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T17:45:00Z",
                    "geocode": {"SAME": ["004013"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-112.7, 33.0], [-111.5, 33.0], [-111.5, 33.8], [-112.7, 33.8], [-112.7, 33.0]]],
                },
            }
        ],
    }
    monkeypatch.setattr(nws_hazards, "fetch_active_alerts_geojson", lambda **_: payload)

    result = nws_hazards.publish_active_hazards(data_root=tmp_path, county_reference_path=county_reference)

    assert result.run_id == "20260406_1730z"
    assert result.frame_count == 1
    assert result.variable_ids == ["active"]

    latest_payload = json.loads((tmp_path / "published" / "nws_hazards" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260406_1730z"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "nws_hazards"
    assert manifest["metadata"]["time_axis_mode"] == "valid"
    assert manifest["metadata"]["source_fingerprint"] == result.fingerprint
    assert manifest["variables"]["active"]["frames"] == [{"fh": 0, "valid_time": "2026-04-06T17:30:00Z"}]

    sidecar = json.loads((result.published_run_dir / "active" / "fh000.json").read_text())
    assert sidecar["display_name"] == "Active Hazards"
    assert sidecar["legend_entries"][0]["label"] == "Tornado Warning"
    assert sidecar["vector_layers"]["primary"]["path"] == "vectors/fh000.geojson"

    vector_payload = json.loads((result.published_run_dir / "active" / "vectors" / "fh000.geojson").read_text())
    assert vector_payload["type"] == "FeatureCollection"
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Tornado Warning"