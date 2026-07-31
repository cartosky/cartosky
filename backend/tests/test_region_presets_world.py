"""World camera preset (global go-live design §4, decision U3).

`world` is a CAMERA preset served by /api/regions like every other entry. The
frontend's coverage filter (`filterRegionOptionsForDataDomain`) shows it only
when a non-canonical data domain is active; that filter works by bbox
containment against the model's canonical region, so these tests pin the two
properties it depends on: the entry has the same shape as its neighbours, and
its bbox is NOT contained by either canonical region (conus / na).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.regions import REGION_PRESETS  # noqa: E402

CANONICAL_REGION_IDS = ("conus", "na")


def _contains(parent: list[float], child: list[float]) -> bool:
    return (
        child[0] >= parent[0]
        and child[1] >= parent[1]
        and child[2] <= parent[2]
        and child[3] <= parent[3]
    )


def test_world_preset_exists_with_the_shared_entry_shape() -> None:
    world = REGION_PRESETS["world"]
    assert world["label"] == "World"
    assert set(world) == set(REGION_PRESETS["na"])


def test_world_preset_is_a_full_extent_camera() -> None:
    world = REGION_PRESETS["world"]
    west, south, east, north = world["bbox"]
    assert (west, east) == (-180.0, 180.0)
    assert south <= -85.0 and north >= 85.0
    lon, lat = world["defaultCenter"]
    assert -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0
    # Far enough out that the full extent fits at desktop widths.
    assert world["defaultZoom"] <= 2


def test_world_min_zoom_stays_inside_the_permalink_z_range() -> None:
    """z < 0 is dropped by both permalink serializers, silently losing the
    camera from shared links (frontend permalink.ts / permalink-read.ts gate on
    z >= 0). No preset may allow the user to zoom out of the URL."""
    for region_id, preset in REGION_PRESETS.items():
        assert preset["minZoom"] >= 0, f"{region_id} allows zoom below the permalink z range"
    assert REGION_PRESETS["world"]["minZoom"] == 0


def test_world_is_excluded_from_every_canonical_coverage_list() -> None:
    """This containment failure IS the coverage gate — U3 needs no new filter."""
    for region_id in CANONICAL_REGION_IDS:
        assert not _contains(REGION_PRESETS[region_id]["bbox"], REGION_PRESETS["world"]["bbox"])


def test_canonical_presets_are_unchanged_by_the_world_addition() -> None:
    for region_id in CANONICAL_REGION_IDS:
        assert _contains(REGION_PRESETS["world"]["bbox"], REGION_PRESETS[region_id]["bbox"])
    # conus stays inside na; the regional presets stay inside conus.
    assert _contains(REGION_PRESETS["na"]["bbox"], REGION_PRESETS["conus"]["bbox"])
    assert _contains(REGION_PRESETS["conus"]["bbox"], REGION_PRESETS["midwest"]["bbox"])
