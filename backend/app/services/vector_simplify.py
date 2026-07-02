"""Publish-time geometry reduction for vector (GeoJSON) outlook products.

NOAA MapServer/shapefile sources ship national-scale outlook polygons with
float64-precision coordinates and dense vertex chains (measured July 2026: a
single CPC outlook FeatureCollection was 32.4 MB raw / 5.5 MB gzipped, 309k
vertices across 24 features). Simplifying to ~1 km tolerance and rounding to
4 decimal places (~11 m) cuts the payload ~95% with no visible change at the
scales these products are drawn for.
"""

from __future__ import annotations

import logging
from typing import Any

from shapely.geometry import mapping, shape

logger = logging.getLogger(__name__)

# ~1 km in degrees at mid-latitudes; invisible for national outlook polygons.
DEFAULT_SIMPLIFY_TOLERANCE_DEG = 0.01
# 4 decimal places ~= 11 m of positional precision.
DEFAULT_COORD_PRECISION = 4


def _round_coords(value: Any, precision: int) -> Any:
    if isinstance(value, (list, tuple)):
        return [_round_coords(item, precision) for item in value]
    if isinstance(value, float):
        return round(value, precision)
    return value


def simplify_geometry(
    geometry: dict,
    *,
    tolerance_deg: float = DEFAULT_SIMPLIFY_TOLERANCE_DEG,
    precision: int = DEFAULT_COORD_PRECISION,
) -> dict:
    """Simplify and coordinate-round one GeoJSON geometry dict.

    Falls back to the original geometry (with rounding only) if shapely cannot
    process it or simplification would empty it — publishing must never fail
    because of a single odd geometry.
    """
    candidate: dict = geometry
    try:
        simplified = shape(geometry).simplify(tolerance_deg, preserve_topology=True)
        if not simplified.is_empty:
            candidate = dict(mapping(simplified))
    except Exception as exc:
        logger.warning("Vector simplify failed; keeping original geometry: %s", exc)
    result = dict(candidate)
    coordinates = result.get("coordinates")
    if coordinates is not None:
        result["coordinates"] = _round_coords(coordinates, precision)
    return result


def simplify_vector_features(
    features: list[dict],
    *,
    tolerance_deg: float = DEFAULT_SIMPLIFY_TOLERANCE_DEG,
    precision: int = DEFAULT_COORD_PRECISION,
) -> list[dict]:
    """Return features with simplified, precision-rounded geometries.

    Features without a usable geometry dict pass through unchanged.
    """
    simplified_features: list[dict] = []
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        if not isinstance(geometry, dict):
            simplified_features.append(feature)
            continue
        simplified_features.append(
            {
                **feature,
                "geometry": simplify_geometry(geometry, tolerance_deg=tolerance_deg, precision=precision),
            }
        )
    return simplified_features
