"""build_frame per-step timing instrumentation (build audit 3.6).

Prod builds slowed 4-6x and the only per-frame number was an opaque
``elapsed_ms``. These tests pin the two readable outputs: the greppable
``Frame steps:`` log line and the Prometheus sum/count counters.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import prometheus_metrics
from app.services.builder import fetch as fetch_module
from app.services.builder import pipeline as pipeline_module

FRAME_STEP_LINE = re.compile(
    r"Frame steps: model=(?P<model>\S+) region=(?P<region>\S+) var=(?P<var>\S+) "
    r"fh(?P<fh>\d{3}) fetch_ms=(?P<fetch>\d+) warp_ms=(?P<warp>\d+) "
    r"colorize_ms=(?P<colorize>\d+) contour_ms=(?P<contour>\d+) "
    r"write_ms=(?P<write>\d+) total_ms=(?P<total>\d+)"
)


class _Plugin:
    id = "aigfs"
    product = "pres"

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_region(self, region: str):
        return region if region == "na" else None

    def herbie_request(
        self,
        *,
        product: str,
        var_key: str,
        ensemble_view=None,
        run_date=None,
        fh: int,
        search_pattern: str | None = None,
    ):
        del var_key, ensemble_view, run_date, fh, search_pattern
        return SimpleNamespace(model=self.id, product=product, herbie_kwargs=None)


def _stub_build_frame(monkeypatch) -> None:
    """Stub every external step so build_frame runs end to end in-process."""
    var_spec_model = SimpleNamespace(
        id="vort500",
        derived=True,
        derive="vort500_from_uv",
        selectors=SimpleNamespace(hints={"baseline_region": "na", "baseline_source": "era5"}),
        kind="continuous",
    )
    var_capability = SimpleNamespace(
        color_map_id="vort500",
        kind="continuous",
        derive_strategy_id="vort500_from_uv",
        units="10^-5 s^-1",
    )
    intermediate = np.ones((426, 846), dtype=np.float32)
    warped = np.full((657, 682), 7.0, dtype=np.float32)

    monkeypatch.setattr(pipeline_module, "_ensure_products_ready", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *a, **k: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *a, **k: var_capability)
    monkeypatch.setattr(
        pipeline_module,
        "get_color_map_spec",
        lambda color_map_id: {
            "id": color_map_id,
            "type": "continuous",
            "units": "10^-5 s^-1",
            "range": [-20.0, 20.0],
            "colors": ["#000000", "#ffffff"],
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "derive_variable",
        lambda **kwargs: (intermediate, CRS.from_epsg(4326), Affine(0.25, 0.0, -130.0, 0.0, -0.25, 60.0)),
    )
    monkeypatch.setattr(
        pipeline_module,
        "warp_to_target_grid",
        lambda *a, **k: (warped, Affine(25000.0, 0.0, -19814869.36, 0.0, -25000.0, 16967796.94)),
    )
    monkeypatch.setattr(
        pipeline_module,
        "float_to_rgba",
        lambda data, color_map_id, meta_var_key=None: (
            np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "units": "10^-5 s^-1", "min": -1.0, "max": 1.0},
        ),
    )
    monkeypatch.setattr(pipeline_module, "write_grid_frame_for_run_root", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "validate_grid_binary_frame", lambda *a, **k: True)
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(pipeline_module, "check_pre_encode_value_sanity", lambda *a, **k: True)
    monkeypatch.setattr(pipeline_module, "_build_contour_metadata_for_variable", lambda **kwargs: ({}, None))


def _run_frame(tmp_path: Path):
    return pipeline_module.build_frame(
        model="aigfs",
        region="na",
        var_id="vort500",
        fh=96,
        run_date=datetime(2026, 5, 28, 12, 0),
        data_root=tmp_path,
        product="pres",
        model_plugin=_Plugin(),
        derive_component_warp_cache=True,
    )


def test_build_frame_logs_one_greppable_step_line(monkeypatch, caplog, tmp_path: Path) -> None:
    _stub_build_frame(monkeypatch)
    caplog.set_level(logging.INFO, logger=pipeline_module.logger.name)

    assert _run_frame(tmp_path) is not None

    lines = [rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("Frame steps:")]
    assert len(lines) == 1, lines
    match = FRAME_STEP_LINE.fullmatch(lines[0])
    assert match is not None, lines[0]
    assert match.group("model") == "aigfs"
    assert match.group("region") == "na"
    assert match.group("var") == "vort500"
    assert match.group("fh") == "096"
    # Every step is always present, including ones a frame skipped (contours
    # are stubbed out here and still report a value).
    steps = {name: int(match.group(name)) for name in pipeline_module.FRAME_STEPS}
    assert set(steps) == set(pipeline_module.FRAME_STEPS)
    assert all(value >= 0 for value in steps.values())
    # total_ms spans the whole frame, so it cannot be less than its parts.
    assert int(match.group("total")) >= sum(steps.values())


def test_build_frame_increments_prometheus_step_counters(monkeypatch, tmp_path: Path) -> None:
    _stub_build_frame(monkeypatch)
    prometheus_metrics.FRAME_STEP_SECONDS_TOTAL.clear()
    prometheus_metrics.FRAME_STEP_FRAMES_TOTAL.clear()

    assert _run_frame(tmp_path) is not None
    assert _run_frame(tmp_path) is not None

    registry = prometheus_metrics._REGISTRY
    for step in pipeline_module.FRAME_STEPS:
        labels = {"model_id": "aigfs", "region": "na", "step": step}
        frames = registry.get_sample_value("cartosky_frame_step_frames_total", labels)
        seconds = registry.get_sample_value("cartosky_frame_step_seconds_total", labels)
        assert frames == 2.0, step
        assert seconds is not None and seconds >= 0.0, step


def test_build_frame_records_runtime_timers_for_the_snapshot_bridge(monkeypatch, tmp_path: Path) -> None:
    # The scheduler process registers no Prometheus collectors; these timers
    # are what actually reaches the API's /metrics via the fetch-runtime
    # JSON snapshot.
    _stub_build_frame(monkeypatch)
    fetch_module.reset_herbie_runtime_caches_for_tests()

    assert _run_frame(tmp_path) is not None

    timers = fetch_module.get_herbie_runtime_metrics()["timers_ms"]
    for step in pipeline_module.FRAME_STEPS:
        name = f"frame_step_{step}_na_ms"
        assert name in timers, sorted(timers)
        assert timers[name]["count"] == 1
