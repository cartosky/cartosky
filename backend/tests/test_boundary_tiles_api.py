from __future__ import annotations

import gzip
import json
import os
import sqlite3
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
from app.services import boundary_tiles

pytestmark = pytest.mark.anyio


def _write_boundaries_mbtiles(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    tile_bytes = gzip.compress(b"fake-mvt")

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE metadata (name text, value text)")
    cur.execute(
        """
        CREATE TABLE tiles (
            zoom_level integer,
            tile_column integer,
            tile_row integer,
            tile_data blob
        )
        """
    )
    metadata_rows = [
        ("name", "Test Boundaries"),
        ("id", "test-boundaries-v1"),
        ("minzoom", "0"),
        ("maxzoom", "5"),
        ("bounds", "-180,-85.0511,180,85.0511"),
        ("center", "-98.58,39.83,4"),
        (
            "vector_layers",
            json.dumps(
                [
                    {"id": "boundaries", "description": "Boundary lines"},
                    {"id": "hydro", "description": "Hydro layers"},
                ]
            ),
        ),
    ]
    cur.executemany("INSERT INTO metadata(name, value) VALUES(?, ?)", metadata_rows)
    cur.execute(
        "INSERT INTO tiles(zoom_level, tile_column, tile_row, tile_data) VALUES(?, ?, ?, ?)",
        (0, 0, 0, tile_bytes),
    )
    conn.commit()
    conn.close()
    return tile_bytes


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    mbtiles_path = tmp_path / "data" / "boundaries" / "v1" / "cartosky_boundaries.mbtiles"
    _write_boundaries_mbtiles(mbtiles_path)

    monkeypatch.setattr(main_module, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_module, "BOUNDARIES_MBTILES", mbtiles_path)
    monkeypatch.setattr(boundary_tiles, "BOUNDARIES_MBTILES", mbtiles_path)
    monkeypatch.setattr(boundary_tiles, "TILES_PUBLIC_BASE_URL", "https://api.cartosky.com")

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def test_tiles_health_reports_boundary_tileset(client: httpx.AsyncClient) -> None:
    response = await client.get("/tiles/v3/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["boundaries_mbtiles_exists"] is True


async def test_boundaries_tilejson_served_from_main_api(client: httpx.AsyncClient) -> None:
    response = await client.get("/tiles/v3/boundaries/v1/tilejson.json")

    assert response.status_code == 200
    assert response.headers["cache-control"] == boundary_tiles.BOUNDARY_CACHE_MISS
    payload = response.json()
    assert payload["name"] == "Test Boundaries"
    assert payload["id"] == "test-boundaries-v1"
    assert payload["tiles"] == ["https://api.cartosky.com/tiles/v3/boundaries/v1/{z}/{x}/{y}.mvt"]
    assert [layer["id"] for layer in payload["vector_layers"]] == ["boundaries", "hydro"]


async def test_boundaries_tile_endpoint_preserves_gzip_and_expected_empty_behavior(client: httpx.AsyncClient) -> None:
    hit_response = await client.get("/tiles/v3/boundaries/v1/0/0/0.mvt")

    assert hit_response.status_code == 200
    assert hit_response.headers["cache-control"] == boundary_tiles.BOUNDARY_CACHE_HIT
    assert hit_response.headers["content-encoding"] == "gzip"
    assert hit_response.content == b"fake-mvt"

    miss_response = await client.get("/tiles/v3/boundaries/v1/1/1/1.mvt")

    assert miss_response.status_code == 200
    assert miss_response.headers["cache-control"] == boundary_tiles.BOUNDARY_CACHE_MISS
    assert miss_response.headers["content-encoding"] == "gzip"
    assert miss_response.headers["content-type"].startswith("application/vnd.mapbox-vector-tile")


async def test_weather_png_tile_endpoint_is_retired(client: httpx.AsyncClient) -> None:
    response = await client.get("/tiles/v3/gfs/20260401_12z/tmp2m/0/4/1/5.png")
    assert response.status_code == 404
