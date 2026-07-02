import json
import math

from app.services.vector_simplify import (
    DEFAULT_COORD_PRECISION,
    simplify_geometry,
    simplify_vector_features,
)


def _dense_circle_polygon(*, center=(-95.0, 38.0), radius_deg=5.0, points=20000) -> dict:
    ring = []
    for index in range(points):
        angle = (index / points) * 2 * math.pi
        ring.append([
            center[0] + radius_deg * math.cos(angle) + 1e-13,
            center[1] + radius_deg * math.sin(angle) + 1e-13,
        ])
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _vertex_count(coordinates) -> int:
    if isinstance(coordinates[0], (int, float)):
        return 1
    return sum(_vertex_count(item) for item in coordinates)


def test_simplify_geometry_reduces_dense_polygon_dramatically():
    geometry = _dense_circle_polygon()
    simplified = simplify_geometry(geometry)

    original_vertices = _vertex_count(geometry["coordinates"])
    simplified_vertices = _vertex_count(simplified["coordinates"])
    assert simplified_vertices < original_vertices / 10

    original_bytes = len(json.dumps(geometry, separators=(",", ":")))
    simplified_bytes = len(json.dumps(simplified, separators=(",", ":")))
    assert simplified_bytes < original_bytes / 10


def test_simplify_geometry_rounds_coordinates():
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [-124.33301935060878, 43.360156639037136],
            [-123.0, 43.0],
            [-123.5, 44.0],
            [-124.33301935060878, 43.360156639037136],
        ]],
    }
    simplified = simplify_geometry(geometry)
    for lon, lat in simplified["coordinates"][0]:
        assert lon == round(lon, DEFAULT_COORD_PRECISION)
        assert lat == round(lat, DEFAULT_COORD_PRECISION)


def test_simplify_geometry_keeps_small_polygon_shape():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-100.0, 40.0], [-99.0, 40.0], [-99.0, 41.0], [-100.0, 40.0]]],
    }
    simplified = simplify_geometry(geometry)
    assert simplified["type"] == "Polygon"
    assert [list(pair) for pair in simplified["coordinates"][0]] == geometry["coordinates"][0]


def test_simplify_geometry_falls_back_on_invalid_geometry():
    geometry = {"type": "Polygon", "coordinates": "not-coordinates"}
    simplified = simplify_geometry(geometry)
    assert simplified == geometry


def test_simplify_vector_features_preserves_properties_and_passthrough():
    features = [
        {
            "type": "Feature",
            "geometry": _dense_circle_polygon(points=5000),
            "properties": {"category": "above", "probability": 40},
        },
        {"type": "Feature", "geometry": None, "properties": {"category": "near"}},
    ]
    simplified = simplify_vector_features(features)
    assert len(simplified) == 2
    assert simplified[0]["properties"] == {"category": "above", "probability": 40}
    assert _vertex_count(simplified[0]["geometry"]["coordinates"]) < 5001
    assert simplified[1] == features[1]
    # Original input is not mutated.
    assert _vertex_count(features[0]["geometry"]["coordinates"]) == 5001
