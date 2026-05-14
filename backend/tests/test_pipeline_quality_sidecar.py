from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder.colorize import float_to_rgba
from app.services.builder import pipeline as pipeline_module
from app.services.colormaps import get_color_map_spec


def test_build_sidecar_defaults_to_full_quality() -> None:
    sidecar = pipeline_module.build_sidecar_json(
        model="hrrr",
        run_id="20260305_17z",
        var_id="snowfall_kuchera_total",
        fh=3,
        run_date=datetime(2026, 3, 5, 17, tzinfo=timezone.utc),
        colorize_meta={"kind": "continuous", "units": "in", "min": 0.0, "max": 10.0},
        var_spec={"type": "continuous", "range": [0.0, 10.0], "colors": ["#000000", "#ffffff"]},
    )

    assert sidecar["quality"] == "full"
    assert sidecar["quality_flags"] == []


def test_build_sidecar_writes_degraded_quality_flags() -> None:
    sidecar = pipeline_module.build_sidecar_json(
        model="hrrr",
        run_id="20260305_17z",
        var_id="snowfall_kuchera_total",
        fh=3,
        run_date=datetime(2026, 3, 5, 17, tzinfo=timezone.utc),
        colorize_meta={"kind": "continuous", "units": "in", "min": 0.0, "max": 10.0},
        var_spec={"type": "continuous", "range": [0.0, 10.0], "colors": ["#000000", "#ffffff"]},
        quality="degraded",
        quality_flags=["slr_fallback_10to1", "apcp_cumulative_fallback"],
    )

    assert sidecar["quality"] == "degraded"
    assert sidecar["quality_flags"] == ["slr_fallback_10to1", "apcp_cumulative_fallback"]


def test_build_sidecar_can_override_display_kind_for_precip() -> None:
    sidecar = pipeline_module.build_sidecar_json(
        model="gfs",
        run_id="20260408_06z",
        var_id="precip_total",
        fh=6,
        run_date=datetime(2026, 4, 8, 6, tzinfo=timezone.utc),
        colorize_meta={"kind": "continuous", "units": "in", "min": 0.0, "max": 25.0},
        var_spec={
            "type": "continuous",
            "display_palette_kind": "discrete",
            "range": [0.0, 25.0],
            "colors": ["#000000", "#ffffff"],
            "legend_stops": [(0.01, "#111111"), (0.1, "#222222")],
        },
    )

    assert sidecar["kind"] == "discrete"
    assert sidecar["legend"]["type"] == "discrete"
    assert sidecar["legend"]["stops"] == [[0.01, "#111111"], [0.1, "#222222"]]


def test_build_sidecar_uses_discrete_display_kind_for_hgt500_anom() -> None:
    sidecar = pipeline_module.build_sidecar_json(
        model="gefs",
        run_id="20260422_06z",
        var_id="hgt500_anom__mean",
        fh=12,
        run_date=datetime(2026, 4, 22, 6, tzinfo=timezone.utc),
        colorize_meta={"kind": "continuous", "units": "dam", "min": -18.0, "max": 24.0},
        var_spec={
            "type": "continuous",
            "display_palette_kind": "discrete",
            "range": [-40.0, 40.0],
            "anchors": [(-40.0, "#081d58"), (0.0, "#f7f3ee"), (40.0, "#67000d")],
        },
    )

    assert sidecar["kind"] == "discrete"
    assert sidecar["legend"]["type"] == "discrete"
    assert sidecar["legend"]["stops"] == [[-40.0, "#081d58"], [0.0, "#f7f3ee"], [40.0, "#67000d"]]


def test_tmp2m_anom_uses_requested_discrete_color_bins() -> None:
    data = np.array([[-31.0, -29.0, -28.0, -0.25, 0.25, 29.0, 30.0]], dtype=np.float32)

    rgba, meta = float_to_rgba(data, "tmp2m_anom")

    assert meta["kind"] == "discrete"
    assert meta["legend_stops"][0] == [-30.0, "#fbcdba"]
    assert meta["legend_stops"][-1] == [28.0, "#66442f"]
    assert rgba[:3, 0, :].T.tolist() == [
        [251, 205, 186],
        [251, 205, 186],
        [248, 186, 197],
        [255, 255, 255],
        [255, 255, 255],
        [102, 68, 47],
        [102, 68, 47],
    ]
    assert rgba[3, 0, :].tolist() == [255, 255, 255, 255, 255, 255, 255]


def test_tmp2m_anom_sidecar_legend_matches_requested_steps() -> None:
    spec = get_color_map_spec("tmp2m_anom")

    sidecar = pipeline_module.build_sidecar_json(
        model="gefs",
        run_id="20260514_00z",
        var_id="tmp2m_anom__mean",
        fh=12,
        run_date=datetime(2026, 5, 14, tzinfo=timezone.utc),
        colorize_meta={"kind": "discrete", "units": "F", "min": -12.0, "max": 9.0},
        var_spec=spec,
    )

    assert sidecar["kind"] == "discrete"
    assert sidecar["legend"]["type"] == "discrete"
    assert len(sidecar["legend"]["stops"]) == 40
    assert sidecar["legend"]["stops"][:4] == [
        [-30.0, "#fbcdba"],
        [-28.0, "#f8bac5"],
        [-24.0, "#f3a8d2"],
        [-20.0, "#eb95dd"],
    ]
    assert sidecar["legend"]["stops"][-4:] == [
        [18.0, "#b27488"],
        [20.0, "#99646c"],
        [24.0, "#80544e"],
        [28.0, "#66442f"],
    ]


def test_hgt500_anom_uses_requested_discrete_color_bins() -> None:
    data = np.array([[-41.0, -37.0, -36.0, -2.0, 2.0, 5.0, 39.0, 40.0]], dtype=np.float32)

    rgba, meta = float_to_rgba(data, "hgt500_anom")

    assert meta["kind"] == "discrete"
    assert meta["legend_stops"][0] == [-40.0, "#061652"]
    assert meta["legend_stops"][-1] == [36.0, "#790378"]
    assert rgba[:3, 0, :].T.tolist() == [
        [6, 22, 82],
        [6, 22, 82],
        [12, 47, 95],
        [255, 255, 255],
        [255, 255, 255],
        [252, 219, 198],
        [121, 3, 120],
        [121, 3, 120],
    ]
    assert rgba[3, 0, :].tolist() == [255, 255, 255, 255, 255, 255, 255, 255]


def test_hgt500_anom_sidecar_legend_matches_requested_steps() -> None:
    spec = get_color_map_spec("hgt500_anom")

    sidecar = pipeline_module.build_sidecar_json(
        model="gefs",
        run_id="20260514_00z",
        var_id="hgt500_anom__mean",
        fh=12,
        run_date=datetime(2026, 5, 14, tzinfo=timezone.utc),
        colorize_meta={"kind": "discrete", "units": "dam", "min": -18.0, "max": 24.0},
        var_spec=spec,
    )

    assert sidecar["kind"] == "discrete"
    assert sidecar["legend"]["type"] == "discrete"
    assert len(sidecar["legend"]["stops"]) == 26
    assert sidecar["legend"]["stops"][:4] == [
        [-40.0, "#061652"],
        [-36.0, "#0c2f5f"],
        [-34.0, "#14437b"],
        [-30.0, "#1c5695"],
    ]
    assert sidecar["legend"]["stops"][-4:] == [
        [26.0, "#b41c2b"],
        [30.0, "#9c1028"],
        [34.0, "#810622"],
        [36.0, "#790378"],
    ]
