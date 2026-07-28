"""Phase 3 perceptual gate for the tmp2m ramp, plus the dew-point guard.

Gate source: docs/plans/2026-07-28-map-viewer-redesign-phase-3-colormap.md (Task 2).
The sRGB -> CIELAB conversion below is self-contained (D65, 2 degree observer)
so no color library dependency is added; delta-E is CIE76.

Thresholds approved by Brian 2026-07-28 at the Task 1 gate: sliding-min
delta-E >= 18 across every 10 F pair in 70-95, L* span >= 20 as a
preservation floor. The perceptual test is
expected RED against the current production ramp (worst pair 70v80 = 13.4).
"""
from __future__ import annotations

import pytest

from app.services.colormaps import COLOR_MAP_SPECS

# Approved thresholds (Brian, 2026-07-28 Task 1 gate).
MIN_SLIDING_10F_DELTA_E = 18.0
MIN_LSTAR_SPAN_70_95 = 20.0

# ---------------------------------------------------------------------------
# Self-contained sRGB -> CIELAB (D65) and CIE76 delta-E.
# ---------------------------------------------------------------------------

def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


_WHITE = (0.95047, 1.0, 1.08883)  # D65


def _f(t: float) -> float:
    return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29


def hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in _hex_to_rgb01(hex_color))
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    fx, fy, fz = _f(x / _WHITE[0]), _f(y / _WHITE[1]), _f(z / _WHITE[2])
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(hex_a: str, hex_b: str) -> float:
    la, lb = hex_to_lab(hex_a), hex_to_lab(hex_b)
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


def _color_at(anchors: list[tuple[float, str]], value: float) -> str:
    """Linear-in-value lookup on the anchor list (exact hit or interpolation)."""
    pairs = sorted((float(v), str(c)) for v, c in anchors)
    for (v0, c0), (v1, c1) in zip(pairs, pairs[1:]):
        if v0 <= value <= v1:
            if value == v0:
                return c0
            if value == v1:
                return c1
            t = (value - v0) / (v1 - v0)
            rgb0 = _hex_to_rgb01(c0)
            rgb1 = _hex_to_rgb01(c1)
            mixed = tuple(round((p + (q - p) * t) * 255) for p, q in zip(rgb0, rgb1))
            return "#{:02x}{:02x}{:02x}".format(*mixed)
    raise AssertionError(f"value {value} outside anchor range")


# ---------------------------------------------------------------------------
# Dew-point guard: dp2m must keep the pre-Phase-3 ramp verbatim. Pinned from
# the shared TMP2M_F_COLOR_ANCHORS list as of main @ bc348d3b.
# ---------------------------------------------------------------------------
DP2M_PINNED_ANCHORS = [
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
    (115, "#5d5e62"), (116, "#595a5f"), (117, "#4f5155"), (118, "#46474a"), (119, "#3f4042"),
    (120, "#2f2f31"),
]


def test_dp2m_ramp_is_unchanged() -> None:
    actual = [(int(v), str(c).lower()) for v, c in COLOR_MAP_SPECS["dp2m"]["anchors"]]
    expected = [(int(v), str(c).lower()) for v, c in DP2M_PINNED_ANCHORS]
    assert actual == expected, "dp2m ramp changed -- Phase 3 must not recolor dew point"


# ---------------------------------------------------------------------------
# Perceptual gate on tmp2m: every 10 F pair in 70-95 must separate.
# ---------------------------------------------------------------------------

def test_tmp2m_70_95_band_10f_separation() -> None:
    anchors = COLOR_MAP_SPECS["tmp2m"]["anchors"]
    sliding = {v: delta_e(_color_at(anchors, v), _color_at(anchors, v + 10)) for v in range(70, 86)}
    worst_value = min(sliding, key=sliding.get)
    assert sliding[worst_value] >= MIN_SLIDING_10F_DELTA_E, (
        f"10F pair {worst_value}v{worst_value + 10} has delta-E "
        f"{sliding[worst_value]:.1f} < {MIN_SLIDING_10F_DELTA_E} -- the 70-95 F band "
        "is not distinguishable at 10 F steps"
    )


def test_tmp2m_70_95_band_lightness_span() -> None:
    anchors = COLOR_MAP_SPECS["tmp2m"]["anchors"]
    lstars = [hex_to_lab(_color_at(anchors, v))[0] for v in range(70, 96)]
    span = max(lstars) - min(lstars)
    assert span >= MIN_LSTAR_SPAN_70_95, (
        f"L* span across 70-95 F is {span:.1f} < {MIN_LSTAR_SPAN_70_95}"
    )


# ---------------------------------------------------------------------------
# Cross-repo pin: the frontend e2e fixture is a copy of the backend anchors
# (the e2e suite stubs its own capabilities/sidecars from it). This test ties
# the two copies together so a palette change on either side breaks loudly.
# ---------------------------------------------------------------------------
import re
from pathlib import Path

_FRONTEND_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "tests" / "e2e" / "viewer-colormap.fixtures.ts"
)


def test_frontend_colormap_fixture_matches_backend_anchors() -> None:
    source = _FRONTEND_FIXTURE.read_text()
    block = source.split("TMP2M_APPROVED_LEGEND_ENTRIES", 1)[1]
    block = block.split("];", 1)[0]
    fixture = [
        (int(value), color.lower())
        for value, color in re.findall(r"\{ value: (-?\d+), color: \"(#[0-9a-fA-F]{6})\" \}", block)
    ]
    backend = [(int(v), str(c).lower()) for v, c in COLOR_MAP_SPECS["tmp2m"]["anchors"]]
    assert fixture == backend, (
        "frontend/tests/e2e/viewer-colormap.fixtures.ts TMP2M_APPROVED_LEGEND_ENTRIES "
        "no longer matches backend TMP2M_F_COLOR_ANCHORS -- regenerate the fixture"
    )
