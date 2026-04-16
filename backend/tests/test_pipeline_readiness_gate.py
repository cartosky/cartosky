from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import pipeline as pipeline_module


class _Plugin:
    id = "hrrr"

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)


def test_build_frame_readiness_gate_short_circuits_derived_fetch(monkeypatch, tmp_path: Path) -> None:
    plugin = _Plugin()
    var_spec_model = SimpleNamespace(
        derived=True,
        derive="snowfall_kuchera_total_cumulative",
        selectors=SimpleNamespace(
            hints={
                "kuchera_apcp_product": "sfc",
                "kuchera_profile_product": "prs",
            }
        ),
    )
    var_capability = SimpleNamespace(
        color_map_id="snow_continuous",
        kind="continuous",
        derive_strategy_id="snowfall_kuchera_total_cumulative",
    )

    readiness_calls: list[str] = []
    derive_called = {"value": False}

    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *args, **kwargs: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *args, **kwargs: var_capability)
    monkeypatch.setattr(
        pipeline_module,
        "get_color_map_spec",
        lambda color_map_id: {"id": color_map_id, "type": "continuous", "units": "in", "range": [0.0, 10.0], "colors": ["#000", "#fff"]},
    )

    def _fake_product_ready(*, model_id, product, run_date, fh, herbie_kwargs=None, allow_grib_without_idx=False):
        del model_id, run_date, fh, herbie_kwargs, allow_grib_without_idx
        readiness_calls.append(str(product))
        return str(product) != "sfc"

    monkeypatch.setattr(pipeline_module, "product_hour_has_any_idx", _fake_product_ready)

    def _fake_derive_variable(**kwargs):
        del kwargs
        derive_called["value"] = True
        raise AssertionError("derive_variable should not run when readiness gate fails")

    monkeypatch.setattr(pipeline_module, "derive_variable", _fake_derive_variable)

    result = pipeline_module.build_frame(
        model="hrrr",
        region="conus",
        var_id="snowfall_kuchera_total",
        fh=13,
        run_date=datetime(2026, 3, 5, 17, 0),
        data_root=tmp_path,
        product="sfc",
        model_plugin=plugin,
    )

    assert result is None
    assert derive_called["value"] is False
    assert readiness_calls == ["sfc", "prs"]


def test_build_frame_tmp2m_skips_dead_contour_generation(monkeypatch, tmp_path: Path) -> None:
    plugin = _Plugin()
    var_spec_model = SimpleNamespace(
        id="tmp2m",
        derived=False,
        selectors=SimpleNamespace(hints={}, search=[":TMP:2 m above ground:"]),
        kind="continuous",
    )
    var_capability = SimpleNamespace(
        color_map_id="tmp2m",
        kind="continuous",
        units="F",
    )

    monkeypatch.setattr(pipeline_module, "_ensure_products_ready", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *args, **kwargs: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *args, **kwargs: var_capability)
    monkeypatch.setattr(
        pipeline_module,
        "get_color_map_spec",
        lambda color_map_id: {
            "id": color_map_id,
            "type": "continuous",
            "units": "F",
            "range": [0.0, 100.0],
            "colors": ["#000000", "#ffffff"],
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "fetch_variable",
        lambda **kwargs: (
            np.array([[273.15]], dtype=np.float32),
            "EPSG:4326",
            from_origin(-130.0, 50.0, 1.0, 1.0),
        ),
    )
    monkeypatch.setattr(pipeline_module, "convert_units", lambda data, **kwargs: data)
    monkeypatch.setattr(
        pipeline_module,
        "warp_to_target_grid",
        lambda data, src_crs, src_transform, **kwargs: (data, src_transform),
    )
    monkeypatch.setattr(
        pipeline_module,
        "float_to_rgba",
        lambda data, color_map_id, meta_var_key=None: (
            np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "units": "F", "min": 0.0, "max": 100.0},
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "write_value_cog",
        lambda data, path, **kwargs: path.write_bytes(b"value"),
    )
    monkeypatch.setattr(pipeline_module, "validate_cog", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_module, "check_value_sanity", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: False)

    contour_called = {"value": False}

    def _fail_if_called(**kwargs):
        del kwargs
        contour_called["value"] = True
        raise AssertionError("build_iso_contour_geojson should not be called")

    monkeypatch.setattr(pipeline_module, "build_iso_contour_geojson", _fail_if_called)

    result = pipeline_module.build_frame(
        model="nbm",
        region="conus",
        var_id="tmp2m",
        fh=28,
        run_date=datetime(2026, 3, 5, 17, 0),
        data_root=tmp_path,
        product="co",
        model_plugin=plugin,
    )

    assert result is not None
    assert contour_called["value"] is False

    sidecar_path = tmp_path / "staging" / "nbm" / "20260305_17z" / "tmp2m" / "fh028.json"
    sidecar = json.loads(sidecar_path.read_text())
    assert "contours" not in sidecar
