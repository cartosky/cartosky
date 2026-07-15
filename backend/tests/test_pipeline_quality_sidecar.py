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


def test_derive_quality_sidecar_metadata_includes_flag_details() -> None:
    metadata = pipeline_module._derive_quality_sidecar_metadata(
        {
            "sidecar_metadata": {"existing": "value"},
            "quality_flag_details": {
                "accum_step_gap": {"affected_pixel_percentage": 12.5}
            },
        }
    )

    assert metadata == {
        "existing": "value",
        "quality_flag_details": {
            "accum_step_gap": {"affected_pixel_percentage": 12.5}
        },
    }


def test_build_sidecar_preserves_pressure_center_metadata() -> None:
    centers = [
        {
            "type": "L",
            "lat": 39.1,
            "lon": -97.2,
            "value": 996,
            "units": "hPa",
            "source": "mslp",
        }
    ]
    sidecar = pipeline_module.build_sidecar_json(
        model="gfs",
        run_id="20260305_18z",
        var_id="ptype_intensity",
        fh=12,
        run_date=datetime(2026, 3, 5, 18, tzinfo=timezone.utc),
        colorize_meta={"kind": "indexed", "units": "in/hr", "min": 0.0, "max": 1.0},
        var_spec={"type": "indexed", "colors": ["#000000", "#ffffff"]},
        extra_metadata={"pressure_centers": centers},
    )

    assert sidecar["pressure_centers"] == centers


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
        colorize_meta={"kind": "continuous", "units": "m", "min": -180.0, "max": 240.0},
        var_spec={
            "type": "continuous",
            "display_palette_kind": "discrete",
            "range": [-400.0, 400.0],
            "anchors": [(-400.0, "#081d58"), (0.0, "#f7f3ee"), (400.0, "#67000d")],
        },
    )

    assert sidecar["kind"] == "discrete"
    assert sidecar["legend"]["type"] == "discrete"
    assert sidecar["legend"]["stops"] == [[-400.0, "#081d58"], [0.0, "#f7f3ee"], [400.0, "#67000d"]]


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
    data = np.array([[-410.0, -370.0, -360.0, -20.0, 20.0, 50.0, 390.0, 400.0]], dtype=np.float32)

    rgba, meta = float_to_rgba(data, "hgt500_anom")

    assert meta["kind"] == "discrete"
    assert meta["legend_stops"][0] == [-440.0, "#aaabab"]
    assert meta["legend_stops"][-1] == [420.0, "#c5a5c2"]
    assert rgba[:3, 0, :].T.tolist() == [
        [147, 157, 157],
        [65, 118, 80],
        [26, 99, 56],
        [255, 255, 255],
        [255, 255, 255],
        [253, 247, 161],
        [198, 134, 181],
        [198, 134, 181],
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
        colorize_meta={"kind": "discrete", "units": "m", "min": -180.0, "max": 240.0},
        var_spec=spec,
    )

    assert sidecar["kind"] == "discrete"
    assert sidecar["legend"]["type"] == "discrete"
    assert len(sidecar["legend"]["stops"]) == 70
    assert sidecar["legend"]["stops"][:4] == [
        [-440.0, "#aaabab"],
        [-420.0, "#939d9d"],
        [-400.0, "#53866e"],
        [-380.0, "#417650"],
    ]
    assert sidecar["legend"]["stops"][-4:] == [
        [360.0, "#cd5897"],
        [380.0, "#c686b5"],
        [400.0, "#c686b5"],
        [420.0, "#c5a5c2"],
    ]
