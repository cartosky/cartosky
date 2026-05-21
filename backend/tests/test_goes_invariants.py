from __future__ import annotations

from app.services.colormaps import get_color_map_spec


def test_goes_ir13_palette_matches_reference_legend_breaks() -> None:
    spec = get_color_map_spec("goes_ir13_enhanced")

    assert spec["type"] == "discrete"
    assert spec["units"] == "C"
    assert spec["range"] == (183.15, 313.15)
    assert spec["legend_stops"][0] == (-90.0, "#7d007a")
    assert spec["legend_stops"][10] == (-80.0, "#e86fbc")
    assert spec["legend_stops"][40] == (-50.0, "#fdff04")
    assert spec["legend_stops"][70] == (-20.0, "#55ffff")
    assert spec["legend_stops"][-1] == (40.0, "#000000")

    color_by_level = dict(zip(spec["levels"], spec["colors"]))
    assert color_by_level[183.15] == "#7d007a"
    assert color_by_level[193.15] == "#e86fbc"
    assert color_by_level[223.15] == "#fdff04"
    assert color_by_level[253.15] == "#55ffff"
    assert color_by_level[313.15] == "#000000"