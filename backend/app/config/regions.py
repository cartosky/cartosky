from __future__ import annotations

REGION_PRESETS: dict[str, dict] = {
    "conus": {
        "label": "CONUS",
        "bbox": [-134.0, 24.0, -60.0, 55.0],
        "defaultCenter": [-96.25, 39.5],
        "defaultZoom": 4,
        "minZoom": 2,
        "maxZoom": 14,
    },
    "na": {
        "label": "North America",
        "bbox": [-178.0, 5.0, -25.0, 82.0],
        "defaultCenter": [-101.5, 45.0],
        "defaultZoom": 0.5,
        # 0, not -1: the permalink serializers drop z < 0, silently losing the
        # camera from shared links (same rule as the world preset).
        "minZoom": 0,
        "maxZoom": 14,
    },
    "pnw": {
        "label": "Northwest",
        "bbox": [-126.0, 41.5, -116.0, 49.5],
        "defaultCenter": [-120.8, 45.6],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    "north_central": {
        "label": "N. Great Plains",
        "bbox": [-112.0, 42.0, -94.0, 49.5],
        "defaultCenter": [-103.0, 45.75],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    "midwest": {
        "label": "Midwest",
        "bbox": [-104.0, 36.5, -80.0, 49.5],
        "defaultCenter": [-92.0, 43.0],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    "northeast": {
        "label": "Northeast",
        "bbox": [-82.5, 37.0, -66.0, 47.8],
        "defaultCenter": [-74.25, 42.4],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    "southeast": {
        "label": "Southeast",
        "bbox": [-95.0, 24.0, -75.0, 36.8],
        "defaultCenter": [-85.0, 30.4],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    "south_great_plains": {
        "label": "S. Great Plains",
        "bbox": [-106.5, 29.0, -93.0, 41.5],
        "defaultCenter": [-99.75, 35.25],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    "southwest": {
        "label": "Southwest",
        "bbox": [-125.0, 30.5, -102.0, 42.5],
        "defaultCenter": [-113.5, 36.5],
        "defaultZoom": 5,
        "minZoom": 3,
        "maxZoom": 14,
    },
    # Full-extent camera for global data domains (global go-live design §4).
    # It is a CAMERA preset like every other entry here — never a data domain.
    # Its bbox is deliberately the whole mercator-displayable world, which is
    # what keeps it out of the canonical (conus/na) coverage-filtered lists and
    # makes it appear exactly when a non-canonical domain is active.
    "world": {
        "label": "World",
        "bbox": [-180.0, -85.0, 180.0, 85.0],
        "defaultCenter": [-30.0, 25.0],
        "defaultZoom": 1.3,
        # minZoom is 0, not -1: the permalink serializers drop `z` below 0
        # (frontend permalink.ts / permalink-read.ts gate on z >= 0), so a
        # negative-zoom World camera would silently lose z from every shared
        # link. The full extent already fits at z=0.
        "minZoom": 0,
        "maxZoom": 14,
    },
    # Global camera presets. Like "world", these are CAMERA presets only —
    # their bboxes fall outside the canonical (conus/na) coverage bboxes, so
    # the frontend coverage filter surfaces them exactly when a non-canonical
    # data domain (e.g. global) is active. minZoom is 0 for the same permalink
    # reason as "world": several of these need sub-z2 to fit on phone widths.
    "europe": {
        "label": "Europe",
        "bbox": [-25.0, 34.0, 45.0, 72.0],
        "defaultCenter": [10.0, 53.0],
        "defaultZoom": 3.2,
        "minZoom": 0,
        "maxZoom": 14,
    },
    "africa": {
        "label": "Africa",
        "bbox": [-20.0, -36.0, 52.0, 38.0],
        "defaultCenter": [16.0, 1.0],
        "defaultZoom": 2.6,
        "minZoom": 0,
        "maxZoom": 14,
    },
    "india": {
        "label": "India",
        "bbox": [60.0, 4.0, 100.0, 38.0],
        "defaultCenter": [80.0, 21.0],
        "defaultZoom": 3.8,
        "minZoom": 0,
        "maxZoom": 14,
    },
    "asia": {
        "label": "Asia",
        "bbox": [40.0, 0.0, 150.0, 62.0],
        "defaultCenter": [95.0, 32.0],
        "defaultZoom": 2.4,
        "minZoom": 0,
        "maxZoom": 14,
    },
    "australia": {
        "label": "Australia",
        "bbox": [110.0, -48.0, 156.0, -8.0],
        "defaultCenter": [133.0, -27.0],
        "defaultZoom": 3.4,
        "minZoom": 0,
        "maxZoom": 14,
    },
    "south_america": {
        "label": "South America",
        "bbox": [-85.0, -57.0, -32.0, 14.0],
        "defaultCenter": [-58.0, -22.0],
        "defaultZoom": 2.7,
        "minZoom": 0,
        "maxZoom": 14,
    },
    # 180°-centered Pacific camera. east > 180 is intentional: MapLibre
    # fitBounds accepts un-normalized longitudes and world copies are on, so
    # the fit lands centered on the antimeridian instead of wrapping to a
    # degenerate reversed span.
    "pacific": {
        "label": "Pacific",
        "bbox": [115.0, -50.0, 245.0, 60.0],
        "defaultCenter": [180.0, 5.0],
        "defaultZoom": 1.8,
        "minZoom": 0,
        "maxZoom": 14,
    },
}
