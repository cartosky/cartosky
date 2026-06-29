from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from fastapi import Response

logger = logging.getLogger(__name__)


def _env_value(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw != "":
            return raw
    return default


DATA_ROOT = Path(_env_value("CARTOSKY_DATA_ROOT", "CARTOSKY_V3_DATA_ROOT", "TWF_V3_DATA_ROOT", default="./data"))
BOUNDARIES_MBTILES = Path(
    _env_value(
        "CARTOSKY_BOUNDARIES_MBTILES",
        "CARTOSKY_V3_BOUNDARIES_MBTILES",
        "TWF_V3_BOUNDARIES_MBTILES",
        default=str(DATA_ROOT / "boundaries" / "v1" / "twf_boundaries.mbtiles"),
    )
)
BOUNDARIES_TILESET_ID = _env_value(
    "CARTOSKY_BOUNDARIES_TILESET_ID",
    "CARTOSKY_V3_BOUNDARIES_TILESET_ID",
    "TWF_V3_BOUNDARIES_TILESET_ID",
    default="cartosky-boundaries-v1",
)
BOUNDARIES_TILESET_NAME = _env_value(
    "CARTOSKY_BOUNDARIES_TILESET_NAME",
    "CARTOSKY_V3_BOUNDARIES_TILESET_NAME",
    "TWF_V3_BOUNDARIES_TILESET_NAME",
    default="CartoSky Boundaries v2",
)
BOUNDARIES_TILESET_PATH_VERSION = (
    _env_value(
        "CARTOSKY_BOUNDARIES_TILESET_VERSION",
        "CARTOSKY_V3_BOUNDARIES_TILESET_VERSION",
        "TWF_V3_BOUNDARIES_TILESET_VERSION",
        default="v2",
    ).strip().strip("/")
    or "v2"
)
TILES_PUBLIC_BASE_URL = _env_value(
    "CARTOSKY_TILES_PUBLIC_BASE_URL",
    "CARTOSKY_V3_TILES_PUBLIC_BASE_URL",
    "TWF_V3_TILES_PUBLIC_BASE_URL",
    default="https://api.cartosky.com",
).rstrip("/")

BOUNDARY_CACHE_HIT = "public, max-age=31536000, immutable"
BOUNDARY_CACHE_MISS = "public, max-age=15"
EMPTY_GZIP_MVT_TILE = base64.b64decode("H4sIAHR2n2kC/wMAAAAAAAAAAAA=")
_MBTILES_LOCAL = threading.local()


def _mbtiles_connection_map() -> dict[str, sqlite3.Connection]:
    mapping = getattr(_MBTILES_LOCAL, "connections", None)
    if isinstance(mapping, dict):
        return mapping
    mapping = {}
    _MBTILES_LOCAL.connections = mapping
    return mapping


def _get_mbtiles_connection(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None

    key = str(path.resolve())
    connections = _mbtiles_connection_map()
    conn = connections.get(key)
    if conn is not None:
        return conn

    conn = sqlite3.connect(key, timeout=5.0)
    conn.execute("PRAGMA query_only = 1")
    connections[key] = conn
    return conn


def read_mbtiles_metadata(path: Path) -> dict[str, str]:
    try:
        conn = _get_mbtiles_connection(path)
        if conn is None:
            return {}
        cur = conn.cursor()
        cur.execute("SELECT name, value FROM metadata")
        rows = cur.fetchall()
        return {str(name): str(value) for name, value in rows}
    except Exception:
        logger.exception("Failed reading MBTiles metadata: %s", path)
        return {}


def lookup_mbtiles_tile(path: Path, *, z: int, x: int, y: int) -> bytes | None:
    if z < 0 or x < 0 or y < 0:
        return None

    max_coord = (1 << z) - 1
    if x > max_coord or y > max_coord:
        return None

    tms_y = max_coord - y

    try:
        conn = _get_mbtiles_connection(path)
        if conn is None:
            return None
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tile_data
            FROM tiles
            WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?
            """,
            (z, x, tms_y),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return row[0]
    except Exception:
        logger.exception("Failed reading MBTiles tile z/x/y=%s/%s/%s from %s", z, x, y, path)
        return None


def _reset_mbtiles_connections() -> None:
    connections = getattr(_MBTILES_LOCAL, "connections", None)
    if not isinstance(connections, dict):
        return
    for conn in connections.values():
        try:
            conn.close()
        except Exception:
            pass
    connections.clear()


def mbtiles_min_max_zoom(metadata: dict[str, str]) -> tuple[int, int]:
    try:
        minzoom = int(metadata.get("minzoom", "0"))
    except Exception:
        minzoom = 0
    try:
        maxzoom = int(metadata.get("maxzoom", "10"))
    except Exception:
        maxzoom = 10
    return minzoom, maxzoom


def build_boundaries_tilejson() -> dict[str, object]:
    metadata = read_mbtiles_metadata(BOUNDARIES_MBTILES)
    minzoom, maxzoom = mbtiles_min_max_zoom(metadata)
    bounds_raw = metadata.get("bounds", "-180,-85.0511,180,85.0511")
    center_raw = metadata.get("center", "-98.58,39.83,4")

    try:
        bounds = [float(v) for v in bounds_raw.split(",")[:4]]
        if len(bounds) != 4:
            raise ValueError("invalid bounds length")
    except Exception:
        bounds = [-180.0, -85.0511, 180.0, 85.0511]

    try:
        center_vals = [float(v) for v in center_raw.split(",")[:3]]
        if len(center_vals) != 3:
            raise ValueError("invalid center length")
    except Exception:
        center_vals = [-98.58, 39.83, 4.0]

    tilejson: dict[str, object] = {
        "tilejson": "2.2.0",
        "name": metadata.get("name", BOUNDARIES_TILESET_NAME),
        "id": metadata.get("id", BOUNDARIES_TILESET_ID),
        "scheme": "xyz",
        "format": "pbf",
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "bounds": bounds,
        "center": center_vals,
        "tiles": [f"{TILES_PUBLIC_BASE_URL}/tiles/v3/boundaries/{BOUNDARIES_TILESET_PATH_VERSION}/{{z}}/{{x}}/{{y}}.mvt"],
    }

    if "vector_layers" in metadata:
        try:
            tilejson["vector_layers"] = json.loads(metadata["vector_layers"])
        except Exception:
            logger.warning("Invalid vector_layers metadata in %s", BOUNDARIES_MBTILES)

    if "attribution" in metadata:
        tilejson["attribution"] = metadata["attribution"]

    if "description" in metadata:
        tilejson["description"] = metadata["description"]

    return tilejson


def empty_mvt_response(*, cache_control: str) -> Response:
    return Response(
        content=EMPTY_GZIP_MVT_TILE,
        media_type="application/vnd.mapbox-vector-tile",
        headers={
            "Cache-Control": cache_control,
            "Content-Encoding": "gzip",
        },
    )
