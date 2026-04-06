from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import nws_hazards_poller


def _write_county_reference(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"GEOID": "04013", "NAME": "Maricopa", "STUSPS": "AZ"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-112.8, 32.9], [-111.2, 32.9], [-111.2, 34.0], [-112.8, 34.0], [-112.8, 32.9]]],
                        },
                    }
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


def test_nws_hazards_poller_noops_when_fingerprint_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path
    county_reference = _write_county_reference(tmp_path / "hazards" / "county_reference.geojson")
    zone_reference = _write_zone_reference(tmp_path / "hazards" / "zone_reference.geojson")
    run_id = "20260406_1730z"
    manifest_dir = data_root / "manifests" / "nws_hazards"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (data_root / "published" / "nws_hazards").mkdir(parents=True, exist_ok=True)
    (data_root / "published" / "nws_hazards" / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

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
    fingerprint = nws_hazards_poller._build_alert_fingerprint(payload)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "model": "nws_hazards",
                "run": run_id,
                "metadata": {"source_fingerprint": fingerprint, "zone_reference_signature": "zonesig"},
                "variables": {"active": {"frames": [{"fh": 0, "valid_time": "2026-04-06T17:30:00Z"}]}},
            }
        )
    )

    monkeypatch.setattr(nws_hazards_poller, "fetch_active_alerts_geojson", lambda **_: payload)
    monkeypatch.setattr(
        nws_hazards_poller,
        "sync_active_zone_reference",
        lambda **_: type("Sync", (), {"signature": "zonesig"})(),
    )

    config = nws_hazards_poller.NWSHazardsPollerConfig(
        data_root=data_root,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
        poll_seconds=90,
        keep_runs=5,
        timeout_seconds=10.0,
        api_base="https://api.weather.gov",
    )
    result = nws_hazards_poller.run_once(config)
    assert result.action == "noop"
    assert result.published_run_id == run_id


def test_nws_hazards_poller_reuses_prefetched_payload_for_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path
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

    monkeypatch.setattr(nws_hazards_poller, "fetch_active_alerts_geojson", lambda **_: payload)
    monkeypatch.setattr(
        nws_hazards_poller,
        "sync_active_zone_reference",
        lambda **_: type("Sync", (), {"signature": "zonesig"})(),
    )
    observed: dict[str, object] = {}

    def _fake_publish_active_hazards(**kwargs):
        observed.update(kwargs)
        return type("Result", (), {
            "run_id": "20260406_1730z",
            "fingerprint": "fp123",
        })()

    monkeypatch.setattr(nws_hazards_poller, "publish_active_hazards", _fake_publish_active_hazards)

    config = nws_hazards_poller.NWSHazardsPollerConfig(
        data_root=data_root,
        county_reference_path=county_reference,
        zone_reference_path=zone_reference,
        poll_seconds=90,
        keep_runs=5,
        timeout_seconds=10.0,
        api_base="https://api.weather.gov",
    )
    result = nws_hazards_poller.run_once(config)
    assert result.action == "published"
    assert observed["payload"] == payload
    assert observed["zone_reference_signature"] == "zonesig"