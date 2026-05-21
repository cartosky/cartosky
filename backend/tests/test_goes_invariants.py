from __future__ import annotations

from app.services.colormaps import get_color_map_spec


def test_goes_ir13_palette_matches_reference_legend_breaks() -> None:
    spec = get_color_map_spec("goes_ir13_enhanced")

    assert spec["range"] == (163.0, 330.0)
    assert spec["legend_stops"][0] == (163.0, "#000000")
    assert spec["legend_stops"][1] == (173.0, "#6b6b6b")
    assert spec["legend_stops"][2] == (198.0, "#f4f4f4")
    assert spec["legend_stops"][-1] == (330.0, "#000000")

    color_by_level = dict(zip(spec["levels"], spec["colors"]))
    assert color_by_level[173.0] == "#6b6b6b"
    assert color_by_level[198.0] == "#f4f4f4"
    assert color_by_level[258.0] == "#f2f2f2"