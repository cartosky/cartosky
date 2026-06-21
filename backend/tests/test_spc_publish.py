from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

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


def test_normalize_extended_probability_geojson_uses_dn_styles_and_idp_filedate() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "dn": 30,
                    "idp_filedate": 1779390916000,
                    "idp_source": "day5otlk_20260621_prob",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "dn": 15,
                    "idp_filedate": 1779390916000,
                    "idp_source": "day5otlk_20260621_prob",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-100.0, 34.0], [-99.0, 34.0], [-99.0, 35.0], [-100.0, 34.0]]],
                },
            },
        ],
    }

    frame = spc_publish._normalize_probability_geojson(
        payload,
        product=spc_publish.SPC_EXTENDED_PRODUCT,
        day_label="Day 4",
        fh=0,
    )

    expected_issue_time = datetime(2026, 5, 21, 19, 15, 16, tzinfo=timezone.utc)
    expected_valid_time = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    assert frame.issue_time == expected_issue_time
    assert frame.valid_time == expected_valid_time
    assert [feature["properties"]["risk_label"] for feature in frame.features] == ["15%", "30%"]
    assert [feature["properties"]["fill"] for feature in frame.features] == ["#FFEB7F", "#FF9600"]
    assert [feature["properties"]["stroke"] for feature in frame.features] == ["#FF9600", "#FF4500"]
    assert [feature["properties"]["hover_label"] for feature in frame.features] == [
        "15% Any Severe Probability",
        "30% Any Severe Probability",
    ]


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
    assert result.variable_ids == ["convective"]
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
    assert sidecar["legend_entries"][0]["label"] == "Slight"
    assert sidecar["vector_layers"]["primary"]["path"] == "vectors/fh000.geojson"
    assert sidecar["day_label"] == "Day 1"
    assert sidecar["issue_time"] == "2026-04-01T06:30:00Z"

    vector_payload = json.loads((result.published_run_dir / "convective" / "vectors" / "fh000.geojson").read_text())
    assert vector_payload["type"] == "FeatureCollection"
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Slight"


def test_collect_latest_spc_products_includes_probability_products_and_skips_missing_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = {
        1: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "DN": 2,
                        "label": "MRGL",
                        "label2": "Marginal Risk",
                        "VALID": "2026-04-01T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]]},
                }
            ],
        },
        9: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "DN": 3,
                        "label": "SLGT",
                        "label2": "Slight Risk",
                        "VALID": "2026-04-02T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-96.0, 34.0], [-95.0, 34.0], [-95.0, 35.0], [-96.0, 34.0]]]},
                }
            ],
        },
        17: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "DN": 2,
                        "label": "MRGL",
                        "label2": "Marginal Risk",
                        "VALID": "2026-04-03T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-94.0, 33.0], [-93.0, 33.0], [-93.0, 34.0], [-94.0, 33.0]]]},
                }
            ],
        },
        3: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "label": "0.05",
                        "label2": "5% Tornado Risk",
                        "fill": "#BD998A",
                        "stroke": "#7F3F27",
                        "VALID": "2026-04-01T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-90.0, 32.0], [-89.0, 32.0], [-89.0, 33.0], [-90.0, 32.0]]]},
                },
                {
                    "type": "Feature",
                    "properties": {
                        "label": "CIG1",
                        "label2": "Tornado Conditional Intensity Group 1 Risk",
                        "fill": "#888888",
                        "stroke": "#000000",
                        "VALID": "2026-04-01T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-89.5, 32.1], [-89.2, 32.1], [-89.2, 32.4], [-89.5, 32.1]]]},
                },
            ],
        },
        11: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "label": "0.02",
                        "label2": "2% Tornado Risk",
                        "fill": "#79BA7A",
                        "stroke": "#1A731D",
                        "VALID": "2026-04-02T12:00:00Z",
                        "ISSUE": "2026-04-01T07:45:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-88.0, 31.0], [-87.0, 31.0], [-87.0, 32.0], [-88.0, 31.0]]]},
                }
            ],
        },
        7: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "label": "0.15",
                        "label2": "15% Wind Risk",
                        "fill": "#FFEB7F",
                        "stroke": "#FF9600",
                        "VALID": "2026-04-01T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-86.0, 30.0], [-85.0, 30.0], [-85.0, 31.0], [-86.0, 30.0]]]},
                }
            ],
        },
        15: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "label": "0.05",
                        "label2": "5% Wind Risk",
                        "fill": "#C5A392",
                        "stroke": "#8B4726",
                        "VALID": "2026-04-02T12:00:00Z",
                        "ISSUE": "2026-04-01T07:45:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-84.0, 29.0], [-83.0, 29.0], [-83.0, 30.0], [-84.0, 29.0]]]},
                }
            ],
        },
        5: {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "label": "0.15",
                        "label2": "15% Hail Risk",
                        "fill": "#FFEB7F",
                        "stroke": "#FF9600",
                        "VALID": "2026-04-01T12:00:00Z",
                        "ISSUE": "2026-04-01T06:30:00Z",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [[[-82.0, 28.0], [-81.0, 28.0], [-81.0, 29.0], [-82.0, 28.0]]]},
                }
            ],
        },
        13: {"type": "FeatureCollection", "features": []},
        21: {"type": "FeatureCollection", "features": []},
        22: {"type": "FeatureCollection", "features": []},
        23: {"type": "FeatureCollection", "features": []},
        24: {"type": "FeatureCollection", "features": []},
        25: {"type": "FeatureCollection", "features": []},
    }

    monkeypatch.setattr(spc_publish, "fetch_spc_layer_geojson", lambda layer_id, **_: payloads[layer_id])

    products, issue_time = spc_publish.collect_latest_spc_products()

    assert set(products.keys()) == {"convective", "tornado_prob", "wind_prob", "hail_prob"}
    assert [frame.fh for frame in products["hail_prob"]] == [0]
    assert products["tornado_prob"][0].features[0]["properties"]["hover_label"] == "5% Tornado Risk"
    assert products["tornado_prob"][0].features[1]["properties"]["hover_label"] == "Tornado Conditional Intensity Group 1 Risk"
    assert issue_time == datetime(2026, 4, 1, 6, 30, tzinfo=timezone.utc)


def test_publish_latest_spc_outlooks_writes_multiple_probability_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = {
        1: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"DN": 2, "label": "MRGL", "label2": "Marginal Risk", "VALID": "2026-04-01T12:00:00Z", "ISSUE": "2026-04-01T06:30:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]]}}]},
        9: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"DN": 3, "label": "SLGT", "label2": "Slight Risk", "VALID": "2026-04-02T12:00:00Z", "ISSUE": "2026-04-01T06:30:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-96.0, 34.0], [-95.0, 34.0], [-95.0, 35.0], [-96.0, 34.0]]]}}]},
        17: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"DN": 2, "label": "MRGL", "label2": "Marginal Risk", "VALID": "2026-04-03T12:00:00Z", "ISSUE": "2026-04-01T06:30:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-94.0, 33.0], [-93.0, 33.0], [-93.0, 34.0], [-94.0, 33.0]]]}}]},
        3: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"label": "0.05", "label2": "5% Tornado Risk", "fill": "#BD998A", "stroke": "#7F3F27", "VALID": "2026-04-01T12:00:00Z", "ISSUE": "2026-04-01T06:30:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-90.0, 32.0], [-89.0, 32.0], [-89.0, 33.0], [-90.0, 32.0]]]}}]},
        11: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"label": "0.02", "label2": "2% Tornado Risk", "fill": "#79BA7A", "stroke": "#1A731D", "VALID": "2026-04-02T12:00:00Z", "ISSUE": "2026-04-01T07:45:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-88.0, 31.0], [-87.0, 31.0], [-87.0, 32.0], [-88.0, 31.0]]]}}]},
        7: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"label": "0.15", "label2": "15% Wind Risk", "fill": "#FFEB7F", "stroke": "#FF9600", "VALID": "2026-04-01T12:00:00Z", "ISSUE": "2026-04-01T06:30:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-86.0, 30.0], [-85.0, 30.0], [-85.0, 31.0], [-86.0, 30.0]]]}}]},
        15: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"label": "0.05", "label2": "5% Wind Risk", "fill": "#C5A392", "stroke": "#8B4726", "VALID": "2026-04-02T12:00:00Z", "ISSUE": "2026-04-01T07:45:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-84.0, 29.0], [-83.0, 29.0], [-83.0, 30.0], [-84.0, 29.0]]]}}]},
        5: {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"label": "0.15", "label2": "15% Hail Risk", "fill": "#FFEB7F", "stroke": "#FF9600", "VALID": "2026-04-01T12:00:00Z", "ISSUE": "2026-04-01T06:30:00Z"}, "geometry": {"type": "Polygon", "coordinates": [[[-82.0, 28.0], [-81.0, 28.0], [-81.0, 29.0], [-82.0, 28.0]]]}}]},
        13: {"type": "FeatureCollection", "features": []},
        21: {"type": "FeatureCollection", "features": []},
        22: {"type": "FeatureCollection", "features": []},
        23: {"type": "FeatureCollection", "features": []},
        24: {"type": "FeatureCollection", "features": []},
        25: {"type": "FeatureCollection", "features": []},
    }

    monkeypatch.setattr(spc_publish, "fetch_spc_layer_geojson", lambda layer_id, **_: payloads[layer_id])

    result = spc_publish.publish_latest_spc_outlooks(data_root=tmp_path)

    assert result.variable_ids == ["convective", "hail_prob", "tornado_prob", "wind_prob"]
    manifest = json.loads(result.manifest_path.read_text())
    assert set(manifest["variables"].keys()) == {"convective", "tornado_prob", "wind_prob", "hail_prob"}
    assert manifest["variables"]["hail_prob"]["frames"] == [{"fh": 0, "valid_time": "2026-04-01T12:00:00Z"}]

    tornado_sidecar = json.loads((result.published_run_dir / "tornado_prob" / "fh000.json").read_text())
    assert tornado_sidecar["legend_title"] == "Tornado Probability"
    assert tornado_sidecar["legend_entries"][0]["label"] == "5%"

    wind_payload = json.loads((result.published_run_dir / "wind_prob" / "vectors" / "fh001.geojson").read_text())
    assert wind_payload["features"][0]["properties"]["hover_label"] == "5% Wind Risk"