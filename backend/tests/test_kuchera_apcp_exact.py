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


_APCP_SELECTOR_REGEX = r":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:$"


class _KucheraPlugin:
    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def herbie_request(self, *, product: str | None = None, var_key: str | None = None, run_date=None, fh: int | None = None, search_pattern: str | None = None):
        del var_key, run_date, fh, search_pattern
        return SimpleNamespace(model="test", product=product, herbie_kwargs=None)

    def get_var_capability(self, var_key: str):
        del var_key
        return None

    def get_var(self, var_key: str):
        by_var = {
            "apcp_step": [_APCP_SELECTOR_REGEX],
            "tmp850": [":TMP:850 mb:"],
            "rh850": [":RH:850 mb:"],
        }
        search = by_var.get(str(var_key))
        if search is None:
            return None
        return SimpleNamespace(
            selectors=SimpleNamespace(
                search=search,
                filter_by_keys={},
                hints={},
            )
        )

    def search_patterns_for_var(self, *, var_key: str, fh: int | None = None, product: str | None = None, var_spec=None):
        del fh, product, var_spec
        resolved = self.get_var(var_key)
        if resolved is None:
            return []
        return list(getattr(resolved.selectors, "search", []) or [])


def _kuchera_var_spec() -> SimpleNamespace:
    return SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "kuchera_levels_hpa": "850",
                "kuchera_require_rh": "true",
                "kuchera_min_levels": "1",
            }
        )
    )


def test_kuchera_apcp_tries_exact_pattern_first(monkeypatch) -> None:
    plugin = _KucheraPlugin()
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    calls: list[str] = []
    exact_pattern = derive_module._apcp_exact_window_pattern(0, 1)

    def _fake_fetch_variable(
        *,
        model_id,
        product,
        search_pattern,
        run_date,
        fh,
        herbie_kwargs=None,
        return_meta=False,
    ):
        del model_id, product, run_date, fh, herbie_kwargs
        pattern = str(search_pattern)
        pattern_no_anchor = pattern[:-1] if pattern.endswith("$") else pattern
        calls.append(pattern)
        if pattern == exact_pattern or pattern_no_anchor == exact_pattern.rstrip("$"):
            data = np.full((2, 2), 1.25, dtype=np.float32)
            meta = {"inventory_line": pattern_no_anchor, "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == _APCP_SELECTOR_REGEX:
            raise AssertionError("selector regex fallback should not be used when exact APCP succeeds")
        if pattern == ":TMP:850 mb:":
            data = np.full((2, 2), -12.0, dtype=np.float32)
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":RH:850 mb:":
            data = np.full((2, 2), 90.0, dtype=np.float32)
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        raise AssertionError(f"unexpected search_pattern: {pattern}")

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [exact_pattern],
    )

    data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="gfs",
        var_key="snowfall_kuchera_total",
        product="pgrb2.0p25",
        run_date=datetime(2026, 3, 4, 0, 0),
        fh=1,
        var_spec_model=_kuchera_var_spec(),
        var_capability=None,
        model_plugin=plugin,
    )

    assert calls
    apcp_calls = [c for c in calls if "APCP" in c]
    assert apcp_calls and apcp_calls[0] == exact_pattern
    assert _APCP_SELECTOR_REGEX not in calls
    assert np.isfinite(data).all()


def test_kuchera_apcp_falls_back_once_when_exact_has_no_inventory(monkeypatch, caplog) -> None:
    plugin = _KucheraPlugin()
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    calls: list[str] = []
    exact_pattern = derive_module._apcp_exact_window_pattern(0, 1)

    def _fake_fetch_variable(
        *,
        model_id,
        product,
        search_pattern,
        run_date,
        fh,
        herbie_kwargs=None,
        return_meta=False,
    ):
        del model_id, product, run_date, fh, herbie_kwargs
        pattern = str(search_pattern)
        pattern_no_anchor = pattern[:-1] if pattern.endswith("$") else pattern
        calls.append(pattern)
        if pattern == exact_pattern or pattern_no_anchor == exact_pattern.rstrip("$"):
            # Force selector fallback: exact payload with empty inventory metadata.
            data = np.full((2, 2), 1.0, dtype=np.float32)
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == _APCP_SELECTOR_REGEX:
            data = np.full((2, 2), 1.0, dtype=np.float32)
            meta = {"inventory_line": ":APCP:surface:0-1 hour acc fcst:", "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":TMP:850 mb:":
            data = np.full((2, 2), -12.0, dtype=np.float32)
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":RH:850 mb:":
            data = np.full((2, 2), 90.0, dtype=np.float32)
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        raise AssertionError(f"unexpected search_pattern: {pattern}")

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [exact_pattern],
    )

    with caplog.at_level("INFO"):
        data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
            model_id="gfs",
            var_key="snowfall_kuchera_total",
            product="pgrb2.0p25",
            run_date=datetime(2026, 3, 4, 0, 0),
            fh=1,
            var_spec_model=_kuchera_var_spec(),
            var_capability=None,
            model_plugin=plugin,
        )

    apcp_calls = [c for c in calls if "APCP" in c]
    assert apcp_calls and apcp_calls[0] == exact_pattern
    assert calls.count(_APCP_SELECTOR_REGEX) == 1
    assert "selector_fallback=true" in caplog.text
    assert "exact_guess_used=true" in caplog.text
    assert 'reason="inventory_exact_match_invalid_result"' in caplog.text
    assert np.isfinite(data).all()


def test_resolve_apcp_step_data_exact_match_uses_explicit_target_grid_warp(monkeypatch) -> None:
    plugin = _KucheraPlugin()
    raw_crs = CRS.from_epsg(4326)
    raw_transform = Affine.identity()
    warped_transform = Affine.translation(1000.0, 2000.0)
    exact_pattern = derive_module._apcp_exact_window_pattern(0, 1)
    raw_data = np.arange(16, dtype=np.float32).reshape(4, 4)
    warped_data = np.arange(4, dtype=np.float32).reshape(2, 2)

    def _fake_fetch_variable(
        *,
        model_id,
        product,
        search_pattern,
        run_date,
        fh,
        herbie_kwargs=None,
        return_meta=False,
    ):
        del model_id, product, run_date, fh, herbie_kwargs
        pattern = str(search_pattern)
        meta = {"inventory_line": exact_pattern.rstrip("$"), "search_pattern": pattern}
        return (raw_data, raw_crs, raw_transform, meta) if return_meta else (raw_data, raw_crs, raw_transform)

    def _fake_warp_component_to_target_grid(*, raw_data, raw_crs, raw_transform, model_id, target_region, target_grid_id, resampling):
        assert model_id == "nam"
        assert target_region == "conus"
        assert target_grid_id == "climatology:era5:conus:25000.0m"
        assert resampling == "bilinear"
        np.testing.assert_array_equal(raw_data, np.arange(16, dtype=np.float32).reshape(4, 4))
        assert raw_crs == CRS.from_epsg(4326)
        assert raw_transform == Affine.identity()
        return warped_data, warped_transform

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [exact_pattern],
    )
    monkeypatch.setattr(derive_module, "_warp_component_to_target_grid", _fake_warp_component_to_target_grid)

    ctx = derive_module.FetchContext(coverage="conus")
    step_data, apcp_valid, step_crs, step_transform, step_mode = derive_module._resolve_apcp_step_data(
        step_fh=1,
        step_index=0,
        step_fhs=[1],
        model_id="nam",
        product="conusnest.hiresf",
        run_date=datetime(2026, 5, 28, 18, 0),
        model_plugin=plugin,
        ctx=ctx,
        apcp_component="apcp_step",
        apcp_product=None,
        use_warped=True,
        target_region="conus",
        target_grid_id="climatology:era5:conus:25000.0m",
        resampling="bilinear",
        cum_diff_state=derive_module._ApcpCumDiffState(),
    )

    np.testing.assert_array_equal(step_data, warped_data)
    np.testing.assert_array_equal(apcp_valid, np.isfinite(warped_data) & (warped_data >= 0.0))
    assert step_crs == CRS.from_epsg(3857)
    assert step_transform == warped_transform
    assert step_mode == "exact_step"
