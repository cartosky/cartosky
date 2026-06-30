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
from app.services.grid import write_grid_frame_for_run_root


class _Plugin:
    id = "gfs"

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_region(self, region: str):
        return region

    def search_patterns_for_var(self, *, var_key: str, fh: int, product: str, var_spec) -> list[str]:
        del var_key, fh, product
        selectors = getattr(var_spec, "selectors", None)
        search = getattr(selectors, "search", None) if selectors is not None else None
        return list(search or [])

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


def _continuous_var_spec(*, allow_dry_frame: bool = False) -> dict[str, object]:
    return {
        "id": "tmp2m",
        "type": "continuous",
        "units": "F",
        "range": [-100.0, 140.0],
        "allow_dry_frame": allow_dry_frame,
    }


def test_pre_encode_value_sanity_rejects_flat_continuous_array() -> None:
    values = np.full((3, 3), 32.0, dtype=np.float32)

    assert (
        pipeline_module.check_pre_encode_value_sanity(
            values,
            _continuous_var_spec(),
            var_spec_model=SimpleNamespace(kind="continuous", units="F"),
            label="test-array",
        )
        is False
    )


def test_pre_encode_value_sanity_allows_configured_dry_frame() -> None:
    values = np.zeros((3, 3), dtype=np.float32)

    assert (
        pipeline_module.check_pre_encode_value_sanity(
            values,
            {
                **_continuous_var_spec(allow_dry_frame=True),
                "levels": [0.0],
            },
            var_spec_model=SimpleNamespace(kind="continuous", units="F"),
            label="test-dry-array",
        )
        is True
    )


def test_validate_grid_binary_frame_accepts_written_frame_and_rejects_size_mismatch(tmp_path: Path) -> None:
    run_root = tmp_path / "staging" / "gfs" / "20260630_00z"
    values = np.array([[32.0, 33.5], [40.0, np.nan]], dtype=np.float32)
    write_grid_frame_for_run_root(
        run_root=run_root,
        model="gfs",
        var="tmp2m",
        fh=0,
        values=values,
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        projection="EPSG:4326",
    )
    frame_path = run_root / "tmp2m" / "grid" / "fh000.l0.u16.bin"
    meta_path = run_root / "tmp2m" / "grid" / "fh000.l0.meta.json"

    assert pipeline_module.validate_grid_binary_frame(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        fh=0,
    )

    frame_path.write_bytes(frame_path.read_bytes() + b"\x00")

    assert (
        pipeline_module.validate_grid_binary_frame(
            frame_path,
            meta_path,
            model="gfs",
            var="tmp2m",
            fh=0,
        )
        is False
    )


def test_build_frame_runs_phase_c_gates_as_parallel_non_authoritative_checks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plugin = _Plugin()
    var_spec_model = SimpleNamespace(
        id="tmp2m",
        derived=False,
        selectors=SimpleNamespace(hints={}, search=[":TMP:2 m above ground:"]),
        kind="continuous",
        units="F",
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
            **_continuous_var_spec(),
            "id": color_map_id,
            "colors": ["#000000", "#ffffff"],
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "fetch_variable",
        lambda **kwargs: (
            np.array([[32.0, 33.0], [34.0, 35.0]], dtype=np.float32),
            "EPSG:4326",
            from_origin(-101.0, 46.0, 1.0, 1.0),
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
            {"kind": "continuous", "units": "F", "min": 32.0, "max": 35.0},
        ),
    )
    monkeypatch.setattr(pipeline_module, "write_value_cog", lambda data, path, **kwargs: path.write_bytes(b"value"))
    monkeypatch.setattr(pipeline_module, "validate_cog", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_module, "check_value_sanity", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(pipeline_module, "_build_contour_metadata_for_variable", lambda **kwargs: ({}, None))

    phase_c_calls: list[str] = []

    def _failing_pre_encode_gate(*args, **kwargs):
        del args, kwargs
        phase_c_calls.append("pre-encode")
        return False

    def _failing_binary_gate(*args, **kwargs):
        del args, kwargs
        phase_c_calls.append("binary")
        return False

    monkeypatch.setattr(pipeline_module, "check_pre_encode_value_sanity", _failing_pre_encode_gate)
    monkeypatch.setattr(pipeline_module, "validate_grid_binary_frame", _failing_binary_gate)

    result = pipeline_module.build_frame(
        model="gfs",
        region="conus",
        var_id="tmp2m",
        fh=0,
        run_date=datetime(2026, 6, 30, 0, 0),
        data_root=tmp_path,
        product="pgrb2.0p25",
        model_plugin=plugin,
    )

    assert result is not None
    assert phase_c_calls == ["pre-encode", "binary"]

    sidecar_path = tmp_path / "staging" / "gfs" / "20260630_00z" / "tmp2m" / "fh000.json"
    assert json.loads(sidecar_path.read_text())["var"] == "tmp2m"
