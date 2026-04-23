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
                            "STATEFP": "04",
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
                            "STATEFP": "08",
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


def _write_zone_reference(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "zone_code": "LSZ242",
                            "name": "Ontonagon to Upper Entrance of Portage Canal MI",
                            "state": "MI",
                            "zone_type": "coastal",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-89.6, 46.8], [-88.5, 46.8], [-88.5, 47.1], [-89.6, 47.1], [-89.6, 46.8]]],
                        },
                    }
                ],
            }
        )
    )
    return path


def test_build_active_hazards_frame_rolls_alerts_up_to_counties_and_keeps_geometry_fallback(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
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

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )
    assert frame.fh == 0
    assert frame.valid_time.isoformat() == "2026-04-06T17:30:00+00:00"

    county_feature = next(feature for feature in frame.features if feature["properties"].get("county_geoid") == "04013")
    assert county_feature["properties"]["risk_label"] == "Tornado Warning"
    assert county_feature["properties"]["fill"] == "#FF0000"
    assert county_feature["properties"]["alert_count"] == 2
    assert county_feature["properties"]["active_hazards"] == ["Tornado Warning", "Severe Thunderstorm Watch"]
    assert county_feature["properties"]["hover_label"] == "Maricopa: Tornado Warning +1 more"
    assert county_feature["properties"]["state"] == "AZ"

    fallback_feature = next(feature for feature in frame.features if feature["properties"].get("area_description") == "Coastal waters")
    assert fallback_feature["properties"]["risk_label"] == "Small Craft Advisory"
    assert fallback_feature["properties"]["fill"] == "#D8BFD8"
    assert fallback_feature["geometry"]["type"] == "Polygon"


def test_build_active_hazards_frame_prefers_precise_geometry_for_flood_alerts(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "flood-1",
                    "status": "Actual",
                    "event": "Flood Warning",
                    "headline": "Flood Warning for river reach",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Kent, MI",
                    "geocode": {"UGC": ["MIC081"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-85.72, 42.86], [-85.67, 42.86], [-85.67, 42.92], [-85.72, 42.92], [-85.72, 42.86]]],
                },
            }
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )
    assert len(frame.features) == 1
    feature = frame.features[0]
    assert feature["properties"]["risk_label"] == "Flood Warning"
    assert "county_geoid" not in feature["properties"]
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"]["fill"] == "#00FF00"


def test_build_active_hazards_frame_resolves_zone_geometry_for_marine_alerts(
    tmp_path: Path,
) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "marine-1",
                    "status": "Actual",
                    "event": "Gale Warning",
                    "headline": "Gale Warning for Lake Superior",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Ontonagon to Upper Entrance of Portage Canal MI",
                    "geocode": {"UGC": ["LSZ242"]},
                },
                "geometry": None,
            }
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )
    assert len(frame.features) == 1
    feature = frame.features[0]
    assert feature["properties"]["risk_label"] == "Gale Warning"
    assert feature["properties"]["zone_code"] == "LSZ242"
    assert feature["properties"]["zone_name"] == "Ontonagon to Upper Entrance of Portage Canal MI"
    assert feature["properties"]["state"] == "MI"
    assert feature["geometry"]["type"] == "Polygon"


def test_build_active_hazards_frame_prefers_zone_geometry_over_county_rollup_for_public_zone_alerts(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "wind-1",
                    "status": "Actual",
                    "event": "Wind Advisory",
                    "headline": "Wind Advisory for Okanogan Valley",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Okanogan Valley",
                    "geocode": {"UGC": ["LSZ242"], "SAME": ["004013"]},
                },
                "geometry": None,
            }
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )
    assert len(frame.features) == 1
    feature = frame.features[0]
    assert feature["properties"]["zone_code"] == "LSZ242"
    assert "county_geoid" not in feature["properties"]


def test_build_active_hazards_frame_prefers_zone_geometry_when_alert_has_both_zone_and_same_codes(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "mixed-1",
                    "status": "Actual",
                    "event": "Wind Advisory",
                    "headline": "Wind Advisory for mixed geocodes",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Mixed zone",
                    "geocode": {"UGC": ["LSZ242"], "SAME": ["004013"]},
                },
                "geometry": None,
            }
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )
    assert len(frame.features) == 1
    feature = frame.features[0]
    assert feature["properties"]["zone_code"] == "LSZ242"
    assert "county_geoid" not in feature["properties"]


def test_build_active_hazards_frame_dissolves_overlapping_same_style_zone_polygons(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = tmp_path / "zone_reference.geojson"
    zone_reference.parent.mkdir(parents=True, exist_ok=True)
    zone_reference.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "zone_code": "AAZ001",
                            "name": "Alpha Zone",
                            "state": "AA",
                            "zone_type": "fire",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "zone_code": "AAZ002",
                            "name": "Beta Zone",
                            "state": "AA",
                            "zone_type": "fire",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[1.0, 0.0], [3.0, 0.0], [3.0, 2.0], [1.0, 2.0], [1.0, 0.0]]],
                        },
                    },
                ],
            }
        )
    )
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "red-1",
                    "status": "Actual",
                    "event": "Red Flag Warning",
                    "headline": "Red Flag Warning for alpha",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Alpha Zone",
                    "geocode": {"UGC": ["AAZ001"]},
                },
                "geometry": None,
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "red-2",
                    "status": "Actual",
                    "event": "Red Flag Warning",
                    "headline": "Red Flag Warning for beta",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:30:00Z",
                    "areaDesc": "Beta Zone",
                    "geocode": {"UGC": ["AAZ002"]},
                },
                "geometry": None,
            },
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )

    assert len(frame.features) == 1
    feature = frame.features[0]
    assert feature["properties"]["risk_label"] == "Red Flag Warning"
    assert feature["properties"]["fill"] == "#FF1493"
    assert feature["properties"]["alert_count"] == 2
    assert sorted(feature["properties"]["alert_ids"]) == ["red-1", "red-2"]
    assert feature["properties"]["hover_label"] == "Red Flag Warning (2 areas)"
    assert feature["properties"]["zone_codes"] == ["AAZ001", "AAZ002"]
    assert feature["geometry"]["type"] == "Polygon"


def test_sync_active_zone_reference_uses_affected_zone_namespace_for_fire_zones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_urls: list[str] = []

    def _fake_fetch_geojson_with_retry(*, url: str, timeout_seconds: float, log_retries: bool, client):
        del timeout_seconds, log_retries, client
        observed_urls.append(url)
        if url.endswith("/zones/fire/COZ220"):
            return {
                "type": "Feature",
                "properties": {
                    "id": "https://api.weather.gov/zones/fire/COZ220",
                    "name": "Northwest Colorado Fire Zone 220",
                    "state": "CO",
                    "type": "fire",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-108.0, 39.0], [-107.0, 39.0], [-107.0, 40.0], [-108.0, 40.0], [-108.0, 39.0]]],
                },
            }
        raise nws_hazards.NWSHazardsError("unexpected lookup")

    monkeypatch.setattr(nws_hazards, "_fetch_geojson_with_retry", _fake_fetch_geojson_with_retry)

    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "fire-1",
                    "status": "Actual",
                    "event": "Red Flag Warning",
                    "headline": "Red Flag Warning for Colorado fire zone",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Colorado Fire Weather Zone 220",
                    "affectedZones": ["https://api.weather.gov/zones/fire/COZ220"],
                    "geocode": {"UGC": ["COZ220"]},
                },
                "geometry": None,
            }
        ],
    }

    result = nws_hazards.sync_active_zone_reference(
        payload=payload,
        zone_reference_path=tmp_path / "zone_reference.geojson",
        timeout_seconds=10.0,
        api_base="https://api.weather.gov",
    )

    assert observed_urls == ["https://api.weather.gov/zones/fire/COZ220"]
    assert result.resolved_zone_codes == ("COZ220",)


def test_publish_active_hazards_writes_manifest_latest_pointer_and_vector_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    county_reference = _write_county_reference(tmp_path / "hazards" / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "hazards" / "zone_reference.geojson")
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

    result = nws_hazards.publish_active_hazards(
        data_root=tmp_path,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )

    assert result.run_id == "20260406_1730z"
    assert result.frame_count == 1
    assert result.variable_ids == ["active"]

    latest_payload = json.loads((tmp_path / "published" / "nws_hazards" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260406_1730z"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "nws_hazards"
    assert manifest["metadata"]["time_axis_mode"] == "valid"
    assert manifest["metadata"]["source_fingerprint"] == result.fingerprint
    assert isinstance(manifest["metadata"]["zone_reference_signature"], str)
    assert manifest["variables"]["active"]["frames"] == [{"fh": 0, "valid_time": "2026-04-06T17:30:00Z"}]

    sidecar = json.loads((result.published_run_dir / "active" / "fh000.json").read_text())
    assert sidecar["display_name"] == "Active Hazards"
    assert sidecar["legend_entries"][0]["label"] == "Tornado Warning"
    assert sidecar["vector_layers"]["primary"]["path"] == "vectors/fh000.geojson"
    assert sidecar["issue_time"] == "2026-04-06T17:30:00Z"

    vector_payload = json.loads((result.published_run_dir / "active" / "vectors" / "fh000.geojson").read_text())
    assert vector_payload["type"] == "FeatureCollection"
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Tornado Warning"