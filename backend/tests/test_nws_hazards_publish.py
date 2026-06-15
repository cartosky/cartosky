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


def test_mrms_overlay_prefers_native_polygon_geometry_over_county_rollup(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    storm_polygon = {
        "type": "Polygon",
        "coordinates": [[[-112.05, 33.35], [-111.95, 33.35], [-111.95, 33.45], [-112.05, 33.45], [-112.05, 33.35]]],
    }
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "ffw-1",
                    "status": "Actual",
                    "event": "Flash Flood Warning",
                    "headline": "Flash Flood Warning for Maricopa County",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T18:30:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
                },
                "geometry": storm_polygon,
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "tor-1",
                    "status": "Actual",
                    "event": "Tornado Warning",
                    "headline": "Tornado Warning for Maricopa County",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T17:45:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
                },
                "geometry": storm_polygon,
            },
        ],
    }

    standalone = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
    )
    assert len(standalone.features) == 1
    county_feature = standalone.features[0]
    assert county_feature["properties"]["county_geoid"] == "04013"
    assert county_feature["properties"]["risk_label"] == "Tornado Warning"
    assert county_feature["geometry"] != storm_polygon

    mrms = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
        prefer_native_polygon_geometry=True,
    )
    assert len(mrms.features) == 2
    mrms_geometries = {feature["properties"]["risk_label"]: feature for feature in mrms.features}
    assert "county_geoid" not in mrms_geometries["Flash Flood Warning"]["properties"]
    assert mrms_geometries["Flash Flood Warning"]["geometry"] == storm_polygon
    assert "county_geoid" not in mrms_geometries["Tornado Warning"]["properties"]
    assert mrms_geometries["Tornado Warning"]["geometry"] == storm_polygon


def test_mrms_overlay_keeps_native_flash_flood_warnings_discrete(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "ffw-1",
                    "status": "Actual",
                    "event": "Flash Flood Warning",
                    "headline": "Flash Flood Warning 1",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T18:30:00Z",
                    "areaDesc": "North Maricopa",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-112.05, 33.35], [-111.95, 33.35], [-111.95, 33.45], [-112.05, 33.45], [-112.05, 33.35]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "ffw-2",
                    "status": "Actual",
                    "event": "Flash Flood Warning",
                    "headline": "Flash Flood Warning 2",
                    "sent": "2026-04-06T17:06:00Z",
                    "effective": "2026-04-06T17:06:00Z",
                    "expires": "2026-04-06T18:35:00Z",
                    "areaDesc": "South Maricopa",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-112.05, 33.15], [-111.95, 33.15], [-111.95, 33.25], [-112.05, 33.25], [-112.05, 33.15]]],
                },
            },
        ],
    }

    frame = nws_hazards.build_active_hazards_frame(
        payload,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
        prefer_native_polygon_geometry=True,
    )
    flash_flood_features = [
        feature
        for feature in frame.features
        if feature["properties"]["risk_label"] == "Flash Flood Warning"
    ]
    assert len(flash_flood_features) == 2
    alert_ids = {feature["properties"]["alert_ids"][0] for feature in flash_flood_features}
    assert alert_ids == {"ffw-1", "ffw-2"}
    assert all(feature["properties"]["geometry_source"] == "native_alert" for feature in flash_flood_features)


def test_mrms_overlay_applies_green_colors_for_flash_flood_products(
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
                    "id": "ffw-1",
                    "status": "Actual",
                    "event": "Flash Flood Warning",
                    "headline": "Flash Flood Warning",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T18:30:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-112.05, 33.35], [-111.95, 33.35], [-111.95, 33.45], [-112.05, 33.45], [-112.05, 33.35]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "ffw-watch-1",
                    "status": "Actual",
                    "event": "Flash Flood Watch",
                    "headline": "Flash Flood Watch",
                    "sent": "2026-04-06T16:55:00Z",
                    "effective": "2026-04-06T16:55:00Z",
                    "expires": "2026-04-06T19:00:00Z",
                    "areaDesc": "Denver County",
                    "geocode": {"SAME": ["008031"], "UGC": ["COC031"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-105.0, 39.7], [-104.8, 39.7], [-104.8, 39.85], [-105.0, 39.85], [-105.0, 39.7]]],
                },
            },
        ],
    }

    monkeypatch.setattr(
        nws_hazards,
        "sync_active_zone_reference",
        lambda **_: nws_hazards.ZoneReferenceSyncResult(
            path=zone_reference,
            needed_zone_codes=(),
            resolved_zone_codes=(),
            signature="test",
            updated=False,
        ),
    )

    overlay = nws_hazards.build_mrms_warnings_overlay_geojson(tmp_path, payload=payload)
    by_label = {
        feature["properties"]["risk_label"]: feature["properties"]
        for feature in overlay["features"]
    }
    for label in ("Flash Flood Warning", "Flash Flood Watch"):
        props = by_label[label]
        assert props["fill"] == "#00FF00"
        assert props["stroke"] == "#00FF00"
        assert props["fill_opacity"] == 0.1
        assert props["stroke_width"] == 3.5


def test_mrms_overlay_skips_zone_sync_and_filters_non_convective_products(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nws_hazards._mrms_warnings_overlay_cache = None
    county_reference = _write_county_reference(tmp_path / "hazards" / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "hazards" / "zone_reference.geojson")
    del county_reference, zone_reference
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "ffw-1",
                    "status": "Actual",
                    "event": "Flash Flood Warning",
                    "headline": "Flash Flood Warning",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T18:30:00Z",
                    "areaDesc": "Denver County",
                    "geocode": {"SAME": ["008031"], "UGC": ["COC031"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-105.0, 39.7], [-104.8, 39.7], [-104.8, 39.85], [-105.0, 39.85], [-105.0, 39.7]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "heat-1",
                    "status": "Actual",
                    "event": "Heat Advisory",
                    "headline": "Heat Advisory",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T18:30:00Z",
                    "areaDesc": "Somewhere hot",
                    "geocode": {"UGC": ["TXZ123"]},
                },
                "geometry": None,
            },
        ],
    }
    sync_calls = 0

    def fake_sync_active_zone_reference(**_kwargs) -> nws_hazards.ZoneReferenceSyncResult:
        nonlocal sync_calls
        sync_calls += 1
        raise AssertionError("MRMS overlay live path should not sync zone references")

    monkeypatch.setattr(nws_hazards, "sync_active_zone_reference", fake_sync_active_zone_reference)

    overlay = nws_hazards.build_mrms_warnings_overlay_geojson(tmp_path, payload=payload)
    assert len(overlay["features"]) == 1
    assert overlay["features"][0]["properties"]["risk_label"] == "Flash Flood Warning"
    assert sync_calls == 0


def test_mrms_overlay_build_uses_in_memory_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nws_hazards._mrms_warnings_overlay_cache = None
    county_reference = _write_county_reference(tmp_path / "hazards" / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "hazards" / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "ffw-1",
                    "status": "Actual",
                    "event": "Flash Flood Warning",
                    "headline": "Flash Flood Warning",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T18:30:00Z",
                    "areaDesc": "Denver County",
                    "geocode": {"SAME": ["008031"], "UGC": ["COC031"]},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-105.0, 39.7], [-104.8, 39.7], [-104.8, 39.85], [-105.0, 39.85], [-105.0, 39.7]]],
                },
            },
        ],
    }
    sync_calls = 0

    def fake_sync_active_zone_reference(**kwargs) -> nws_hazards.ZoneReferenceSyncResult:
        nonlocal sync_calls
        sync_calls += 1
        return nws_hazards.ZoneReferenceSyncResult(
            path=zone_reference,
            needed_zone_codes=(),
            resolved_zone_codes=(),
            signature="test",
            updated=False,
        )

    monkeypatch.setattr(nws_hazards, "sync_active_zone_reference", fake_sync_active_zone_reference)

    first = nws_hazards.build_mrms_warnings_overlay_geojson(tmp_path, payload=payload)
    second = nws_hazards.build_mrms_warnings_overlay_geojson(tmp_path, payload=payload)
    assert first == second
    assert sync_calls == 0


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

    fill_features = [feature for feature in frame.features if feature["properties"].get("geometry_role") == "fill"]
    outline_feature = next(feature for feature in frame.features if feature["geometry"]["type"] in {"LineString", "MultiLineString"})

    assert len(fill_features) == 2
    assert {feature["properties"]["risk_label"] for feature in fill_features} == {"Red Flag Warning"}
    assert {feature["properties"]["fill"] for feature in fill_features} == {"#FF1493"}
    assert {feature["properties"]["alert_count"] for feature in fill_features} == {1}
    assert sorted(feature["properties"]["alert_ids"] for feature in fill_features) == [["red-1"], ["red-2"]]
    assert {feature["properties"]["hover_label"] for feature in fill_features} == {"Alpha Zone: Red Flag Warning", "Beta Zone: Red Flag Warning"}
    assert sorted(feature["properties"]["zone_code"] for feature in fill_features) == ["AAZ001", "AAZ002"]
    assert {feature["properties"]["stroke_width"] for feature in fill_features} == {0.0}
    assert outline_feature["properties"]["geometry_role"] == "outline"
    assert outline_feature["properties"]["fill_opacity"] == 0.0
    assert outline_feature["properties"]["hover_label"] == "Red Flag Warning (2 areas)"
    assert outline_feature["properties"]["zone_codes"] == ["AAZ001", "AAZ002"]


def test_build_active_hazards_frame_keeps_red_flag_warning_style_consistent_across_county_and_zone_sources(tmp_path: Path) -> None:
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
                            "name": "Alpha Fire Zone",
                            "state": "AA",
                            "zone_type": "fire",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-101.0, 35.0], [-100.0, 35.0], [-100.0, 36.0], [-101.0, 36.0], [-101.0, 35.0]]],
                        },
                    }
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
                    "id": "red-zone-1",
                    "status": "Actual",
                    "event": "Red Flag Warning",
                    "headline": "Red Flag Warning for fire zone",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Alpha Fire Zone",
                    "affectedZones": ["https://api.weather.gov/zones/fire/AAZ001"],
                    "geocode": {"UGC": ["AAZ001"]},
                },
                "geometry": None,
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "red-county-1",
                    "status": "Actual",
                    "event": "Red Flag Warning",
                    "headline": "Red Flag Warning for county fallback",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
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

    zone_fill_feature = next(feature for feature in frame.features if feature["properties"].get("zone_code") == "AAZ001")
    county_fill_feature = next(feature for feature in frame.features if feature["properties"].get("county_geoid") == "04013")
    outline_feature = next(feature for feature in frame.features if feature["geometry"]["type"] in {"LineString", "MultiLineString"})

    assert zone_fill_feature["properties"]["risk_label"] == "Red Flag Warning"
    assert zone_fill_feature["properties"]["fill_opacity"] == 0.42
    assert zone_fill_feature["properties"]["stroke_width"] == 0.0
    assert zone_fill_feature["properties"]["zone_code"] == "AAZ001"

    assert county_fill_feature["properties"]["risk_label"] == "Red Flag Warning"
    assert county_fill_feature["properties"]["fill_opacity"] == 0.42
    assert county_fill_feature["properties"]["stroke_width"] == 0.0
    assert county_fill_feature["properties"]["county_geoid"] == "04013"

    assert outline_feature["properties"]["risk_label"] == "Red Flag Warning"
    assert outline_feature["properties"]["fill_opacity"] == 0.0
    assert outline_feature["properties"]["stroke_width"] == 1.6
    assert outline_feature["properties"]["zone_codes"] == ["AAZ001"]
    assert outline_feature["properties"]["county_geoids"] == ["04013"]


def test_build_active_hazards_frame_splits_single_red_flag_warning_area_into_fill_and_outline(tmp_path: Path) -> None:
    county_reference = _write_county_reference(tmp_path / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "zone_reference.geojson")
    payload = {
        "type": "FeatureCollection",
        "updated": "2026-04-06T17:30:00Z",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "red-county-1",
                    "status": "Actual",
                    "event": "Red Flag Warning",
                    "headline": "Red Flag Warning for county fallback",
                    "sent": "2026-04-06T17:05:00Z",
                    "effective": "2026-04-06T17:05:00Z",
                    "expires": "2026-04-06T20:00:00Z",
                    "areaDesc": "Maricopa County",
                    "geocode": {"SAME": ["004013"], "UGC": ["AZC013"]},
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

    fill_feature = next(feature for feature in frame.features if feature["properties"].get("geometry_role") == "fill")
    outline_feature = next(feature for feature in frame.features if feature["properties"].get("geometry_role") == "outline")

    assert len(frame.features) == 2
    assert fill_feature["properties"]["risk_label"] == "Red Flag Warning"
    assert fill_feature["properties"]["fill_opacity"] == 0.42
    assert fill_feature["properties"]["stroke_width"] == 0.0
    assert outline_feature["properties"]["risk_label"] == "Red Flag Warning"
    assert outline_feature["properties"]["fill_opacity"] == 0.0
    assert outline_feature["properties"]["stroke_width"] == 1.6


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
    assert manifest["variables"]["active"]["frames"][0]["fh"] == 0
    assert manifest["variables"]["active"]["frames"][0]["valid_time"] == "2026-04-06T17:30:00Z"
    assert isinstance(manifest["variables"]["active"]["frames"][0]["generated_at"], str)

    sidecar = json.loads((result.published_run_dir / "active" / "fh000.json").read_text())
    assert sidecar["display_name"] == "Active Hazards"
    assert sidecar["legend_entries"][0]["label"] == "Tornado Warning"
    assert sidecar["vector_layers"]["primary"]["path"] == "vectors/fh000.geojson"
    assert sidecar["issue_time"] == "2026-04-06T17:30:00Z"

    vector_payload = json.loads((result.published_run_dir / "active" / "vectors" / "fh000.geojson").read_text())
    assert vector_payload["type"] == "FeatureCollection"
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Tornado Warning"
