"""V2 palette constants for COG-encoded tiles.

Band 1 in each COG stores a byte: a palette index for discrete fields, or a fixed-range
byte (0–255) for continuous fields. Band 2 stores alpha as a byte. Runtime tiles are
rendered by mapping LUT[band1] and applying band2 as the output alpha.
"""

from __future__ import annotations

import numpy as np
from typing import cast

# Precipitation type configuration with levels and colors.
# Legacy thresholds were specified in mm/hr; convert once to in/hr so legend
# units are consistent with HRRR-style precip displays.
MM_PER_INCH = 25.4
RAIN_LEVELS_MMHR = [0.01, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 4, 6, 10, 16, 24]
SNOW_LEVELS_MMHR = [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0]
WINTER_LEVELS_MMHR = [0.1, 0.5, 1, 2, 3, 4, 6, 10, 14]
RAIN_LEVELS = [value / MM_PER_INCH for value in RAIN_LEVELS_MMHR]
SNOW_LEVELS = [value / MM_PER_INCH for value in SNOW_LEVELS_MMHR]
WINTER_LEVELS = [value / MM_PER_INCH for value in WINTER_LEVELS_MMHR]

PRECIP_CONFIG = {
    "rain": {
        "levels": RAIN_LEVELS,
        "colors": [
            "#90ee90", "#66dd66", "#33cc33", "#00bb00", "#009900", "#007700",
            "#005500", "#ffff00", "#ffb300", "#ff6600", "#ff0000", "#ff00ff",
        ],
    },
    "frzr": {
        "levels": WINTER_LEVELS,
        "colors": [
            "#ffc0cb", "#ff69b4", "#ff1493", "#c71585", "#931040", "#b03060",
            "#d20000", "#ff2400", "#ff4500",
        ],
    },
    "sleet": {
        "levels": WINTER_LEVELS,
        "colors": [
            "#e0ffff", "#add8e6", "#9370db", "#8a2be2", "#9400d3", "#800080",
            "#4b0082", "#8b008b", "#b22222",
        ],
    },
    "snow": {
        "levels": SNOW_LEVELS,
        "colors": [
            "#c0ffff", "#55ffff", "#4feaff", "#48d3ff", "#42bfff", "#3caaff",
            "#3693ff", "#2a69f1", "#1d42ca", "#1b18dc", "#161fb8", "#130495",
            "#130495", "#550a87", "#550a87", "#af068e", "#ea0081",
        ],
    },
}


def _hex_to_rgb(hex_color: str) -> np.ndarray:
    hex_str = hex_color.strip().lstrip("#")
    return np.array(
        [
            int(hex_str[0:2], 16),
            int(hex_str[2:4], 16),
            int(hex_str[4:6], 16),
        ],
        dtype=np.float64,
    )


def _rgb_to_hex(rgb: np.ndarray) -> str:
    r, g, b = np.clip(np.rint(rgb), 0, 255).astype(np.uint8).tolist()
    return f"#{r:02x}{g:02x}{b:02x}"


def _expand_hex_ramp(colors_hex: list[str], n: int) -> list[str]:
    if not colors_hex:
        raise ValueError("colors_hex must not be empty")
    if len(colors_hex) == 1:
        return [colors_hex[0]] * n

    anchors = np.stack([_hex_to_rgb(color) for color in colors_hex], axis=0)
    stop_positions = np.linspace(0.0, 1.0, num=len(colors_hex), dtype=np.float64)
    target_positions = np.linspace(0.0, 1.0, num=n, dtype=np.float64)

    r = np.interp(target_positions, stop_positions, anchors[:, 0])
    g = np.interp(target_positions, stop_positions, anchors[:, 1])
    b = np.interp(target_positions, stop_positions, anchors[:, 2])
    return [_rgb_to_hex(np.array([rr, gg, bb], dtype=np.float64)) for rr, gg, bb in zip(r, g, b)]


def _expand_color_anchors(levels: list[float], anchors: list[tuple[float, str]]) -> list[str]:
    if not levels:
        return []
    if not anchors:
        raise ValueError("anchors must not be empty")

    ordered = sorted((float(value), color) for value, color in anchors)
    anchor_values = np.array([value for value, _ in ordered], dtype=np.float64)
    anchor_colors = np.stack([_hex_to_rgb(color) for _, color in ordered], axis=0)
    target_values = np.array(levels, dtype=np.float64)
    r = np.interp(target_values, anchor_values, anchor_colors[:, 0])
    g = np.interp(target_values, anchor_values, anchor_colors[:, 1])
    b = np.interp(target_values, anchor_values, anchor_colors[:, 2])
    return [_rgb_to_hex(np.array([rr, gg, bb], dtype=np.float64)) for rr, gg, bb in zip(r, g, b)]


GOES_IR13_LEGEND_STOPS = [
    (-90.0, "#7d007a"),
    (-89.0, "#870a8a"),
    (-88.0, "#941391"),
    (-87.0, "#9d2193"),
    (-86.0, "#aa2a97"),
    (-85.0, "#bc3ea2"),
    (-84.0, "#ca48ac"),
    (-83.0, "#d352af"),
    (-82.0, "#de5cb6"),
    (-81.0, "#e866c0"),
    (-80.0, "#e86fbc"),
    (-79.0, "#e7e7e7"),
    (-78.0, "#d0d0d0"),
    (-77.0, "#b5b5b5"),
    (-76.0, "#a3a3a3"),
    (-75.0, "#898989"),
    (-74.0, "#5d5d5d"),
    (-73.0, "#474747"),
    (-72.0, "#2e2e2e"),
    (-71.0, "#171516"),
    (-70.0, "#000000"),
    (-69.0, "#1b0101"),
    (-68.0, "#3c0400"),
    (-67.0, "#530200"),
    (-66.0, "#660000"),
    (-65.0, "#920003"),
    (-64.0, "#ad0002"),
    (-63.0, "#bb0000"),
    (-62.0, "#d00002"),
    (-61.0, "#eb0000"),
    (-60.0, "#f40005"),
    (-59.0, "#f51f03"),
    (-58.0, "#f54204"),
    (-57.0, "#f55403"),
    (-56.0, "#f66704"),
    (-55.0, "#f79405"),
    (-54.0, "#f8a702"),
    (-53.0, "#f9bd04"),
    (-52.0, "#face00"),
    (-51.0, "#fce801"),
    (-50.0, "#fdff04"),
    (-49.0, "#e2fd00"),
    (-48.0, "#bcff00"),
    (-47.0, "#acfd01"),
    (-46.0, "#92ff03"),
    (-45.0, "#69ff04"),
    (-44.0, "#56fe02"),
    (-43.0, "#4efe00"),
    (-42.0, "#4fff00"),
    (-41.0, "#4dfc00"),
    (-40.0, "#4fff01"),
    (-39.0, "#43df0f"),
    (-38.0, "#39c117"),
    (-37.0, "#31a824"),
    (-36.0, "#2a952a"),
    (-35.0, "#1e6f42"),
    (-34.0, "#114454"),
    (-33.0, "#0a2561"),
    (-32.0, "#0b2b5e"),
    (-31.0, "#081167"),
    (-30.0, "#07006e"),
    (-29.0, "#0f2188"),
    (-28.0, "#173f9b"),
    (-27.0, "#1d53a4"),
    (-26.0, "#2266ae"),
    (-25.0, "#38aad1"),
    (-24.0, "#37a8cc"),
    (-23.0, "#3fbddb"),
    (-22.0, "#47d4e8"),
    (-21.0, "#4ee9f1"),
    (-20.0, "#55ffff"),
    (-18.0, "#fffff3"),
    (-16.0, "#f9fdfe"),
    (-14.0, "#f4f4f4"),
    (-12.0, "#f3f3f3"),
    (-10.0, "#eeeeee"),
    (-8.0, "#dfdfdf"),
    (-6.0, "#dcdcdc"),
    (-4.0, "#d5d5d5"),
    (-2.0, "#cecece"),
    (0.0, "#c8c8c8"),
    (2.0, "#c0c0c0"),
    (4.0, "#b5b5b5"),
    (6.0, "#ababab"),
    (8.0, "#a0a0a0"),
    (10.0, "#949494"),
    (12.0, "#8d8d8d"),
    (14.0, "#848484"),
    (16.0, "#797979"),
    (18.0, "#6f6f6f"),
    (20.0, "#696969"),
    (22.0, "#5f5f5f"),
    (24.0, "#565656"),
    (26.0, "#4a4a4a"),
    (28.0, "#3f3f3f"),
    (30.0, "#333333"),
    (32.0, "#282828"),
    (34.0, "#1f1f1f"),
    (36.0, "#131313"),
    (38.0, "#030303"),
    (40.0, "#000000"),
]
GOES_IR13_LEVELS = [round(value + 273.15, 2) for value, _ in GOES_IR13_LEGEND_STOPS]
GOES_IR13_COLORS = [color for _, color in GOES_IR13_LEGEND_STOPS]
GOES_IR13_RANGE = (GOES_IR13_LEVELS[0], GOES_IR13_LEVELS[-1])


GFS_PTYPE_INTENSITY_ORDER = ("rain", "snow", "ice")
GFS_PTYPE_INTENSITY_BINS = {
    "rain": [0.0, 0.01, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
    "snow": [0.05, 0.25, 0.50, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0],
    "ice": [0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0, 1.25, 1.5, 2.0],
}
GFS_PTYPE_INTENSITY_COLORS = {
    "rain": [
        "#b4e6b7", "#9ed1a0", "#89be8a", "#74ab76", "#609961", "#4d864d", "#407f45",
        "#34783c", "#277134", "#1c6a2b", "#126324", "#f5f166", "#f7d95b", "#f9c252",
        "#fbab48", "#fd943f",
    ],
    "snow": [
        "#bdd7ff", "#a0c4ff", "#7eb0ff", "#5e9cff", "#4289ff", "#3478f6", "#2f74f0",
        "#1d5dd8", "#1550cc", "#0d47bf",
    ],
    "ice": [
        "#fff0f5", "#fff7f3", "#fde0dd", "#fcc5c0", "#faa3a0", "#f768a1", "#ea4b9a",
        "#dd3497", "#c4298e", "#ae017e", "#930086", "#7a0177", "#67006b", "#550061",
        "#49006a", "#3d0052", "#2d0044", "#1a0033",
    ],
}
GFS_PTYPE_INTENSITY_LABELS = {
    "rain": "Rain",
    "snow": "Snow",
    "ice": "Ice",
}


def _build_gfs_ptype_intensity_flat_palette() -> tuple[
    list[float],
    list[str],
    dict[str, dict[str, int]],
    dict[str, list[float]],
    list[dict[str, object]],
]:
    levels: list[float] = []
    colors: list[str] = []
    breaks: dict[str, dict[str, int]] = {}
    levels_by_type: dict[str, list[float]] = {}
    legend_entries: list[dict[str, object]] = []
    offset = 0
    for key in GFS_PTYPE_INTENSITY_ORDER:
        bins = list(GFS_PTYPE_INTENSITY_BINS[key])
        type_colors = list(GFS_PTYPE_INTENSITY_COLORS[key])
        type_levels = bins[: len(type_colors)]
        count = min(len(type_colors), len(type_levels))
        if count <= 0:
            continue
        levels.extend(type_levels[:count])
        colors.extend(type_colors[:count])
        breaks[key] = {
            "offset": offset,
            "count": count,
        }
        levels_by_type[key] = type_levels[:count]
        legend_entries.append(
            {
                "value": float(type_levels[0]),
                "color": str(type_colors[0]),
                "label": str(GFS_PTYPE_INTENSITY_LABELS[key]),
            }
        )
        offset += count
    return levels, colors, breaks, levels_by_type, legend_entries


(
    GFS_PTYPE_INTENSITY_LEVELS,
    GFS_PTYPE_INTENSITY_COLORS_FLAT,
    GFS_PTYPE_INTENSITY_BREAKS,
    GFS_PTYPE_INTENSITY_LEVELS_BY_TYPE,
    GFS_PTYPE_INTENSITY_LEGEND_ENTRIES,
) = _build_gfs_ptype_intensity_flat_palette()

RADAR_PTYPE_ORDER = ("rain", "snow", "sleet", "frzr")


def _build_modeled_reflectivity_palette(ptype: str) -> tuple[list[float], list[str]]:
    levels = [float(value) for value in range(5, 71)]

    anchors_by_ptype = {
        "rain": [
            (5.0, "#a8f0a8"),
            (10.0, "#4efb4c"),
            (15.0, "#2d9e2e"),
            (20.0, "#155719"),
            (25.0, "#c8e640"),
            (30.0, "#feff50"),
            (33.0, "#f8d000"),
            (35.0, "#f8b422"),
            (38.0, "#f8a442"),
            (40.0, "#f57030"),
            (43.0, "#f5253a"),
            (45.0, "#d41020"),
            (48.0, "#c21230"),
            (50.0, "#c21230"),
            (53.0, "#c21230"),
            (55.0, "#f800fd"),
            (60.0, "#9854c6"),
            (65.0, "#fdfdfd"),
            (70.0, "#fdfdfd"),
        ],
        "frzr": [
            (5.0, "#ffd6e1"),
            (10.0, "#fbcad0"),
            (20.0, "#dc4f8b"),
            (30.0, "#bd1366"),
            (40.0, "#da2d0d"),
            (50.0, "#fd0000"),
            (55.0, "#f800fd"),
            (65.0, "#9854c6"),
            (70.0, "#fdfdfd"),
        ],
        "sleet": [
            (5.0, "#ddd0ff"),
            (10.0, "#b49dff"),
            (20.0, "#c54ef9"),
            (30.0, "#a913d3"),
            (40.0, "#bc0f9c"),
            (50.0, "#fd0000"),
            (55.0, "#f800fd"),
            (65.0, "#9854c6"),
            (70.0, "#fdfdfd"),
        ],
        "snow": [
            (5.0, "#c8ffff"),
            (10.0, "#55ffff"),
            (20.0, "#3caaff"),
            (30.0, "#1e40d0"),
            (40.0, "#2a009a"),
            (50.0, "#fd0000"),
            (55.0, "#f800fd"),
            (65.0, "#9854c6"),
            (70.0, "#fdfdfd"),
        ],
    }

    colors = _expand_color_anchors(levels, anchors_by_ptype[ptype])
    return levels, colors


_MODELED_REFL_PALETTES = {
    ptype: _build_modeled_reflectivity_palette(ptype)
    for ptype in RADAR_PTYPE_ORDER
}

_MODELED_REFL_CONFIG = {
    ptype: {
        "levels": list(levels),
        "colors": list(colors),
    }
    for ptype, (levels, colors) in _MODELED_REFL_PALETTES.items()
}

_MRMS_RADAR_BASE_CONFIG = {
    "frzr": {
        "levels": [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 70],
        "colors": [
            "#ffffff", "#fbcad0", "#f893ba", "#e96c9f", "#dd88a5", "#dc4f8b", "#d03a80",
            "#c62773", "#bd1366", "#b00145", "#c21230", "#da2d0d", "#e33403", "#f53c00",
            "#f53c00", "#f54603",
        ],
    },
    "sleet": {
        "levels": [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 70],
        "colors": [
            "#ffffff", "#b49dff", "#b788ff", "#c56cff", "#c54ef9", "#c54ef9", "#b730e7",
            "#a913d3", "#a913d3", "#9b02b4", "#bc0f9c", "#a50085", "#c52c7b", "#cf346f",
            "#d83c64", "#e24556",
        ],
    },
    "snow": {
        "levels": [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 70],
        "colors": [
            "#ffffff", "#55ffff", "#4feaff", "#48d3ff", "#42bfff", "#3caaff", "#3693ff",
            "#2a6aee", "#1e40d0", "#110ba7", "#2a009a", "#0c276f", "#540093", "#bc0f9c",
            "#d30085", "#f5007f",
        ],
    },
}

MRMS_RADAR_CONFIG = {
    **_MRMS_RADAR_BASE_CONFIG,
    # The observed reflectivity-only product can follow the NWS Enhanced scale
    # exactly, including cyan/blue weak echoes. In the ptype composite, blue is
    # reserved for snow, so rain keeps a green low end and rejoins the NWS-style
    # yellow/red/purple high end as intensity increases.
    "rain": {
        "levels": [5, 10, 15, 20, 23, 25, 28, 30, 33, 35, 38, 40, 43, 45, 48, 50, 55, 60, 65, 70],
        "colors": [
            "#d7f7cf", "#9cf29a", "#4be85a", "#02fd02", "#01dc02", "#01c501",
            "#00a901", "#008e00", "#80ca01", "#fdf802", "#f0d600", "#e5bc00",
            "#fdae00", "#fd9500", "#fd5f00", "#fd0000", "#d40000", "#bc0000",
            "#f800fd", "#fdfdfd",
        ],
    },
}


def _build_radar_ptype_flat_palette(
    config: dict[str, dict[str, list[float] | list[str]]] = _MODELED_REFL_CONFIG,
) -> tuple[list[float], list[str], dict[str, dict[str, int]]]:
    levels: list[float] = []
    colors: list[str] = []
    breaks: dict[str, dict[str, int]] = {}
    offset = 0
    for key in RADAR_PTYPE_ORDER:
        cfg = config[key]
        type_levels = list(cast(list[float], cfg["levels"]))
        type_colors = list(cast(list[str], cfg["colors"]))
        levels.extend(type_levels)
        colors.extend(type_colors)
        breaks[key] = {
            "offset": offset,
            "count": len(type_colors),
        }
        offset += len(type_colors)
    return levels, colors, breaks


RADAR_PTYPE_LEVELS, RADAR_PTYPE_COLORS, RADAR_PTYPE_BREAKS = _build_radar_ptype_flat_palette()
RADAR_PTYPE_LEVELS_BY_TYPE = {
    key: list(_MODELED_REFL_CONFIG[key]["levels"][: len(_MODELED_REFL_CONFIG[key]["colors"])])
    for key in RADAR_PTYPE_ORDER
}
MRMS_RADAR_PTYPE_ORDER = RADAR_PTYPE_ORDER
MRMS_RADAR_PTYPE_LEVELS, MRMS_RADAR_PTYPE_COLORS, MRMS_RADAR_PTYPE_BREAKS = _build_radar_ptype_flat_palette(MRMS_RADAR_CONFIG)
MRMS_RADAR_PTYPE_LEVELS_BY_TYPE = {
    key: list(MRMS_RADAR_CONFIG[key]["levels"][: len(MRMS_RADAR_CONFIG[key]["colors"])])
    for key in MRMS_RADAR_PTYPE_ORDER
}


def _build_mrms_reflectivity_palette() -> tuple[list[float], list[str]]:
    levels = [float(value) for value in range(5, 81)]
    colors = _expand_color_anchors(
        levels,
        [
            (5.0, "#04e9e7"),
            (10.0, "#019ff4"),
            (15.0, "#0300f4"),
            (20.0, "#02fd02"),
            (25.0, "#01c501"),
            (30.0, "#008e00"),
            (35.0, "#fdf802"),
            (40.0, "#e5bc00"),
            (45.0, "#fd9500"),
            (50.0, "#fd0000"),
            (55.0, "#d40000"),
            (60.0, "#bc0000"),
            (65.0, "#f800fd"),
            (70.0, "#9854c6"),
            (75.0, "#fdfdfd"),
            (80.0, "#fdfdfd"),
        ],
    )
    return levels, colors


MRMS_REFLECTIVITY_LEVELS, MRMS_REFLECTIVITY_COLORS = _build_mrms_reflectivity_palette()

# 2m temperature (°F) palette
TMP2M_F_COLOR_ANCHORS = [
    (-60, "#184a6a"), (-59, "#1e506b"), (-58, "#225a70"), (-57, "#2b6377"), (-56, "#357081"),
    (-55, "#396f85"), (-54, "#3d7487"), (-53, "#46808e"), (-52, "#488390"), (-51, "#52909b"),
    (-50, "#5b979f"), (-49, "#629ea3"), (-48, "#6aa7aa"), (-47, "#72afb0"), (-46, "#78b5b4"),
    (-45, "#7bb8b7"), (-44, "#8ccac3"), (-43, "#93d3cb"), (-42, "#98d8d1"), (-41, "#9ad8d2"),
    (-40, "#a4e9dc"), (-39, "#a1e6d9"), (-38, "#a2e6db"), (-37, "#9eded6"), (-36, "#9fdad8"),
    (-35, "#9bd7d5"), (-34, "#9dd3d7"), (-33, "#99c5cf"), (-32, "#9bc3d4"), (-31, "#9ab7cc"),
    (-30, "#99b6cf"), (-29, "#93adc8"), (-28, "#889dbb"), (-27, "#8c9fbc"), (-26, "#969dc9"),
    (-25, "#898eb8"), (-24, "#9398ca"), (-23, "#8a89b7"), (-22, "#8d85bd"), (-21, "#8b84bb"),
    (-20, "#8175b4"), (-19, "#8371a9"), (-18, "#786187"), (-17, "#7f64a1"), (-16, "#8569b5"),
    (-15, "#7c5ea5"), (-14, "#825bae"), (-13, "#7953a1"), (-12, "#7e50ab"), (-11, "#784e99"),
    (-10, "#70457a"), (-9, "#724388"), (-8, "#6c397c"), (-7, "#6f3697"), (-6, "#7636a1"),
    (-5, "#6d3395"), (-4, "#712d9c"), (-3, "#6a2f91"), (-2, "#65287f"), (-1, "#61237c"),
    (0, "#682480"), (1, "#7d3183"), (2, "#983b9e"), (3, "#924e9b"), (4, "#9b50a4"),
    (5, "#9e5ba7"), (6, "#93609c"), (7, "#895d93"), (8, "#9a75a7"), (9, "#8c6f98"),
    (10, "#b19bc1"), (11, "#a897b7"), (12, "#ada5bf"), (13, "#b0bac6"), (14, "#afbdc6"),
    (15, "#dbeef3"), (16, "#b5c7d3"), (17, "#96a1b0"), (18, "#a0bdd6"), (19, "#89a5c2"),
    (20, "#8aacd1"), (21, "#7399bd"), (22, "#668ebf"), (23, "#6896cf"), (24, "#5884bc"),
    (25, "#4c82c6"), (26, "#3f73b9"), (27, "#3262a7"), (28, "#2660b2"), (29, "#1d56a2"),
    (30, "#004fab"), (31, "#0046ab"), (32, "#003b47"), (33, "#0d454f"), (34, "#1e4e54"),
    (35, "#2b5656"), (36, "#36615c"), (37, "#446960"), (38, "#4f6e65"), (39, "#6f7e71"),
    (40, "#779073"), (41, "#769173"), (42, "#90a47a"), (43, "#9dab7f"), (44, "#adb986"),
    (45, "#a8b685"), (46, "#c6cd90"), (47, "#d4d996"), (48, "#e1e19a"), (49, "#f3efa1"),
    (50, "#fff19f"), (51, "#eedc92"), (52, "#e8d28c"), (53, "#dbc182"), (54, "#d8bc7f"),
    (55, "#caa771"), (56, "#bf9767"), (57, "#b6865c"), (58, "#b6845b"), (59, "#ab724f"),
    (60, "#a36647"), (61, "#9c5d42"), (62, "#91503b"), (63, "#864535"), (64, "#813a2f"),
    (65, "#772825"), (66, "#732122"), (67, "#671517"), (68, "#611013"), (69, "#5b0a0f"),
    (70, "#5b0a0f"), (71, "#5b0a0f"), (72, "#611013"), (73, "#661416"), (74, "#6b191a"),
    (75, "#6e1b21"), (76, "#722025"), (77, "#742128"), (78, "#762533"), (79, "#7a2c33"),
    (80, "#6b2d2a"), (81, "#723c33"), (82, "#763f37"), (83, "#7d483e"), (84, "#854f44"),
    (85, "#8d5c4e"), (86, "#916556"), (87, "#946e62"), (88, "#9c746a"), (89, "#a67c72"),
    (90, "#ac867d"), (91, "#b7938b"), (92, "#ba9b92"), (93, "#c1a39b"), (94, "#c8b3ab"),
    (95, "#ceb9b1"), (96, "#d6c5bc"), (97, "#e6d6cc"), (98, "#e7d9d0"), (99, "#fae9de"),
    (100, "#ecddd2"), (101, "#e3d2c8"), (102, "#d7c9c1"), (103, "#c3bdbb"), (104, "#bcaaa4"),
    (105, "#c1b4af"), (106, "#b6aba7"), (107, "#aba09d"), (108, "#a19693"), (109, "#958e8b"),
    (110, "#908582"), (111, "#8a7d7a"), (112, "#776f6f"), (113, "#757170"), (114, "#575b60"),
    (115, "#5d5e62"), (116, "#595a5f"), (117, "#4f5155"), (118, "#46474a"), (119, "#3f4042"), (120, "#2f2f31"),
]

TMP2M_F_RANGE = (-60.0, 120.0)
TMP2M_ANOM_F_LEVELS = [
    -30.0, -28.0, -24.0, -20.0, -18.0, -16.0, -14.0, -12.0, -10.0, -8.0,
    -7.0, -6.0, -5.0, -4.0, -3.0, -2.5, -2.0, -1.5, -1.0, -0.5,
    0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0,
    7.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 24.0, 28.0, 30.0,
]
TMP2M_ANOM_F_COLORS = [
    "#fbcdba", "#f8bac5", "#f3a8d2", "#eb95dd", "#e684e8", "#df71f4",
    "#b65bec", "#9157db", "#6c52ca", "#484fb8", "#244ba6", "#174693",
    "#205ead", "#2874c6", "#3b8adf", "#64b7f8", "#78cdf7", "#8ce3f6",
    "#9ff8f4", "#ffffff", "#ffffff", "#fefebe", "#fceaa0", "#fbd283",
    "#f9b365", "#f78b50", "#ef633e", "#dd3d2c", "#c21c26", "#9d253a",
    "#ba4354", "#d96073", "#f186a8", "#f9a7dd", "#e497c0", "#cc86a5",
    "#b27488", "#99646c", "#80544e", "#66442f",
]
TMP2M_ANOM_F_LEGEND_STOPS = list(zip(TMP2M_ANOM_F_LEVELS[:-1], TMP2M_ANOM_F_COLORS))
TMP2M_ANOM_F_COLOR_ANCHORS = TMP2M_ANOM_F_LEGEND_STOPS
TMP2M_ANOM_F_RANGE = (-30.0, 30.0)
HGT500_ANOM_DAM_LEVELS = [
    -40.0, -36.0, -34.0, -30.0, -26.0, -24.0, -20.0, -18.0, -14.0,
    -12.0, -10.0, -6.0, -4.0, 0.0, 4.0, 6.0, 10.0, 12.0,
    16.0, 18.0, 20.0, 24.0, 26.0, 30.0, 34.0, 36.0, 40.0,
]
HGT500_ANOM_DAM_COLORS = [
    "#061652", "#0c2f5f", "#14437b", "#1c5695", "#2368ad", "#307ab6",
    "#3c8abe", "#4f9ac7", "#6eadd1", "#8ac0db", "#a2cde2", "#bad9e9",
    "#ffffff", "#ffffff", "#fcdbc6", "#f9c7ae", "#f6b293", "#f19e7b",
    "#e6856a", "#da6a55", "#cf5246", "#c2383a", "#b41c2b", "#9c1028",
    "#810622", "#790378",
]
HGT500_ANOM_DAM_LEGEND_STOPS = list(zip(HGT500_ANOM_DAM_LEVELS[:-1], HGT500_ANOM_DAM_COLORS))
HGT500_ANOM_DAM_COLOR_ANCHORS = HGT500_ANOM_DAM_LEGEND_STOPS
HGT500_ANOM_DAM_RANGE = (-40.0, 40.0)
PRECIP_ANOM_IN_LEVELS = [
    -5.5, -5.0, -4.5, -4.0, -3.5, -3.0, -2.5, -2.0, -1.8, -1.6, -1.4,
    -1.2, -1.0, -0.8, -0.6, -0.4, -0.2, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2,
    1.4, 1.6, 1.8, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5,
]
PRECIP_ANOM_IN_COLORS = [
    "#fbc9c9", "#f5a1a1", "#e58281", "#e16464", "#d54f4f", "#c93c3c",
    "#b52828", "#a62021", "#64544a", "#776658", "#8b7668", "#9f8977",
    "#b39987", "#c7ab95", "#e1c3ad", "#efddcb", "#ffffff", "#c9e9b9",
    "#b7dfa7", "#9bd18c", "#7cc378", "#5aaf62", "#529d5a", "#4a874d",
    "#467847", "#2b6eeb", "#2b6eeb", "#3083f1", "#3c97f5", "#50a5f5",
    "#78b9fb", "#97d3fb", "#b5f1fb",
]
PRECIP_ANOM_IN_LEGEND_STOPS = list(zip(PRECIP_ANOM_IN_LEVELS[:-1], PRECIP_ANOM_IN_COLORS))
PRECIP_ANOM_IN_RANGE = (-5.5, 5.5)

# Total precipitation (inches)
precip_colors = [
    "#c0c0c0", "#909090", "#606060",
    "#b0f090", "#80e060", "#50c040",
    "#3070f0", "#5090f0", "#80b0f0", "#b0d0f0",
    "#ffff80", "#ffd060", "#ffa040",
    "#ff6030", "#e03020", "#a01010", "#700000",
    "#d0b0e0", "#b080d0", "#9050c0", "#7020a0",
    "#c040c0",
]
precip_levels = [
    0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.2, 1.6,
    2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 25.0,
]

# Total snowfall (inches, 10:1 ratio)
snow_colors = [
    "#aeb4c0", "#a19ca3", "#74737d", "#535254", 
    "#2f76df", "#3565e1","#1f5cc4", "#204eb5",
    "#3e0091", "#4c008f", "#5a008d", "#67008a", "#860087",
    "#a10285", "#c90181", "#f3027c",
    "#f41484", "#f53b9b", "#f65faf", "#f76eb7", "#f885c3",
    "#f58dc7", "#ea95ca", "#e79dcd", "#d9acd5", "#cfb2d6",
    "#c1c7dd", "#b6d8ec", "#a9e3ef", "#a1eff3", "#94f8f6",
    "#8dedeb", "#7edbd9", "#73c0c7", "#7cb9ca", "#81b7cd",
    "#88b0ce", "#8db0d0", "#90b0d2", "#93abd7", "#93abd7",
    "#99a7db", "#9da5dd", "#a5a0df", "#a5a0df", "#af9be7",
    "#af9be7", "#ad95e2", "#b795eb", "#b291e5", "#bf91f1",
    "#c68df5", "#c488f0", "#d187f9", "#cb84f3",
]
snow_levels = [
    0.1, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0,
    10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0,
    20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0,
    30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0, 37.0, 38.0, 39.0,
    40.0, 41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0,
]

SNOWFALL_TOTAL_COLOR_ANCHORS = list(zip(snow_levels, snow_colors))
SNOWFALL_TOTAL_RANGE = (0.0, 48.0)

ICE_TOTAL_LEVELS = [
    0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45,
    0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.25, 1.50, 2.00,
]
ICE_TOTAL_COLORS = [
    "#fef7f3", "#fdebe8", "#fce0dd", "#fbd2ce", "#fac5c0", "#f9b2ba",
    "#f89eb5", "#f783ab", "#f666a1", "#ea4d9c", "#dc3397", "#c4188a",
    "#ad007e", "#93007a", "#790077", "#600070", "#49006a",
]
ICE_TOTAL_LEGEND_STOPS = list(zip(ICE_TOTAL_LEVELS[:-1], ICE_TOTAL_COLORS))
ICE_TOTAL_LEGEND_LABELS = [
    "0.05-0.10", "0.10-0.15", "0.15-0.20", "0.20-0.25", "0.25-0.30", "0.30-0.35",
    "0.35-0.40", "0.40-0.45", "0.45-0.50", "0.50-0.60", "0.60-0.70", "0.70-0.80",
    "0.80-0.90", "0.90-1", "1-1.25", "1.25-1.50", "1.50-2",
]
ICE_TOTAL_LEGEND_ENTRIES = [
    {
        "value": float(ICE_TOTAL_LEVELS[index]),
        "color": color,
        "label": ICE_TOTAL_LEGEND_LABELS[index],
    }
    for index, color in enumerate(ICE_TOTAL_COLORS)
]

# 10m wind speed (mph) continuous palette anchors
WSPD10M_COLOR_ANCHORS = [
    (0, "#ffffff"), (4, "#e1e1e1"), (6, "#d1d1d1"), (8, "#b3b3b3"),
    (10, "#8c8c8c"), (12, "#595959"), (14, "#595959"), (16, "#005ac8"), (18, "#0078ea"),
    (20, "#339bef"), (22, "#82cbf6"), (24, "#82cbf6"), (26, "#a4eff9"), (28, "#00cd41"),
    (30, "#1bef53"), (32, "#59f36f"), (34, "#59f36f"), (36, "#a4faa6"), (38, "#bcffba"),
    (40, "#fffaa7"), (42, "#ffe576"), (44, "#ffb944"), (46, "#ffb944"), (48, "#ff9727"),
    (50, "#ff561f"), (52, "#ff2c1c"), (54, "#eb1818"), (56, "#eb1818"), (58, "#c70714"),
    (60, "#a10913"), (62, "#5c352d"), (64, "#71473f"), (66, "#71473f"), (68, "#865a4e"),
    (70, "#b08279"), (75, "#f1d8cf"), (80, "#ffe9e4"), (85, "#f69a97"),
    (90, "#e55c5f"), (95, "#ca3639"), (100, "#ab2125"),
]
WSPD10M_RANGE = (0.0, 100.0)

MSLP_HPA_LEGEND_STOPS = [
    (960.0, "#5e4fa2"),
    (972.0, "#3288bd"),
    (984.0, "#66c2a5"),
    (996.0, "#abdda4"),
    (1004.0, "#e6f598"),
    (1012.0, "#fee08b"),
    (1020.0, "#fdae61"),
    (1028.0, "#f46d43"),
    (1040.0, "#d53e4f"),
]
MSLP_HPA_COLOR_ANCHORS = list(MSLP_HPA_LEGEND_STOPS)
MSLP_HPA_RANGE = (960.0, 1040.0)

RH_PERCENT_LEVELS = [float(value) for value in range(0, 105, 5)]
RH_PERCENT_COLORS = [
    "#543004",
    "#714107",
    "#8d520b",
    "#a96c1e",
    "#c28634",
    "#d3aa5f",
    "#e2c786",
    "#efdcad",
    "#f6ebcd",
    "#f5f2e8",
    "#e9f2f1",
    "#d0ece8",
    "#b1e1da",
    "#8ad1c6",
    "#64b9ae",
    "#3b9b93",
    "#27827a",
    "#1d675f",
    "#145147",
    "#0d3c31",
]
RH_PERCENT_LEGEND_STOPS = list(zip(RH_PERCENT_LEVELS[:-1], RH_PERCENT_COLORS))
RH_PERCENT_LEGEND_ENTRIES = [
    {
        "value": float(RH_PERCENT_LEVELS[index]),
        "color": color,
        "label": f"{int(RH_PERCENT_LEVELS[index])}-{int(RH_PERCENT_LEVELS[index + 1])}",
    }
    for index, color in enumerate(RH_PERCENT_COLORS)
]
RH_PERCENT_RANGE = (0.0, 100.0)

WSPD300_COLOR_ANCHORS = [
    (0, "#ffffff"),
    (5, "#e1e1e1"),
    (10, "#b3b3b3"),
    (15, "#595959"),
    (20, "#005ac8"),
    (25, "#339bef"),
    (30, "#82cbf6"),
    (35, "#a4eff9"),
    (40, "#00cd41"),
    (45, "#59f36f"),
    (50, "#a4faa6"),
    (55, "#fffaa7"),
    (60, "#ffb944"),
    (65, "#ff9727"),
    (70, "#ff561f"),
    (75, "#eb1818"),
    (80, "#c70714"),
    (90, "#71473f"),
    (100, "#b08279"),
    (110, "#ffe9e4"),
    (120, "#e55c5f"),
    (130, "#ab2125"),
    (140, "#8c184f"),
    (150, "#b82876"),
    (160, "#df52a6"),
    (170, "#f59bd3"),
    (180, "#fccee8"),
    (190, "#fff3fb"),
]
WSPD300_RANGE = (0.0, 190.0)

QPF6H_LEGEND_STOPS = list(zip(precip_levels, precip_colors))
PRECIP_TOTAL_COLOR_ANCHORS = list(zip(precip_levels, precip_colors))

PWAT_LEGEND_STOPS = [
    (0.0, "#3b321d"),
    (0.2, "#686048"),
    (0.4, "#a9a38a"),
    (0.6, "#74aa73"),
    (0.8, "#3b753b"),
    (1.0, "#569aa5"),
    (1.2, "#366369"),
    (1.4, "#1a3233"),
    (1.6, "#4a457d"),
    (1.8, "#332965"),
    (2.0, "#6d3d69"),
    (2.2, "#83526f"),
    (2.4, "#9b6776"),
    (2.6, "#d1a998"),
    (2.8, "#e6cfb8"),
]
PWAT_COLOR_ANCHORS = list(PWAT_LEGEND_STOPS)
PWAT_COLORS = [color for _, color in PWAT_LEGEND_STOPS]

MLCAPE_LEGEND_LEVELS = [
    250.0,
    500.0,
    750.0,
    1000.0,
    1250.0,
    1500.0,
    1750.0,
    2000.0,
    2250.0,
    2500.0,
    2750.0,
    3000.0,
    3250.0,
    3500.0,
    3750.0,
    4000.0,
    4250.0,
    4500.0,
    4750.0,
    5000.0,
    5250.0,
    5500.0,
    5750.0,
    6000.0,
    6250.0,
]
MLCAPE_LEGEND_COLORS = [
    "#1f2379",
    "#2530b5",
    "#2f43ff",
    "#3858ff",
    "#3f6cff",
    "#4c8af6",
    "#5eadef",
    "#68cce7",
    "#6ddfe1",
    "#69f1cf",
    "#64f0a7",
    "#72f27b",
    "#93f55e",
    "#b7fb4e",
    "#d7ff45",
    "#f1fd3b",
    "#ffd52d",
    "#ffb21f",
    "#ff9518",
    "#ff7813",
    "#ff5b0e",
    "#ff3008",
    "#ff1906",
    "#ee0904",
    "#cf0000",
]
MLCAPE_COLOR_ANCHORS = list(zip(MLCAPE_LEGEND_LEVELS, MLCAPE_LEGEND_COLORS))
MLCAPE_RANGE = (0.0, 6250.0)

# 850mb temperature (°C) continuous palette anchors and range
TMP850_COLOR_ANCHORS = [
    (-40.0, "#90d8cb"), (-39.0, "#96cec9"), (-38.0, "#95c5c6"), (-37.0, "#9bc3cb"), (-36.0, "#9ec0cf"),
    (-35.0, "#9bb5cb"), (-34.0, "#9aacc7"), (-33.0, "#9aa2c5"), (-32.0, "#9899c2"), (-31.0, "#9790c0"),
    (-30.0, "#9586bc"), (-29.0, "#967ebb"), (-28.0, "#9475b9"), (-27.0, "#926cb5"), (-26.0, "#9163b3"),
    (-25.0, "#8f5bb1"), (-24.0, "#8e52ad"), (-23.0, "#8d4bab"), (-22.0, "#8b42a8"), (-21.0, "#893ba6"),
    (-20.0, "#8833a3"), (-19.0, "#862ba0"), (-18.0, "#84249e"), (-17.0, "#9a30a1"), (-16.0, "#a143a9"),
    (-15.0, "#a757b1"), (-14.0, "#ae6bb9"), (-13.0, "#b481c2"), (-12.0, "#ba97ca"), (-11.0, "#c0add2"),
    (-10.0, "#c6c5db"), (-9.0, "#cbdde3"), (-8.0, "#d7e8ef"), (-7.0, "#bad3e5"), (-6.0, "#9ebddc"),
    (-5.0, "#80a7d1"), (-4.0, "#6692c8"), (-3.0, "#4c7ebe"), (-2.0, "#336ab6"), (-1.0, "#1758ad"),
    (0.0, "#0045a5"), (1.0, "#004552"), (2.0, "#16565a"), (3.0, "#316864"), (4.0, "#497b6c"),
    (5.0, "#648d75"), (6.0, "#7e9e7b"), (7.0, "#97ae82"), (8.0, "#afbc87"), (9.0, "#d6d38f"),
    (10.0, "#e3d68f"), (11.0, "#dcc887"), (12.0, "#e0c483"), (13.0, "#debb7e"), (14.0, "#d8af75"),
    (15.0, "#cf9e6a"), (16.0, "#c38d5f"), (17.0, "#b57c54"), (18.0, "#a76a49"), (19.0, "#9e5b40"),
    (20.0, "#954d37"), (21.0, "#8a3f2e"), (22.0, "#813326"), (23.0, "#78261d"), (24.0, "#6f1b17"),
    (25.0, "#661111"), (26.0, "#5d0006"), (27.0, "#580005"), (28.0, "#620e14"), (29.0, "#6b161e"),
    (30.0, "#751e2b"), (31.0, "#7e2638"), (32.0, "#6b2f2c"), (33.0, "#844f4a"), (34.0, "#9f716b"), 
    (35.0, "#b9958d"), (36.0, "#d5bab1"), (37.0, "#f1e1d7"), (38.0, "#e5d9d0"), (39.0, "#d3c9c0"), (40.0, "#c1b7b0"),
]
TMP850_RANGE = (-40.0, 40.0)

VORT500_LEGEND_STOPS = [
    (0.5, "#ffffff"),
    (1.0, "#dddddd"),
    (1.5, "#bdbdbd"),
    (2.0, "#9e9e9e"),
    (3.0, "#6fc9c4"),
    (4.0, "#2ed6d4"),
    (5.0, "#00d16f"),
    (6.0, "#58df00"),
    (8.0, "#b5e400"),
    (10.0, "#fff100"),
    (12.0, "#ffd100"),
    (14.0, "#ffb000"),
    (16.0, "#ff8a00"),
    (18.0, "#ff6200"),
    (20.0, "#ff3b00"),
    (22.0, "#e62a00"),
    (25.0, "#c31900"),
    (30.0, "#9f0f00"),
    (35.0, "#8f1a56"),
    (40.0, "#9d2494"),
    (45.0, "#ba2de1"),
    (50.0, "#db3cf3"),
    (55.0, "#ee54f7"),
    (60.0, "#f58bfa"),
    (85.0, "#ffd2f9"),
]
VORT500_COLOR_ANCHORS = list(VORT500_LEGEND_STOPS)
VORT500_COLORS = [color for _, color in VORT500_LEGEND_STOPS]
VORT500_RANGE = (0.0, 85.0)

# Palette/LUT catalog keyed by color_map_id.
# Variable identity/metadata lives in model plugin capabilities.
COLOR_MAP_SPECS: dict[str, dict] = {
    "precip_rain": {
        "type": "discrete",
        "units": "in/hr",
        "levels": PRECIP_CONFIG["rain"]["levels"],
        "colors": PRECIP_CONFIG["rain"]["colors"],
    },
    "precip_frzr": {
        "type": "discrete",
        "units": "in/hr",
        "levels": PRECIP_CONFIG["frzr"]["levels"],
        "colors": PRECIP_CONFIG["frzr"]["colors"],
    },
    "precip_sleet": {
        "type": "discrete",
        "units": "in/hr",
        "levels": PRECIP_CONFIG["sleet"]["levels"],
        "colors": PRECIP_CONFIG["sleet"]["colors"],
    },
    "precip_snow": {
        "type": "discrete",
        "units": "in/hr",
        "levels": PRECIP_CONFIG["snow"]["levels"],
        "colors": PRECIP_CONFIG["snow"]["colors"],
    },
    "radar_ptype": {
        "type": "indexed",
        "units": "dBZ",
        "transparent_zero": True,
        "display_resampling_override": "bilinear",
        "levels": RADAR_PTYPE_LEVELS,
        "colors": RADAR_PTYPE_COLORS,
        "display_name": "Composite Reflectivity + P-Type",
        "legend_title": "Composite Reflectivity + P-Type (dBZ)",
        "ptype_order": list(RADAR_PTYPE_ORDER),
        "ptype_breaks": RADAR_PTYPE_BREAKS,
        "ptype_levels": RADAR_PTYPE_LEVELS_BY_TYPE,
    },
    "radar_ptype_rain": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "dBZ",
        "range": (0.0, 75.0),
        "levels": list(_MODELED_REFL_CONFIG["rain"]["levels"]),
        "colors": list(_MODELED_REFL_CONFIG["rain"]["colors"]),
        "display_name": "Rain",
        "legend_title": "Rain Reflectivity",
        "transparent_below_min": 10.0,
        "display_resampling_override": "bilinear",
        "allow_dry_frame": True,
    },
    "radar_ptype_snow": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "dBZ",
        "range": (0.0, 75.0),
        "levels": list(_MODELED_REFL_CONFIG["snow"]["levels"]),
        "colors": list(_MODELED_REFL_CONFIG["snow"]["colors"]),
        "display_name": "Snow",
        "legend_title": "Snow Reflectivity",
        "transparent_below_min": 10.0,
        "display_resampling_override": "bilinear",
        "allow_dry_frame": True,
    },
    "radar_ptype_sleet": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "dBZ",
        "range": (0.0, 75.0),
        "levels": list(_MODELED_REFL_CONFIG["sleet"]["levels"]),
        "colors": list(_MODELED_REFL_CONFIG["sleet"]["colors"]),
        "display_name": "Sleet",
        "legend_title": "Sleet Reflectivity",
        "transparent_below_min": 10.0,
        "display_resampling_override": "bilinear",
        "allow_dry_frame": True,
    },
    "radar_ptype_frzr": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "dBZ",
        "range": (0.0, 75.0),
        "levels": list(_MODELED_REFL_CONFIG["frzr"]["levels"]),
        "colors": list(_MODELED_REFL_CONFIG["frzr"]["colors"]),
        "display_name": "Freezing Rain",
        "legend_title": "Freezing Rain Reflectivity",
        "transparent_below_min": 10.0,
        "display_resampling_override": "bilinear",
        "allow_dry_frame": True,
    },
    "precip_total": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": (0.0, 25.0),
        "anchors": PRECIP_TOTAL_COLOR_ANCHORS,
        "colors": precip_colors,
        "display_name": "Total Precipitation",
        "legend_title": "Total Precipitation (in)",
        "legend_stops": QPF6H_LEGEND_STOPS,
        "allow_dry_frame": True,
        "transparent_below_min": 0.01,
    },
    "mrms_recent_precip_6h": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": (0.0, 25.0),
        "anchors": PRECIP_TOTAL_COLOR_ANCHORS,
        "colors": precip_colors,
        "display_name": "6-h Recent Precip",
        "legend_title": "6-h Recent Precip (in)",
        "legend_stops": QPF6H_LEGEND_STOPS,
        "allow_dry_frame": True,
        "transparent_below_min": 0.01,
    },
    "mrms_recent_precip_24h": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": (0.0, 25.0),
        "anchors": PRECIP_TOTAL_COLOR_ANCHORS,
        "colors": precip_colors,
        "display_name": "24-h Recent Precip",
        "legend_title": "24-h Recent Precip (in)",
        "legend_stops": QPF6H_LEGEND_STOPS,
        "allow_dry_frame": True,
        "transparent_below_min": 0.01,
    },
    "mrms_recent_precip_72h": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": (0.0, 25.0),
        "anchors": PRECIP_TOTAL_COLOR_ANCHORS,
        "colors": precip_colors,
        "display_name": "72-h Recent Precip",
        "legend_title": "72-h Recent Precip (in)",
        "legend_stops": QPF6H_LEGEND_STOPS,
        "allow_dry_frame": True,
        "transparent_below_min": 0.01,
    },
    "qpf6h": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": (0.0, 6.0),
        "colors": precip_colors,
        "display_name": "6-hr Precip",
        "legend_title": "6-hr Precip (in)",
        "legend_stops": QPF6H_LEGEND_STOPS,
    },
    "pwat": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": (0.0, 3.0),
        "anchors": PWAT_COLOR_ANCHORS,
        "colors": PWAT_COLORS,
        "display_name": "Precipitable Water",
        "legend_title": "Precipitable Water (in)",
        "legend_stops": PWAT_LEGEND_STOPS,
        "allow_dry_frame": True,
        "transparent_below_min": 0.05,
    },
    "ptype_intensity": {
        "type": "indexed",
        "units": "in/hr",
        "transparent_zero": True,
        "levels": GFS_PTYPE_INTENSITY_LEVELS,
        "colors": GFS_PTYPE_INTENSITY_COLORS_FLAT,
        "range": (0.0, 3.0),
        "bins_per_ptype": 0,
        "display_name": "Precipitation Type & Intensity",
        "legend_title": "Precipitation Type",
        "legend_entries": GFS_PTYPE_INTENSITY_LEGEND_ENTRIES,
        "ptype_order": list(GFS_PTYPE_INTENSITY_ORDER),
        "ptype_breaks": GFS_PTYPE_INTENSITY_BREAKS,
        "ptype_levels": GFS_PTYPE_INTENSITY_LEVELS_BY_TYPE,
    },
    "ptype_intensity_rain": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in/hr",
        "range": (0.0, 3.0),
        "levels": list(GFS_PTYPE_INTENSITY_BINS["rain"]),
        "colors": list(GFS_PTYPE_INTENSITY_COLORS["rain"]),
        "display_name": "Rain",
        "legend_title": "Rain",
        "transparent_below_min": 0.01,
        "display_resampling_override": "bilinear",
    },
    "ptype_intensity_snow": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in/hr",
        "range": (0.0, 10.0),
        "levels": list(GFS_PTYPE_INTENSITY_BINS["snow"]),
        "colors": list(GFS_PTYPE_INTENSITY_COLORS["snow"]),
        "display_name": "Snow",
        "legend_title": "Snow",
        "transparent_below_min": 0.01,
        "display_resampling_override": "bilinear",
        "allow_dry_frame": True,
    },
    "ptype_intensity_ice": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in/hr",
        "range": (0.0, 2.0),
        "levels": list(GFS_PTYPE_INTENSITY_BINS["ice"]),
        "colors": list(GFS_PTYPE_INTENSITY_COLORS["ice"]),
        "display_name": "Ice",
        "legend_title": "Ice",
        "transparent_below_min": 0.01,
        "display_resampling_override": "bilinear",
        "allow_dry_frame": True,
    },
    "snowfall_total": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "in",
        "range": SNOWFALL_TOTAL_RANGE,
        "anchors": SNOWFALL_TOTAL_COLOR_ANCHORS,
        "power_norm_gamma": 0.72,
        "display_name": "Total Snowfall (10:1)",
        "legend_title": "Total Snowfall (in)",
        "allow_dry_frame": True,
        "transparent_below_min": 0.1,
    },
    "ice_total": {
        "type": "discrete",
        "units": "in",
        "range": (0.0, 2.0),
        "levels": ICE_TOTAL_LEVELS,
        "colors": ICE_TOTAL_COLORS,
        "display_name": "Total Ice",
        "legend_title": "Total Ice (in)",
        "legend_stops": ICE_TOTAL_LEGEND_STOPS,
        "legend_entries": ICE_TOTAL_LEGEND_ENTRIES,
        "allow_dry_frame": True,
        "transparent_below_min": True,
    },
    "tmp2m": {
        "type": "continuous",
        "units": "F",
        "range": TMP2M_F_RANGE,
        "anchors": TMP2M_F_COLOR_ANCHORS,
        "display_name": "2m Temperature",
        "legend_title": "Temperature (°F)",
    },
    "tmp2m_anom": {
        "type": "discrete",
        "units": "F",
        "range": TMP2M_ANOM_F_RANGE,
        "levels": TMP2M_ANOM_F_LEVELS,
        "colors": TMP2M_ANOM_F_COLORS,
        "display_name": "Surface Temperature Anomaly",
        "legend_title": "Surface Temperature Anomaly (°F)",
        "legend_stops": TMP2M_ANOM_F_LEGEND_STOPS,
        "transparent_below_min": False,
    },
    "tmp850_anom": {
        "type": "discrete",
        "units": "F",
        "range": TMP2M_ANOM_F_RANGE,
        "levels": TMP2M_ANOM_F_LEVELS,
        "colors": TMP2M_ANOM_F_COLORS,
        "display_name": "850mb Temperature Anomaly",
        "legend_title": "850mb Temperature Anomaly (°F)",
        "legend_stops": TMP2M_ANOM_F_LEGEND_STOPS,
        "transparent_below_min": False,
    },
    "hgt500_anom": {
        "type": "discrete",
        "units": "dam",
        "range": HGT500_ANOM_DAM_RANGE,
        "levels": HGT500_ANOM_DAM_LEVELS,
        "colors": HGT500_ANOM_DAM_COLORS,
        "display_name": "500mb Height Anomaly",
        "legend_title": "500mb Height Anomaly (dam)",
        "legend_stops": HGT500_ANOM_DAM_LEGEND_STOPS,
        "display_resampling_override": "bilinear",
        "transparent_below_min": False,
    },
    "precip_anom": {
        "type": "discrete",
        "units": "in",
        "range": PRECIP_ANOM_IN_RANGE,
        "levels": PRECIP_ANOM_IN_LEVELS,
        "colors": PRECIP_ANOM_IN_COLORS,
        "display_name": "Precip Anomaly",
        "legend_title": "Precip Anomaly (in)",
        "legend_stops": PRECIP_ANOM_IN_LEGEND_STOPS,
        "display_resampling_override": "bilinear",
        "transparent_below_min": False,
    },
    "dp2m": {
        "type": "continuous",
        "units": "F",
        "range": TMP2M_F_RANGE,
        "anchors": TMP2M_F_COLOR_ANCHORS,
        "display_name": "2m Dew Point",
        "legend_title": "Dew Point (°F)",
    },
    "rh": {
        "type": "discrete",
        "units": "%",
        "range": RH_PERCENT_RANGE,
        "levels": RH_PERCENT_LEVELS,
        "colors": RH_PERCENT_COLORS,
        "display_name": "Relative Humidity",
        "legend_title": "Relative Humidity (%)",
        "legend_stops": RH_PERCENT_LEGEND_STOPS,
        "legend_entries": RH_PERCENT_LEGEND_ENTRIES,
        "transparent_below_min": False,
    },
    "tmp850": {
        "type": "continuous",
        "units": "C",
        "range": TMP850_RANGE,
        "anchors": TMP850_COLOR_ANCHORS,
        "display_name": "850mb Temperature",
        "legend_title": "850mb Temperature (°C)",
    },
    "vort500": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "10^-5 s^-1",
        "range": VORT500_RANGE,
        "anchors": VORT500_COLOR_ANCHORS,
        "colors": VORT500_COLORS,
        "display_name": "500mb Heights + Vorticity",
        "legend_title": "500mb Heights + Vorticity",
        "legend_stops": VORT500_LEGEND_STOPS,
        "transparent_below_min": 0.5,
        "physical": False,
    },
    "mlcape": {
        "type": "continuous",
        "display_palette_kind": "discrete",
        "units": "J/kg",
        "range": MLCAPE_RANGE,
        "anchors": MLCAPE_COLOR_ANCHORS,
        "display_name": "Mixed-Layer CAPE",
        "legend_title": "Mixed-Layer CAPE (J/kg)",
        "legend_stops": list(zip(MLCAPE_LEGEND_LEVELS, MLCAPE_LEGEND_COLORS)),
        "transparent_below_min": 25.0,
    },
    "wspd10m": {
        "type": "continuous",
        "units": "mph",
        "range": WSPD10M_RANGE,
        "anchors": WSPD10M_COLOR_ANCHORS,
        "display_name": "10m Wind Speed",
        "legend_title": "Wind Speed (mph)",
    },
    "wspd850": {
        "type": "continuous",
        "units": "kt",
        "range": WSPD300_RANGE,
        "anchors": WSPD300_COLOR_ANCHORS,
        "display_name": "850mb Heights & Winds",
        "legend_title": "850mb Wind Speed (kt)",
    },
    "wspd300": {
        "type": "continuous",
        "units": "kt",
        "range": WSPD300_RANGE,
        "anchors": WSPD300_COLOR_ANCHORS,
        "display_name": "300mb Heights & Winds",
        "legend_title": "300mb Wind Speed (kt)",
    },
    "wgst10m": {
        "type": "continuous",
        "units": "mph",
        "range": WSPD10M_RANGE,
        "anchors": WSPD10M_COLOR_ANCHORS,
        "display_name": "10m Wind Gust",
        "legend_title": "Wind Gust (mph)",
    },
    "mslp": {
        "type": "continuous",
        "units": "hPa",
        "range": MSLP_HPA_RANGE,
        "anchors": MSLP_HPA_COLOR_ANCHORS,
        "display_name": "Mean Sea-Level Pressure",
        "legend_title": "Mean Sea-Level Pressure (hPa)",
        "legend_stops": MSLP_HPA_LEGEND_STOPS,
    },
    "spres": {
        "type": "continuous",
        "units": "hPa",
        "range": MSLP_HPA_RANGE,
        "anchors": MSLP_HPA_COLOR_ANCHORS,
        "display_name": "Surface Pressure",
        "legend_title": "Surface Pressure (hPa)",
        "legend_stops": MSLP_HPA_LEGEND_STOPS,
    },
    "refc": {
        "type": "discrete",
        "units": "dBZ",
        # Hide "no precip" / near-noise returns by making <10 dBZ transparent.
        # Keep visible echoes starting at the first non-white radar color.
        "levels": [
            value for value in _MODELED_REFL_PALETTES["rain"][0]
            if value >= 10.0
        ],
        "colors": [
            color
            for value, color in zip(*_MODELED_REFL_PALETTES["rain"], strict=False)
            if value >= 10.0
        ],
        "display_name": "Composite Reflectivity",
        "legend_title": "Reflectivity (dBZ)",
    },
    "mrms_reflectivity": {
        "type": "discrete",
        "units": "dBZ",
        # Keep the MRMS reflectivity palette separate from forecast reflectivity
        # metadata so we can tune the observed product independently later.
        "levels": MRMS_REFLECTIVITY_LEVELS,
        "colors": MRMS_REFLECTIVITY_COLORS,
        "display_name": "Base Reflectivity",
        "legend_title": "MRMS Reflectivity (dBZ)",
        "transparent_below_min": True,
        # MRMS is displayed as a smoothed visual field even though sampling
        # remains on the unsmoothed value raster.
        "display_resampling_override": "bilinear",
    },
    "mrms_radar_ptype": {
        "type": "indexed",
        "units": "dBZ",
        "transparent_zero": True,
        "levels": MRMS_RADAR_PTYPE_LEVELS,
        "colors": MRMS_RADAR_PTYPE_COLORS,
        "display_name": "Reflectivity + Ptype",
        "legend_title": "MRMS Reflectivity + Ptype (dBZ)",
        "ptype_order": list(MRMS_RADAR_PTYPE_ORDER),
        "ptype_breaks": MRMS_RADAR_PTYPE_BREAKS,
        "ptype_levels": MRMS_RADAR_PTYPE_LEVELS_BY_TYPE,
    },
    "goes_ir13_enhanced": {
        "type": "discrete",
        "display_palette_kind": "discrete",
        "units": "C",
        "levels": GOES_IR13_LEVELS,
        "colors": GOES_IR13_COLORS,
        "range": GOES_IR13_RANGE,
        "display_name": "Clean IR",
        "legend_title": "Brightness Temperature",
        "legend_stops": GOES_IR13_LEGEND_STOPS,
        "display_resampling_override": "bilinear",
        "transparent_below_min": False,
    },
}

# Aliases kept for historical compatibility with already-published metadata.
COLOR_MAP_ALIASES: dict[str, str] = {
    "temp_f_-60_120_tmp2m": "tmp2m",
    "wind_mph_0_100": "wspd10m",
    "radar_ptype_v1": "radar_ptype",
}


def get_color_map_spec(color_map_id: str) -> dict:
    mapped_id = COLOR_MAP_ALIASES.get(color_map_id, color_map_id)
    spec = COLOR_MAP_SPECS.get(mapped_id)
    if spec is None:
        raise KeyError(f"Unknown color_map_id: {color_map_id!r}")
    return spec

_LUT_CACHE: dict[str, np.ndarray] = {}


def _fail_if_legacy_builder_with_anchors(
    var_key: str,
    spec: dict,
    *,
    caller: str,
) -> None:
    anchors = spec.get("color_anchors") or spec.get("anchors")
    if anchors:
        raise RuntimeError(
            f"Legacy colormap path '{caller}' does not support anchored continuous specs "
            f"for var '{var_key}'. Use V3 builder colorization/sidecar pipeline instead."
        )


def hex_to_rgba_u8(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    hex_str = hex_color.strip().lstrip("#")
    if len(hex_str) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    a = int(alpha)
    return r, g, b, a


def build_discrete_lut(colors_hex: list[str]) -> np.ndarray:
    if not colors_hex:
        raise ValueError("colors_hex must contain at least one color")
    lut = np.zeros((256, 4), dtype=np.uint8)
    max_idx = min(len(colors_hex), 256)
    for idx in range(max_idx):
        lut[idx] = hex_to_rgba_u8(colors_hex[idx], 255)
    if max_idx < 256:
        lut[max_idx:] = lut[max_idx - 1]
    return lut


def build_continuous_lut(colors_hex: list[str], n: int = 256) -> np.ndarray:
    if len(colors_hex) < 2:
        raise ValueError("colors_hex must contain at least two colors")
    stops = np.array([hex_to_rgba_u8(color, 255)[:3] for color in colors_hex], dtype=float)
    stop_positions = np.linspace(0.0, 1.0, num=len(colors_hex))
    target_positions = np.linspace(0.0, 1.0, num=n)
    r = np.interp(target_positions, stop_positions, stops[:, 0])
    g = np.interp(target_positions, stop_positions, stops[:, 1])
    b = np.interp(target_positions, stop_positions, stops[:, 2])
    a = np.full(n, 255.0)
    lut = np.stack([r, g, b, a], axis=1).astype(np.uint8)
    return lut


def build_continuous_lut_from_stops(
    stops: list[tuple[float, str]],
    n: int = 256,
    *,
    range_vals: tuple[float, float] | None = None,
) -> np.ndarray:
    if len(stops) < 2:
        raise ValueError("stops must contain at least two entries")

    sorted_stops = sorted(stops, key=lambda item: item[0])
    stop_values = np.array([float(value) for value, _ in sorted_stops], dtype=float)
    stop_colors = np.array([
        hex_to_rgba_u8(color, 255)[:3] for _, color in sorted_stops
    ], dtype=float)

    if range_vals is None:
        range_min, range_max = float(stop_values[0]), float(stop_values[-1])
    else:
        range_min, range_max = float(range_vals[0]), float(range_vals[1])

    if range_max == range_min:
        raise ValueError("stop range must not be zero")

    target_values = np.linspace(range_min, range_max, num=n)
    r = np.interp(target_values, stop_values, stop_colors[:, 0])
    g = np.interp(target_values, stop_values, stop_colors[:, 1])
    b = np.interp(target_values, stop_values, stop_colors[:, 2])
    a = np.full(n, 255.0)
    lut = np.stack([r, g, b, a], axis=1).astype(np.uint8)
    return lut


def get_lut(var_key: str) -> np.ndarray:
    """Build runtime LUT for tile rendering.
    
    For discrete vars: maps byte index to color.
    For continuous vars: always interpolates colors array into 256 steps.
    Never uses legend_stops for LUT generation (stops are legend-only).
    """
    if var_key in _LUT_CACHE:
        return _LUT_CACHE[var_key]
    spec = get_color_map_spec(var_key)
    _fail_if_legacy_builder_with_anchors(var_key, spec, caller="get_lut")
    colors = spec["colors"]
    if spec["type"] == "discrete":
        lut = build_discrete_lut(colors)
    else:
        # Continuous: always build from colors array, never from stops
        lut = build_continuous_lut(colors, n=256)
    _LUT_CACHE[var_key] = lut
    return lut


def encode_to_byte_and_alpha(
    values: np.ndarray,
    var_key: str,
) -> tuple[np.ndarray, np.ndarray, dict]:
    spec = get_color_map_spec(var_key)
    _fail_if_legacy_builder_with_anchors(var_key, spec, caller="encode_to_byte_and_alpha")
    kind = spec.get("type")
    if kind not in {"discrete", "continuous"}:
        raise ValueError(f"Unsupported var spec type for {var_key}: {kind}")

    finite_mask = np.isfinite(values)

    if kind == "discrete":
        levels = spec.get("levels")
        colors = spec.get("colors")
        if not levels or not colors:
            raise ValueError(f"Discrete spec for {var_key} must include levels and colors")
        if len(colors) not in {len(levels), len(levels) - 1}:
            raise ValueError(
                f"Discrete spec for {var_key} must have colors length == levels or levels-1 "
                f"(got colors={len(colors)} levels={len(levels)})"
            )

        bins = np.digitize(np.where(finite_mask, values, levels[0]), levels, right=False) - 1
        bins = np.clip(bins, 0, len(colors) - 1).astype(np.uint8)
        transparent_below_min = spec.get("transparent_below_min", True)
        if transparent_below_min:
            valid_mask = finite_mask & (values >= levels[0])
        else:
            valid_mask = finite_mask
        alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
        byte_band = np.where(alpha == 255, bins, 0).astype(np.uint8)

        meta = {
            "var_key": var_key,
            "kind": "discrete",
            "units": spec.get("units"),
            "levels": list(levels),
            "colors": list(colors),
        }
        # Add optional display metadata if present
        if "display_name" in spec:
            meta["display_name"] = spec["display_name"]
        if "legend_title" in spec:
            meta["legend_title"] = spec["legend_title"]
        if "ptype_order" in spec:
            meta["ptype_order"] = list(spec["ptype_order"])
        if "ptype_breaks" in spec:
            meta["ptype_breaks"] = dict(spec["ptype_breaks"])
        if "ptype_levels" in spec:
            meta["ptype_levels"] = {
                str(key): list(values) for key, values in dict(spec["ptype_levels"]).items()
            }
        if "range" in spec:
            range_vals = spec.get("range")
            if isinstance(range_vals, (list, tuple)) and len(range_vals) == 2:
                meta["range"] = [float(range_vals[0]), float(range_vals[1])]
        if "bins_per_ptype" in spec:
            meta["bins_per_ptype"] = int(spec["bins_per_ptype"])
        return byte_band, alpha, meta

    range_vals = spec.get("range")
    if not range_vals or len(range_vals) != 2:
        raise ValueError(f"Continuous spec for {var_key} must include range (min,max)")
    range_min, range_max = range_vals
    if range_max == range_min:
        raise ValueError(f"Continuous spec for {var_key} has invalid range: {range_vals}")

    scale = (values - range_min) / (range_max - range_min)
    scaled = np.clip(np.rint(scale * 255.0), 0, 255).astype(np.uint8)
    alpha = np.where(finite_mask, 255, 0).astype(np.uint8)
    byte_band = np.where(finite_mask, scaled, 0).astype(np.uint8)

    meta = {
        "var_key": var_key,
        "kind": "continuous",
        "units": spec.get("units"),
        "range": [float(range_min), float(range_max)],
        "colors": list(spec.get("colors", [])),
    }
    # Add optional display metadata if present
    if "display_name" in spec:
        meta["display_name"] = spec["display_name"]
    if "legend_title" in spec:
        meta["legend_title"] = spec["legend_title"]
    if "legend_stops" in spec:
        meta["legend_stops"] = [list(item) for item in spec["legend_stops"]]
    return byte_band, alpha, meta
