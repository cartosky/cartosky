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
    model = "spc"
    run_id = "20260401_0630z"
    variable = "convective"
    tornado_variable = "tornado_prob"

    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "model": model,
                "run": run_id,
                "metadata": {
                    "time_axis_mode": "valid",
                },
                "variables": {
                    variable: {
                        "expected_frames": 3,
                        "available_frames": 3,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-04-01T12:00:00Z"},
                            {"fh": 1, "valid_time": "2026-04-02T12:00:00Z"},
                            {"fh": 2, "valid_time": "2026-04-03T12:00:00Z"},
                        ],
                    },
                    tornado_variable: {
                        "expected_frames": 2,
                        "available_frames": 2,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-04-01T12:00:00Z"},
                            {"fh": 1, "valid_time": "2026-04-02T12:00:00Z"},
                        ],
                    }
                },
            }
        )
    )

    model_root = published_root / model
    model_root.mkdir(parents=True, exist_ok=True)
    (model_root / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    var_dir = model_root / run_id / variable
    (var_dir / "vectors").mkdir(parents=True, exist_ok=True)
    tornado_var_dir = model_root / run_id / tornado_variable
    (tornado_var_dir / "vectors").mkdir(parents=True, exist_ok=True)

    for fh, valid_time, day_label in (
        (0, "2026-04-01T12:00:00Z", "Day 1"),
        (1, "2026-04-02T12:00:00Z", "Day 2"),
        (2, "2026-04-03T12:00:00Z", "Day 3"),
    ):
        (var_dir / f"fh{fh:03d}.json").write_text(
            json.dumps(
                {
                    "kind": "categorical",
                    "valid_time": valid_time,
                    "day_label": day_label,
                    "legend_entries": [{"value": 2, "color": "#008000", "label": "Marginal"}],
                    "vector_layers": {
                        "primary": {
                            "format": "geojson",
                            "path": f"vectors/fh{fh:03d}.geojson",
                            "style_key": "spc_convective",
                        }
                    },
                }
            )
        )

    for fh, valid_time, day_label, label in (
        (0, "2026-04-01T12:00:00Z", "Day 1", "5%"),
        (1, "2026-04-02T12:00:00Z", "Day 2", "2%"),
    ):
        (tornado_var_dir / f"fh{fh:03d}.json").write_text(
            json.dumps(
                {
                    "kind": "categorical",
                    "valid_time": valid_time,
                    "day_label": day_label,
                    "legend_title": "Tornado Probability",
                    "legend_entries": [{"value": 5, "color": "#BD998A", "label": label}],
                    "vector_layers": {
                        "primary": {
                            "format": "geojson",
                            "path": f"vectors/fh{fh:03d}.geojson",
                            "style_key": "spc_tornado_probability",
                        }
                    },
                }
            )
        )
        (tornado_var_dir / "vectors" / f"fh{fh:03d}.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "risk_code": 5,
                                "risk_label": label,
                                "hover_label": f"{label} Tornado Probability",
                                "fill": "#BD998A",
                                "fill_opacity": 0.65,
                                "stroke": "#7F3F27",
                                "stroke_width": 1.25,
                                "sort_rank": 5,
                            },
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[-92.0, 35.0], [-91.0, 35.0], [-91.0, 36.0], [-92.0, 35.0]]],
                            },
                        }
                    ],
                }
            )
        )
        (var_dir / "vectors" / f"fh{fh:03d}.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "risk_code": 2,
                                "risk_label": "Marginal",
                                "fill": "#008000",
                                "fill_opacity": 0.65,
                                "stroke": "#000000",
                                "stroke_width": 1.25,
                                "sort_rank": 2,
                            },
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]],
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


async def test_spc_latest_manifest_frames_and_vector_endpoint_resolve(client: httpx.AsyncClient) -> None:
    capabilities_response = await client.get("/api/v4/capabilities")
    assert capabilities_response.status_code == 200
    capabilities_payload = capabilities_response.json()

    spc = capabilities_payload["model_catalog"]["spc"]
    assert spc["constraints"]["time_axis_mode"] == "valid"
    assert spc["constraints"]["latest_only"] is True
    assert spc["defaults"]["default_render_substrate"] == "vector"
    assert spc["variables"]["convective"]["render_substrates"] == ["vector"]
    assert spc["variables"]["tornado_prob"]["render_substrates"] == ["vector"]
    assert spc["variables"]["wind_prob"]["render_substrates"] == ["vector"]
    assert spc["variables"]["hail_prob"]["render_substrates"] == ["vector"]

    manifest_response = await client.get("/api/v4/spc/latest/manifest")
    assert manifest_response.status_code == 200
    manifest_payload = manifest_response.json()
    assert manifest_payload["run"] == "20260401_0630z"
    assert set(manifest_payload["variables"].keys()) == {"convective", "tornado_prob"}

    frames_response = await client.get("/api/v4/spc/latest/convective/frames")
    assert frames_response.status_code == 200
    frames = frames_response.json()
    assert [frame["fh"] for frame in frames] == [0, 1, 2]
    assert frames[0]["run"] == "20260401_0630z"
    assert frames[0]["meta"]["meta"]["valid_time"] == "2026-04-01T12:00:00Z"
    assert frames[0]["meta"]["meta"]["day_label"] == "Day 1"
    assert frames[0]["meta"]["meta"]["vector_layers"]["primary"]["path"] == "vectors/fh000.geojson"

    vector_response = await client.get("/api/v4/spc/latest/convective/0/vectors/primary")
    assert vector_response.status_code == 200
    vector_payload = vector_response.json()
    assert vector_payload["type"] == "FeatureCollection"
    assert vector_payload["features"][0]["properties"]["risk_label"] == "Marginal"

    tornado_frames_response = await client.get("/api/v4/spc/latest/tornado_prob/frames")
    assert tornado_frames_response.status_code == 200
    tornado_frames = tornado_frames_response.json()
    assert [frame["fh"] for frame in tornado_frames] == [0, 1]
    assert tornado_frames[0]["meta"]["meta"]["legend_title"] == "Tornado Probability"

    tornado_vector_response = await client.get("/api/v4/spc/latest/tornado_prob/0/vectors/primary")
    assert tornado_vector_response.status_code == 200
    tornado_vector_payload = tornado_vector_response.json()
    assert tornado_vector_payload["features"][0]["properties"]["hover_label"] == "5% Tornado Probability"