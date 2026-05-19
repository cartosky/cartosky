from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app.services import nws_hazards as nws_hazards_service

pytestmark = pytest.mark.anyio


def _reset_main_caches() -> None:
    with main_module._ds_cache_lock:
        for ds in main_module._ds_cache.values():
            try:
                ds.close()
            except Exception:
                pass
        main_module._ds_cache.clear()

    with main_module._sample_lock:
        main_module._sample_cache.clear()
        main_module._sample_inflight.clear()
        main_module._sample_rate_window.clear()

    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()
    main_module._sample_transformer.cache_clear()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    model = "nws_hazards"
    run_id = "20260406_1730z"
    variable = "active"

    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "model": model,
                "run": run_id,
                "metadata": {
                    "time_axis_mode": "valid",
                    "source_fingerprint": "abc123",
                },
                "variables": {
                    variable: {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-04-06T17:30:00Z"},
                        ],
                    },
                },
            }
        )
    )

    model_root = published_root / model
    model_root.mkdir(parents=True, exist_ok=True)
    (model_root / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    var_dir = model_root / run_id / variable
    (var_dir / "vectors").mkdir(parents=True, exist_ok=True)
    (var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "kind": "categorical",
                "valid_time": "2026-04-06T17:30:00Z",
                "legend_title": "NWS Hazards",
                "display_name": "Active Hazards",
                "legend_entries": [{"value": 390, "color": "#dc2626", "label": "Tornado Warning"}],
                "vector_layers": {
                    "primary": {
                        "format": "geojson",
                        "path": "vectors/fh000.geojson",
                        "style_key": "nws_hazards_active",
                    }
                },
            }
        )
    )
    (var_dir / "vectors" / "fh000.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "risk_code": "tornado_warning",
                            "risk_label": "Tornado Warning",
                            "hover_label": "Maricopa: Tornado Warning",
                            "fill": "#dc2626",
                            "fill_opacity": 0.58,
                            "stroke": "#7f1d1d",
                            "stroke_width": 1.0,
                            "sort_rank": 390,
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-112.8, 32.9], [-111.2, 32.9], [-111.2, 34.0], [-112.8, 34.0], [-112.8, 32.9]]],
                        },
                    }
                ],
            }
        )
    )

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)

    _reset_main_caches()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


async def test_nws_hazards_latest_manifest_frames_and_vector_endpoint_resolve(client: httpx.AsyncClient) -> None:
    capabilities_response = await client.get("/api/v4/capabilities")
    assert capabilities_response.status_code == 200
    capabilities_payload = capabilities_response.json()

    hazards = capabilities_payload["model_catalog"]["nws_hazards"]
    assert hazards["constraints"]["time_axis_mode"] == "valid"
    assert hazards["constraints"]["latest_only"] is True
    assert hazards["defaults"]["default_render_substrate"] == "vector"
    assert hazards["variables"]["active"]["render_substrates"] == ["vector"]

    manifest_response = await client.get("/api/v4/nws_hazards/latest/manifest")
    assert manifest_response.status_code == 200
    manifest_payload = manifest_response.json()
    assert manifest_payload["run"] == "20260406_1730z"

    frames_response = await client.get("/api/v4/nws_hazards/latest/active/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0]
    assert frames[0]["meta"]["meta"]["legend_title"] == "NWS Hazards"

    vector_response = await client.get("/api/v4/nws_hazards/latest/active/0/vectors/primary")
    assert vector_response.status_code == 200
    assert vector_response.headers["content-type"].startswith("application/geo+json")
    vector_payload = vector_response.json()
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Tornado Warning"


async def test_nws_hazards_alert_detail_endpoint_returns_normalized_nws_alert(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch_alert_geojson(alert_id: str) -> dict:
        assert alert_id == "urn:oid:alert-1"
        return {
            "id": alert_id,
            "properties": {
                "id": alert_id,
                "event": "Frost Advisory",
                "headline": "Frost Advisory issued May 19",
                "severity": "Minor",
                "urgency": "Expected",
                "certainty": "Likely",
                "effective": "2026-05-19T03:00:00Z",
                "expires": "2026-05-19T13:00:00Z",
                "areaDesc": "Minnehaha County",
                "description": "Temperatures as low as 33 will result in frost formation.",
                "instruction": "Take steps now to protect tender plants from the cold.",
            },
        }

    monkeypatch.setattr(nws_hazards_service, "fetch_alert_geojson", fake_fetch_alert_geojson)

    response = await client.get("/api/v4/nws-hazards/alert", params={"id": "urn:oid:alert-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["event"] == "Frost Advisory"
    assert payload["headline"] == "Frost Advisory issued May 19"
    assert payload["areas"] == ["Minnehaha County"]
    assert payload["description"] == "Temperatures as low as 33 will result in frost formation."
    assert payload["instruction"] == "Take steps now to protect tender plants from the cold."
