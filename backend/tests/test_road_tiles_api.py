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
from app.services import roads_tiles

pytestmark = pytest.mark.anyio


def _write_roads_mbtiles(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    tile_bytes = gzip.compress(b"fake-road-mvt")

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
        ("name", "Test Roads"),
        ("id", "test-roads-v1"),
        ("minzoom", "5"),
        ("maxzoom", "14"),
        ("bounds", "-178,5,-25,82"),
        ("center", "-101.5,45.0,4"),
        (
            "vector_layers",
            json.dumps(
                [
                    {"id": "roads", "description": "Road lines"},
                ]
            ),
        ),
    ]
    cur.executemany("INSERT INTO metadata(name, value) VALUES(?, ?)", metadata_rows)
    cur.execute(
        "INSERT INTO tiles(zoom_level, tile_column, tile_row, tile_data) VALUES(?, ?, ?, ?)",
        (5, 0, 31, tile_bytes),
    )
    conn.commit()
    conn.close()
    return tile_bytes


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    roads_mbtiles_path = tmp_path / "data" / "roads" / "v1" / "cartosky_roads.mbtiles"
    _write_roads_mbtiles(roads_mbtiles_path)

    roads_tiles._reset_mbtiles_connections()
    monkeypatch.setattr(main_module, "ROADS_MBTILES", roads_mbtiles_path)
    monkeypatch.setattr(roads_tiles, "ROADS_MBTILES", roads_mbtiles_path)
    monkeypatch.setattr(roads_tiles, "TILES_PUBLIC_BASE_URL", "https://api.cartosky.com")

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    roads_tiles._reset_mbtiles_connections()


async def test_tiles_health_reports_road_tileset(client: httpx.AsyncClient) -> None:
    response = await client.get("/tiles/v3/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["roads_mbtiles_exists"] is True


async def test_roads_tilejson_served_from_main_api(client: httpx.AsyncClient) -> None:
    response = await client.get("/tiles/v3/roads/v1/tilejson.json")

    assert response.status_code == 200
    assert response.headers["cache-control"] == roads_tiles.ROAD_CACHE_MISS
    assert "roads_tilejson_total;dur=" in response.headers.get("server-timing", "")
    payload = response.json()
    assert payload["name"] == "Test Roads"
    assert payload["id"] == "test-roads-v1"
    assert payload["tiles"] == ["https://api.cartosky.com/tiles/v3/roads/v1/{z}/{x}/{y}.mvt"]
    assert [layer["id"] for layer in payload["vector_layers"]] == ["roads"]


async def test_roads_tilejson_falls_back_to_defaults_when_mbtiles_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_roads_mbtiles_path = tmp_path / "data" / "roads" / "v1" / "cartosky_roads.mbtiles"

    roads_tiles._reset_mbtiles_connections()
    monkeypatch.setattr(main_module, "ROADS_MBTILES", missing_roads_mbtiles_path)
    monkeypatch.setattr(roads_tiles, "ROADS_MBTILES", missing_roads_mbtiles_path)
    monkeypatch.setattr(roads_tiles, "TILES_PUBLIC_BASE_URL", "https://api.cartosky.com")

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/tiles/v3/roads/v1/tilejson.json")

    roads_tiles._reset_mbtiles_connections()

    assert response.status_code == 200
    assert response.headers["cache-control"] == roads_tiles.ROAD_CACHE_MISS
    payload = response.json()
    assert payload["name"] == roads_tiles.ROADS_TILESET_NAME
    assert payload["id"] == roads_tiles.ROADS_TILESET_ID
    assert payload["tiles"] == ["https://api.cartosky.com/tiles/v3/roads/v1/{z}/{x}/{y}.mvt"]


async def test_roads_tile_endpoint_preserves_gzip_and_expected_empty_behavior(client: httpx.AsyncClient) -> None:
    hit_response = await client.get("/tiles/v3/roads/v1/5/0/0.mvt")

    assert hit_response.status_code == 200
    assert hit_response.headers["cache-control"] == roads_tiles.ROAD_CACHE_HIT
    assert "roads_tile_total;dur=" in hit_response.headers.get("server-timing", "")
    assert hit_response.headers["content-encoding"] == "gzip"
    assert hit_response.content == b"fake-road-mvt"

    miss_response = await client.get("/tiles/v3/roads/v1/6/1/1.mvt")

    assert miss_response.status_code == 200
    assert miss_response.headers["cache-control"] == roads_tiles.ROAD_CACHE_MISS
    assert "roads_tile_total;dur=" in miss_response.headers.get("server-timing", "")
    assert miss_response.headers["content-encoding"] == "gzip"
    assert miss_response.headers["content-type"].startswith("application/vnd.mapbox-vector-tile")