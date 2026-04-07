from __future__ import annotations

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

from app.services.builder import derive as derive_module


def test_precip_total_mixed_cadence_uses_hourly_then_6hourly_steps(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    seen_fhs: list[int] = []

    def _fake_fetch_component(**kwargs):
        fh = int(kwargs["fh"])
        var_key = str(kwargs["var_key"])
        return_meta = bool(kwargs.get("return_meta", False))
        assert var_key == "apcp_step"
        seen_fhs.append(fh)
        data = np.ones((2, 2), dtype=np.float32)
        if return_meta:
            return data, crs, transform, {"search_pattern": "", "inventory_line": "", "fh": fh}
        return data, crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", lambda **kwargs: [])

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "step_transition_fh": "36",
                "step_hours_after_fh": "6",
                "step_hours_after_fh_align_to_cycle": "true",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="nbm",
        var_key="precip_total",
        product="co",
        run_date=datetime(2026, 3, 2, 0, 0),
        fh=42,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert seen_fhs == [*range(1, 37), 42]
    # 37 steps of 1 kg/m^2 (== 1 mm) converted to inches.
    expected_inches = 37.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_inventory_cumulative_differencing_prevents_gfs_overcount(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        data_by_pattern = {
            ":APCP:surface:0-3 hour acc fcst:$": np.full((2, 2), 3.0, dtype=np.float32),
            ":APCP:surface:0-6 hour acc fcst:$": np.full((2, 2), 6.0, dtype=np.float32),
        }
        data = data_by_pattern[pattern]
        inventory_line = {
            ":APCP:surface:0-3 hour acc fcst:$": ":APCP:surface:0-3 hour acc fcst:",
            ":APCP:surface:0-6 hour acc fcst:$": ":APCP:surface:0-6 hour acc fcst:",
        }[pattern]
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {
            3: [":APCP:surface:0-3 hour acc fcst:"],
            6: [":APCP:surface:0-6 hour acc fcst:"],
        }[int(fh)]

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "3",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="gfs",
        var_key="precip_total",
        product="pgrb2.0p25",
        run_date=datetime(2026, 3, 2, 0, 0),
        fh=6,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_patterns == [
        "3::APCP:surface:0-3 hour acc fcst:$",
        "6::APCP:surface:0-6 hour acc fcst:$",
    ]
    expected_inches = 6.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_incremental_reuses_prior_cumulative_for_gfs_late_step(monkeypatch, caplog) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        assert pattern == ":APCP:surface:0-6 hour acc fcst:$"
        data = np.full((2, 2), 6.0, dtype=np.float32)
        meta = {"inventory_line": ":APCP:surface:0-6 hour acc fcst:", "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {6: [":APCP:surface:0-6 hour acc fcst:"]}[int(fh)]

    def _fake_prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key, scale_divisor=0.03937007874015748):
        del model_id, run_date, ctx, grid_cache_key, scale_divisor
        if str(var_key) == "precip_total" and int(fh) == 3:
            return np.full((2, 2), 3.0, dtype=np.float32), crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _fake_prior_loader)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "3",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    with caplog.at_level("INFO"):
        data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
            model_id="gfs",
            var_key="precip_total",
            product="pgrb2.0p25",
            run_date=datetime(2026, 3, 2, 0, 0),
            fh=6,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            model_plugin=object(),
        )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_patterns == ["6::APCP:surface:0-6 hour acc fcst:$"]
    assert "precip_total incremental model=gfs" in caplog.text
    assert "computed_steps=1" in caplog.text
    assert "reused_prev_cumulative=true" in caplog.text
    expected_inches = 6.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_gfs_late_cadence_uses_exact_6hour_step_windows(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        data_by_pattern = {
            ":APCP:surface:306-312 hour acc fcst:$": np.full((2, 2), 6.0, dtype=np.float32),
            ":APCP:surface:312-318 hour acc fcst:$": np.full((2, 2), 6.0, dtype=np.float32),
        }
        data = data_by_pattern[pattern]
        meta = {
            "inventory_line": pattern[:-1],
            "search_pattern": pattern,
            "fh": int(fh),
        }
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {318: [":APCP:surface:312-318 hour acc fcst:"]}[int(fh)]

    def _fake_prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key, scale_divisor=0.03937007874015748):
        del model_id, run_date, ctx, grid_cache_key, scale_divisor
        if str(var_key) == "precip_total" and int(fh) == 312:
            return np.full((2, 2), 6.0, dtype=np.float32), crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _fake_prior_loader)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="gfs",
        var_key="precip_total",
        product="pgrb2.0p25",
        run_date=datetime(2026, 4, 7, 12, 0),
        fh=318,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_patterns == [
        "318::APCP:surface:312-318 hour acc fcst:$",
    ]
    expected_inches = 12.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_inventory_prefers_gfs_cumulative_over_overlap_window(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        data_by_pattern = {
            ":APCP:surface:0-3 hour acc fcst:$": np.full((2, 2), 3.0, dtype=np.float32),
            ":APCP:surface:0-6 hour acc fcst:$": np.full((2, 2), 6.0, dtype=np.float32),
            ":APCP:surface:6-9 hour acc fcst:$": np.full((2, 2), 3.0, dtype=np.float32),
            ":APCP:surface:0-12 hour acc fcst:$": np.full((2, 2), 12.0, dtype=np.float32),
        }
        data = data_by_pattern[pattern]
        inventory_line = {
            ":APCP:surface:0-3 hour acc fcst:$": ":APCP:surface:0-3 hour acc fcst:",
            ":APCP:surface:0-6 hour acc fcst:$": ":APCP:surface:0-6 hour acc fcst:",
            ":APCP:surface:6-9 hour acc fcst:$": ":APCP:surface:6-9 hour acc fcst:",
            ":APCP:surface:0-12 hour acc fcst:$": ":APCP:surface:0-12 hour acc fcst:",
        }[pattern]
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {
            3: [":APCP:surface:0-3 hour acc fcst:"],
            6: [":APCP:surface:0-6 hour acc fcst:"],
            9: [":APCP:surface:6-9 hour acc fcst:", ":APCP:surface:0-9 hour acc fcst:"],
            12: [":APCP:surface:6-12 hour acc fcst:", ":APCP:surface:0-12 hour acc fcst:"],
        }[int(fh)]

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "3",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="gfs",
        var_key="precip_total",
        product="pgrb2.0p25",
        run_date=datetime(2026, 3, 2, 0, 0),
        fh=12,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_patterns == [
        "3::APCP:surface:0-3 hour acc fcst:$",
        "6::APCP:surface:0-6 hour acc fcst:$",
        "9::APCP:surface:6-9 hour acc fcst:$",
        "12::APCP:surface:0-12 hour acc fcst:$",
    ]
    expected_inches = 12.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_inventory_differences_nam_overlap_windows(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        data_by_pattern = {
            ":APCP:surface:0-1 hour acc fcst:$": 1.0,
            ":APCP:surface:0-2 hour acc fcst:$": 2.0,
            ":APCP:surface:0-3 hour acc fcst:$": 3.0,
            ":APCP:surface:3-4 hour acc fcst:$": 1.0,
            ":APCP:surface:3-5 hour acc fcst:$": 3.0,
            ":APCP:surface:3-6 hour acc fcst:$": 6.0,
        }
        inventory_line = {
            ":APCP:surface:0-1 hour acc fcst:$": ":APCP:surface:0-1 hour acc fcst:",
            ":APCP:surface:0-2 hour acc fcst:$": ":APCP:surface:0-2 hour acc fcst:",
            ":APCP:surface:0-3 hour acc fcst:$": ":APCP:surface:0-3 hour acc fcst:",
            ":APCP:surface:3-4 hour acc fcst:$": ":APCP:surface:3-4 hour acc fcst:",
            ":APCP:surface:3-5 hour acc fcst:$": ":APCP:surface:3-5 hour acc fcst:",
            ":APCP:surface:3-6 hour acc fcst:$": ":APCP:surface:3-6 hour acc fcst:",
        }[pattern]
        data = np.full((2, 2), data_by_pattern[pattern], dtype=np.float32)
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {
            1: [":APCP:surface:0-1 hour acc fcst:"],
            2: [":APCP:surface:0-2 hour acc fcst:"],
            3: [":APCP:surface:0-3 hour acc fcst:"],
            4: [":APCP:surface:3-4 hour acc fcst:"],
            5: [":APCP:surface:3-5 hour acc fcst:"],
            6: [":APCP:surface:3-6 hour acc fcst:"],
        }[int(fh)]

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="nam",
        var_key="precip_total",
        product="conusnest.hiresf",
        run_date=datetime(2026, 3, 2, 0, 0),
        fh=6,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    expected_inches = 9.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_incremental_reuses_prior_overlap_bucket_window(monkeypatch, caplog) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []
    step_fhs = [1, 2, 3, 4, 5, 6]

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        data_by_pattern = {
            ":APCP:surface:0-1 hour acc fcst:$": 1.0,
            ":APCP:surface:0-2 hour acc fcst:$": 2.0,
            ":APCP:surface:0-3 hour acc fcst:$": 3.0,
            ":APCP:surface:3-4 hour acc fcst:$": 1.0,
            ":APCP:surface:3-5 hour acc fcst:$": 3.0,
            ":APCP:surface:3-6 hour acc fcst:$": 6.0,
        }
        inventory_line = pattern[:-1]
        data = np.full((2, 2), data_by_pattern[pattern], dtype=np.float32)
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {6: [":APCP:surface:3-6 hour acc fcst:"]}[int(fh)]

    def _fake_prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key, scale_divisor=0.03937007874015748):
        del model_id, run_date, ctx, grid_cache_key, scale_divisor
        if str(var_key) == "precip_total" and int(fh) == 5:
            return np.full((2, 2), 6.0, dtype=np.float32), crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _fake_prior_loader)
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: [int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)],
    )
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    with caplog.at_level("INFO"):
        data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
            model_id="nam",
            var_key="precip_total",
            product="conusnest.hiresf",
            run_date=datetime(2026, 4, 7, 18, 0),
            fh=6,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            model_plugin=object(),
        )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_patterns == [
        "6::APCP:surface:3-6 hour acc fcst:$",
        "5::APCP:surface:3-5 hour acc fcst:$",
    ]
    assert "retrying full rebuild" not in caplog.text
    assert "computed_steps=1" in caplog.text
    assert "reused_prev_cumulative=true" in caplog.text
    expected_inches = 9.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_incremental_nam_overlap_uses_seeded_prior_exact_window(monkeypatch, caplog) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        data_by_pattern = {
            ":APCP:surface:3-4 hour acc fcst:$": 1.0,
            ":APCP:surface:3-5 hour acc fcst:$": 3.0,
        }
        inventory_line = {
            ":APCP:surface:3-4 hour acc fcst:$": ":APCP:surface:3-4 hour acc fcst:",
            ":APCP:surface:3-5 hour acc fcst:$": ":APCP:surface:3-5 hour acc fcst:",
        }[pattern]
        data = np.full((2, 2), data_by_pattern[pattern], dtype=np.float32)
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        return {5: [":APCP:surface:3-5 hour acc fcst:"]}[int(fh)]

    def _fake_prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key, scale_divisor=0.03937007874015748):
        del model_id, run_date, ctx, grid_cache_key, scale_divisor
        if str(var_key) == "precip_total" and int(fh) == 4:
            return np.full((2, 2), 4.0, dtype=np.float32), crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _fake_prior_loader)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    with caplog.at_level("INFO"):
        data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
            model_id="nam",
            var_key="precip_total",
            product="conusnest.hiresf",
            run_date=datetime(2026, 4, 7, 18, 0),
            fh=5,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            model_plugin=object(),
        )

    assert out_crs == crs
    assert out_transform == transform
    assert fetch_patterns == [
        "5::APCP:surface:3-5 hour acc fcst:$",
        "4::APCP:surface:3-4 hour acc fcst:$",
    ]
    assert "retrying full rebuild" not in caplog.text
    assert "computed_steps=1" in caplog.text
    assert "reused_prev_cumulative=true" in caplog.text
    expected_inches = 6.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_nbm_late_step_prefers_exact_36_to_42_window_and_ignores_invalid_cache(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        if int(fh) < 36:
            data = np.full((2, 2), 1.0, dtype=np.float32)
            inventory_line = f":APCP:surface:{int(fh) - 1}-{int(fh)} hour acc fcst:"
        elif int(fh) == 36:
            data = np.full((2, 2), 1.0, dtype=np.float32)
            inventory_line = ":APCP:surface:35-36 hour acc fcst:"
        else:
            assert pattern == ":APCP:surface:36-42 hour acc fcst:$"
            data = np.full((2, 2), 6.0, dtype=np.float32)
            inventory_line = ":APCP:surface:36-42 hour acc fcst:"
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        if int(fh) < 36:
            return [f":APCP:surface:{int(fh) - 1}-{int(fh)} hour acc fcst:"]
        if int(fh) == 36:
            return [
                ":APCP:surface:35-36 hour acc fcst:prob >0.254:prob fcst 255/255",
                ":APCP:surface:35-36 hour acc fcst:",
            ]
        return [
            ":APCP:surface:36-42 hour acc fcst:prob >0.254:prob fcst 255/255",
            ":APCP:surface:30-42 hour acc fcst:prob >0.254:prob fcst 255/255",
            ":APCP:surface:41-42 hour acc fcst:",
            ":APCP:surface:36-42 hour acc fcst:",
        ]

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "step_transition_fh": "36",
                "step_hours_after_fh": "6",
                "step_hours_after_fh_align_to_cycle": "true",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")
    class _Plugin:
        def normalize_var_id(self, var_id: str) -> str:
            return var_id

        def get_var_capability(self, var_id: str):
            del var_id
            return None

        def get_var(self, var_id: str):
            assert var_id == "apcp_step"
            return SimpleNamespace(
                selectors=SimpleNamespace(
                    search=[
                        ":APCP:surface:[0-9]+-[0-9]+ hour acc@\\(fcst,dt=[0-9]+ hour\\),missing=0:$",
                        ":APCP:surface:[0-9]+-[0-9]+ hour acc@\\(fcst,dt=[0-9]+ hour\\):$",
                        ":APCP:surface:[0-9]+-[0-9]+ hour acc fcst:$",
                    ],
                    filter_by_keys={
                        "shortName": "apcp",
                        "typeOfLevel": "surface",
                    },
                    hints={
                        "upstream_var": "apcp",
                        "cf_var": "apcp",
                        "short_name": "apcp",
                    },
                )
            )

    plugin = _Plugin()
    ctx = derive_module.FetchContext(coverage="conus")
    run_date = datetime(2026, 3, 2, 0, 0)
    cache_key = (
        "nbm",
        "co",
        run_date.isoformat(),
        42,
        "apcp_step",
        derive_module._selector_fingerprint(plugin.get_var("apcp_step").selectors),
        "conus",
        "",
    )
    ctx.fetch_cache[cache_key] = (np.full((2, 2), 1.0, dtype=np.float32), crs, transform)
    ctx.fetch_meta_cache[cache_key] = {
        "inventory_line": ":APCP:surface:41-42 hour acc fcst:",
        "search_pattern": ":APCP:surface:[0-9]+-[0-9]+ hour acc fcst:$",
        "fh": 42,
    }

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="nbm",
        var_key="precip_total",
        product="co",
        run_date=run_date,
        fh=42,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=plugin,
        ctx=ctx,
    )

    assert out_crs == crs
    assert out_transform == transform
    assert any(pattern.endswith(":APCP:surface:36-42 hour acc fcst:$") for pattern in fetch_patterns)
    assert not any("prob" in pattern for pattern in fetch_patterns)
    expected_inches = 42.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_precip_total_nbm_off_cycle_uses_shifted_late_windows(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    fetch_patterns: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        fetch_patterns.append(f"{int(fh)}:{pattern}")
        if int(fh) <= 36:
            data = np.full((2, 2), 1.0, dtype=np.float32)
            inventory_line = f":APCP:surface:{int(fh) - 1}-{int(fh)} hour acc fcst:"
        elif int(fh) == 39:
            assert pattern == ":APCP:surface:33-39 hour acc fcst:$"
            data = np.full((2, 2), 6.0, dtype=np.float32)
            inventory_line = ":APCP:surface:33-39 hour acc fcst:"
        else:
            assert int(fh) == 45
            assert pattern == ":APCP:surface:39-45 hour acc fcst:$"
            data = np.full((2, 2), 6.0, dtype=np.float32)
            inventory_line = ":APCP:surface:39-45 hour acc fcst:"
        meta = {"inventory_line": inventory_line, "search_pattern": pattern, "fh": int(fh)}
        if return_meta:
            return data, crs, transform, meta
        return data, crs, transform

    def _fake_inventory_lines(*, model_id, product, run_date, fh, search_pattern):
        del model_id, product, run_date, search_pattern
        if int(fh) <= 36:
            return [f":APCP:surface:{int(fh) - 1}-{int(fh)} hour acc fcst:"]
        if int(fh) == 39:
            return [
                ":APCP:surface:38-39 hour acc fcst:",
                ":APCP:surface:33-39 hour acc fcst:",
            ]
        return [
            ":APCP:surface:44-45 hour acc fcst:",
            ":APCP:surface:39-45 hour acc fcst:",
        ]

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_kuchera_inventory_lines", _fake_inventory_lines)
    monkeypatch.setattr(derive_module, "_fetch_component", lambda **kwargs: (_ for _ in ()).throw(AssertionError("selector fallback should not be used")))

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "step_transition_fh": "36",
                "step_hours_after_fh": "6",
                "step_hours_after_fh_align_to_cycle": "true",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="kgm2_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="nbm",
        var_key="precip_total",
        product="co",
        run_date=datetime(2026, 3, 11, 9, 0),
        fh=45,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert any(pattern.endswith(":APCP:surface:33-39 hour acc fcst:$") for pattern in fetch_patterns)
    assert any(pattern.endswith(":APCP:surface:39-45 hour acc fcst:$") for pattern in fetch_patterns)
    assert not any(pattern.endswith(":APCP:surface:36-42 hour acc fcst:$") for pattern in fetch_patterns)
    expected_inches = 45.0 * 0.03937007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)


def test_snowfall_total_mixed_cadence_uses_hourly_then_6hourly_steps(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    seen_fhs: list[int] = []

    def _fake_fetch_component(**kwargs):
        fh = int(kwargs["fh"])
        var_key = str(kwargs["var_key"])
        assert var_key == "asnow_step"
        seen_fhs.append(fh)
        return np.ones((2, 2), dtype=np.float32), crs, transform

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "asnow_step",
                "step_hours": "1",
                "step_transition_fh": "36",
                "step_hours_after_fh": "6",
            }
        )
    )
    var_capability = SimpleNamespace(conversion="m_to_in")

    data, out_crs, out_transform = derive_module._derive_precip_total_cumulative(
        model_id="nbm",
        var_key="snowfall_total",
        product="co",
        run_date=datetime(2026, 3, 2, 0, 0),
        fh=42,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert seen_fhs == [*range(1, 37), 42]
    # 37 steps of 1 meter converted to inches.
    expected_inches = 37.0 * 39.37007874015748
    np.testing.assert_allclose(data, np.full((2, 2), expected_inches, dtype=np.float32), rtol=1e-6, atol=1e-6)
