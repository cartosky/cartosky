from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import pipeline as pipeline_module


class _Plugin:
    id = "aigfs"
    product = "pres"

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_region(self, region: str):
        return region if region == "na" else None

    def herbie_request(self, *, product: str, var_key: str, ensemble_view=None, run_date=None, fh: int, search_pattern: str | None = None):
        del var_key, ensemble_view, run_date, fh, search_pattern
        return SimpleNamespace(model=self.id, product=product, herbie_kwargs=None)


def test_build_frame_rewarps_derived_output_when_cached_component_grid_does_not_match_target(monkeypatch, tmp_path: Path) -> None:
    # Exercises build_frame's retained COG path: opt the model out of the
    # (now default) binary-only substrate.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "gfs,hrrr,nbm,eps,aigfs,ifs")
    plugin = _Plugin()
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
    intermediate_crs = CRS.from_epsg(4326)
    intermediate_transform = Affine(0.25, 0.0, -130.0, 0.0, -0.25, 60.0)
    warped = np.full((657, 682), 7.0, dtype=np.float32)

    monkeypatch.setattr(pipeline_module, "_ensure_products_ready", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *args, **kwargs: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *args, **kwargs: var_capability)
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
        lambda **kwargs: (intermediate, intermediate_crs, intermediate_transform),
    )

    warp_calls = {"count": 0}

    def _fake_warp_to_target_grid(data, src_crs, src_transform, **kwargs):
        del kwargs
        warp_calls["count"] += 1
        assert data.shape == (426, 846)
        assert rasterio.crs.CRS.from_user_input(src_crs) == intermediate_crs
        assert src_transform == intermediate_transform
        return warped, Affine(25000.0, 0.0, -19814869.36, 0.0, -25000.0, 16967796.94)

    monkeypatch.setattr(pipeline_module, "warp_to_target_grid", _fake_warp_to_target_grid)
    monkeypatch.setattr(
        pipeline_module,
        "float_to_rgba",
        lambda data, color_map_id, meta_var_key=None: (
            np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "units": "10^-5 s^-1", "min": -1.0, "max": 1.0},
        ),
    )

    def _fake_write_value_cog(data, path, **kwargs):
        del kwargs
        assert data.shape == (657, 682)
        path.write_bytes(b"value")

    monkeypatch.setattr(pipeline_module, "write_value_cog", _fake_write_value_cog)
    monkeypatch.setattr(pipeline_module, "validate_cog", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_module, "check_value_sanity", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(pipeline_module, "_build_contour_metadata_for_variable", lambda **kwargs: ({}, None))

    result = pipeline_module.build_frame(
        model="aigfs",
        region="na",
        var_id="vort500",
        fh=96,
        run_date=datetime(2026, 5, 28, 12, 0),
        data_root=tmp_path,
        product="pres",
        model_plugin=plugin,
        derive_component_warp_cache=True,
    )

    assert result is not None
    assert warp_calls["count"] == 1