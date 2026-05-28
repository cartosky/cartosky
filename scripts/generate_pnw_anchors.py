#!/usr/bin/env python3
"""
generate_pnw_anchors.py

Fetches live NWS /points grid coordinates for new PNW anchor cities and
produces two output files:

  pnw_new_geojson_features.json   — array of GeoJSON Feature objects ready
                                    to paste into anchors_conus.geojson

  pnw_new_index_entries.json      — dict of anchor_id -> AnchorInfo entries
                                    ready to merge into anchor_index.json

Run from the repo root:
    python3 scripts/generate_pnw_anchors.py

Requires: Python 3.8+, no third-party deps (uses stdlib urllib only).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# City definitions
# Priority suffix determines zoom visibility:
#   _1 – _4  already exist (current CONUS set)
#   _5 – _8  appear at regional zoom (~zoom 6-7)
#   _9+      appear at state/sub-state zoom (~zoom 8+)
# ---------------------------------------------------------------------------

NEW_CITIES: list[tuple[str, str, str, str, float, float]] = [
    # (anchor_id, city, st, state, lat, lon)

    # --- Washington ---
    ("WA_5",  "Tacoma",        "WA", "Washington", 47.2529, -122.4443),
    ("WA_6",  "Olympia",       "WA", "Washington", 47.0379, -122.9007),
    ("WA_7",  "Everett",       "WA", "Washington", 47.9790, -122.2021),
    ("WA_8",  "Yakima",        "WA", "Washington", 46.6021, -120.5059),
    ("WA_9",  "Wenatchee",     "WA", "Washington", 47.4235, -120.3103),
    ("WA_10", "Mount Vernon",  "WA", "Washington", 48.4226, -122.3343),
    ("WA_11", "Aberdeen",      "WA", "Washington", 46.9757, -123.8154),
    ("WA_12", "Walla Walla",   "WA", "Washington", 46.0646, -118.3430),

    # --- Oregon ---
    ("OR_5",  "Salem",         "OR", "Oregon",     44.9429, -123.0351),
    ("OR_6",  "Corvallis",     "OR", "Oregon",     44.5646, -123.2620),
    ("OR_7",  "Roseburg",      "OR", "Oregon",     43.2165, -123.3417),
    ("OR_8",  "Klamath Falls", "OR", "Oregon",     42.2249, -121.7817),
    ("OR_9",  "Pendleton",     "OR", "Oregon",     45.6721, -118.7886),
    ("OR_10", "Astoria",       "OR", "Oregon",     46.1879, -123.8313),

    # --- Northern Idaho ---
    # (Southern ID already has Boise/Idaho Falls/Pocatello as _1–_4)
    ("ID_5",  "Twin Falls",    "ID", "Idaho",      42.5630, -114.4609),
    ("ID_6",  "Lewiston",      "ID", "Idaho",      46.4165, -117.0177),
    ("ID_7",  "Sandpoint",     "ID", "Idaho",      48.2766, -116.5531),
    ("ID_8",  "Moscow",        "ID", "Idaho",      46.7324, -117.0002),
]

NWS_USER_AGENT = "(CartoSky anchor generator, contact@cartosky.com)"
NWS_POINTS_BASE = "https://api.weather.gov/points"
REQUEST_DELAY_S = 0.4   # stay well under NWS rate limits
REQUEST_TIMEOUT_S = 10


def fetch_nws_points(lat: float, lon: float) -> dict[str, Any]:
    url = f"{NWS_POINTS_BASE}/{lat:.4f},{lon:.4f}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": NWS_USER_AGENT,
            "Accept": "application/geo+json",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def resolve_grid(lat: float, lon: float) -> tuple[str, int, int] | None:
    """Return (wfo, gridX, gridY) or None on failure."""
    try:
        data = fetch_nws_points(lat, lon)
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as exc:
        print(f"  NWS error: {exc}")
        return None

    props = data.get("properties", {})
    wfo = props.get("cwa") or props.get("gridId")
    gx = props.get("gridX")
    gy = props.get("gridY")

    if not wfo or gx is None or gy is None:
        print(f"  NWS response missing fields: {props}")
        return None

    return wfo, int(gx), int(gy)


def build_geojson_feature(
    anchor_id: str,
    city: str,
    st: str,
    state: str,
    lat: float,
    lon: float,
    wfo: str,
    grid_x: int,
    grid_y: int,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": anchor_id,
        "properties": {
            "st": st,
            "state": state,
            "city": city,
            "wfo": wfo,
            "gridX": grid_x,
            "gridY": grid_y,
        },
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
    }


def build_index_entry(
    anchor_id: str,
    city: str,
    st: str,
    state: str,
    lat: float,
    lon: float,
    wfo: str,
    grid_x: int,
    grid_y: int,
) -> dict[str, Any]:
    return {
        "city": city,
        "state": state,
        "st": st,
        "lat": lat,
        "lon": lon,
        "wfo": wfo,
        "gridX": grid_x,
        "gridY": grid_y,
    }


def main() -> None:
    geojson_features: list[dict[str, Any]] = []
    index_entries: dict[str, dict[str, Any]] = {}
    failed: list[str] = []

    total = len(NEW_CITIES)
    for i, (anchor_id, city, st, state, lat, lon) in enumerate(NEW_CITIES, 1):
        print(f"[{i:2}/{total}] {anchor_id:8} {city:18} ({lat:.4f}, {lon:.4f})")
        grid = resolve_grid(lat, lon)

        if grid is None:
            print(f"  FAILED — skipping {anchor_id}")
            failed.append(anchor_id)
            time.sleep(REQUEST_DELAY_S)
            continue

        wfo, gx, gy = grid
        print(f"  OK  wfo={wfo}  gridX={gx}  gridY={gy}")

        geojson_features.append(
            build_geojson_feature(anchor_id, city, st, state, lat, lon, wfo, gx, gy)
        )
        index_entries[anchor_id] = build_index_entry(
            anchor_id, city, st, state, lat, lon, wfo, gx, gy
        )
        time.sleep(REQUEST_DELAY_S)

    # Write GeoJSON features file
    geojson_path = Path("pnw_new_geojson_features.json")
    with open(geojson_path, "w") as f:
        json.dump(geojson_features, f, indent=2)
    print(f"\nWrote {len(geojson_features)} GeoJSON features → {geojson_path}")

    # Write index entries file
    index_path = Path("pnw_new_index_entries.json")
    with open(index_path, "w") as f:
        json.dump(index_entries, f, indent=2)
    print(f"Wrote {len(index_entries)} index entries → {index_path}")

    if failed:
        print(f"\nFAILED ({len(failed)}): {', '.join(failed)}")
        print("Re-run after checking NWS API availability, or add grid values manually.")
    else:
        print("\nAll cities resolved successfully.")

    print("""
Next steps:
  1. Paste the contents of pnw_new_geojson_features.json into
     frontend/public/data/anchors_conus.geojson (inside the "features" array,
     before the closing ]).

  2. Merge the contents of pnw_new_index_entries.json into
     data/anchor_index.json (inside the "anchors" object).

  3. Restart the backend so nws.py reloads the anchor index.

  4. Test a few new IDs via:
       curl http://localhost:8200/api/v4/anchors/WA_5/weather
""")


if __name__ == "__main__":
    main()
