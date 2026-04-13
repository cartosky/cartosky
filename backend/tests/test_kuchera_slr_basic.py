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


def test_kuchera_ratio_formula_warm_branch() -> None:
    max_t_k = np.array([[273.16]], dtype=np.float32)
    ratio = derive_module._kuchera_ratio_from_maxt_low500_k(max_t_k)
    np.testing.assert_allclose(ratio, np.array([[8.0]], dtype=np.float32), rtol=0.0, atol=1e-6)


def test_kuchera_ratio_formula_cold_branch() -> None:
    max_t_k = np.array([[270.16]], dtype=np.float32)
    ratio = derive_module._kuchera_ratio_from_maxt_low500_k(max_t_k)
    np.testing.assert_allclose(ratio, np.array([[13.0]], dtype=np.float32), rtol=0.0, atol=1e-6)


def test_kuchera_ratio_formula_clamps_to_bounds() -> None:
    max_t_k = np.array([[300.0, 230.0]], dtype=np.float32)
    ratio = derive_module._kuchera_ratio_from_maxt_low500_k(max_t_k)
    np.testing.assert_allclose(
        ratio,
        np.array([[5.0, 30.0]], dtype=np.float32),
        rtol=0.0,
        atol=1e-6,
    )


def test_kuchera_maxt_uses_remaining_levels_when_one_missing() -> None:
    levels = [850, 700, 600, 500]
    temp_stack = [
        np.full((2, 2), -12.0, dtype=np.float32),
        np.full((2, 2), -9.0, dtype=np.float32),
        np.full((2, 2), -11.0, dtype=np.float32),
        np.full((2, 2), -15.0, dtype=np.float32),
    ]

    max_t_k = derive_module._kuchera_maxt_low500_from_temp_stack_k(temp_stack)
    np.testing.assert_allclose(max_t_k, np.full((2, 2), 264.15, dtype=np.float32), rtol=0.0, atol=1e-4)

    slr = derive_module._compute_kuchera_slr(
        levels_hpa=levels,
        temp_stack_c=temp_stack,
    )
    assert slr.dtype == np.float32
    np.testing.assert_allclose(slr, np.full((2, 2), 19.01, dtype=np.float32), rtol=0.0, atol=1e-3)


def test_kuchera_slr_does_not_require_rh_inputs() -> None:
    levels = [925, 850, 700]
    temp_stack = [
        np.full((2, 2), -8.0, dtype=np.float32),
        np.full((2, 2), -10.0, dtype=np.float32),
        np.full((2, 2), -12.0, dtype=np.float32),
    ]

    slr = derive_module._compute_kuchera_slr(
        levels_hpa=levels,
        temp_stack_c=temp_stack,
    )

    assert slr.dtype == np.float32
    assert np.isfinite(slr).all()


def test_kuchera_surface_temp_cap_tapers_near_freezing() -> None:
    step_slr = np.full((1, 5), 20.0, dtype=np.float32)
    surface_temp_f = np.array([[29.0, 31.0, 32.0, 33.0, 35.0]], dtype=np.float32)
    surface_temp_c = (surface_temp_f - 32.0) * np.float32(5.0 / 9.0)

    capped, applied_mask, cap_ratio = derive_module._apply_kuchera_surface_temp_slr_cap(
        step_slr,
        surface_temp_c,
        cold_threshold_f=30.0,
        warm_threshold_f=34.0,
        cold_cap_ratio=18.0,
        warm_cap_ratio=10.0,
    )

    np.testing.assert_allclose(
        capped,
        np.array([[20.0, 16.0, 14.0, 12.0, 10.0]], dtype=np.float32),
        rtol=0.0,
        atol=1e-6,
    )
    np.testing.assert_array_equal(
        applied_mask,
        np.array([[False, True, True, True, True]]),
    )
    assert np.isnan(cap_ratio[0, 0])


def test_kuchera_surface_temp_cap_limits_step_ratio_in_derive(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    apcp = np.full((2, 2), 1.0, dtype=np.float32)
    tmp850 = np.full((2, 2), -12.0, dtype=np.float32)
    tmp2m = np.full((2, 2), 0.0, dtype=np.float32)
    exact_apcp_pattern = derive_module._apcp_exact_window_pattern(0, 1)

    def _fake_fetch_component(**kwargs):
        product = str(kwargs["product"])
        var_key = str(kwargs["var_key"])
        fh = int(kwargs["fh"])
        return_meta = bool(kwargs.get("return_meta", False))
        if var_key == "tmp850":
            if return_meta:
                return tmp850, crs, transform, {"inventory_line": "", "product": product, "fh": fh}
            return tmp850, crs, transform
        if var_key == "tmp2m":
            if return_meta:
                return tmp2m, crs, transform, {"inventory_line": "", "product": product, "fh": fh}
            return tmp2m, crs, transform
        raise AssertionError(f"unexpected component {var_key}")

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [exact_apcp_pattern.rstrip("$")],
    )

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
        if pattern == exact_apcp_pattern or pattern_no_anchor == exact_apcp_pattern.rstrip("$"):
            meta = {"inventory_line": pattern_no_anchor, "search_pattern": pattern}
            return (apcp, crs, transform, meta) if return_meta else (apcp, crs, transform)
        raise AssertionError(f"unexpected search_pattern: {pattern}")

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "kuchera_profile_product": "prs",
                "kuchera_levels_hpa": "850",
                "kuchera_use_surface_temp_cap": "true",
                "kuchera_surface_temp_cap_cold_f": "30",
                "kuchera_surface_temp_cap_warm_f": "34",
                "kuchera_surface_temp_cap_cold_ratio": "18",
                "kuchera_surface_temp_cap_warm_ratio": "10",
            }
        )
    )

    data, out_crs, out_transform = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="hrrr",
        var_key="snowfall_kuchera_total",
        product="sfc",
        run_date=datetime(2026, 3, 4, 20, 0),
        fh=1,
        var_spec_model=var_spec_model,
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    expected = np.full((2, 2), 14.0 * 0.03937007874015748, dtype=np.float32)
    np.testing.assert_allclose(data, expected, rtol=0.0, atol=1e-6)


def test_kuchera_simplified_profile_prefers_925_over_500() -> None:
    levels = [925, 850, 700, 600, 500]

    selected = derive_module._kuchera_select_profile_levels(levels, simplified=True)

    assert selected == [925, 850, 700, 600]


def test_kuchera_can_use_distinct_profile_product_without_rh_fetch(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    apcp = np.full((2, 2), 1.0, dtype=np.float32)
    temp = np.full((2, 2), -12.0, dtype=np.float32)
    exact_apcp_pattern = derive_module._apcp_exact_window_pattern(0, 1)

    seen_products: list[tuple[str, str, int]] = []

    def _fake_fetch_component(**kwargs):
        product = str(kwargs["product"])
        var_key = str(kwargs["var_key"])
        fh = int(kwargs["fh"])
        return_meta = bool(kwargs.get("return_meta", False))
        seen_products.append((product, var_key, fh))
        if var_key == "apcp_step":
            if return_meta:
                return apcp, crs, transform, {"inventory_line": ":APCP:surface:0-1 hour acc fcst:"}
            return apcp, crs, transform
        if var_key.startswith("tmp"):
            if return_meta:
                return temp, crs, transform, {"inventory_line": ""}
            return temp, crs, transform
        raise AssertionError(f"unexpected component {var_key}")

    monkeypatch.setattr(derive_module, "_fetch_component", _fake_fetch_component)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [exact_apcp_pattern.rstrip("$")],
    )

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
        if pattern == exact_apcp_pattern or pattern_no_anchor == exact_apcp_pattern.rstrip("$"):
            meta = {"inventory_line": pattern_no_anchor, "search_pattern": pattern}
            return (apcp, crs, transform, meta) if return_meta else (apcp, crs, transform)
        raise AssertionError(f"unexpected search_pattern: {pattern}")

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "kuchera_profile_product": "prs",
                "kuchera_levels_hpa": "925,850,700,600",
            }
        )
    )
    data, out_crs, out_transform = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="hrrr",
        var_key="snowfall_kuchera_total",
        product="sfc",
        run_date=datetime(2026, 3, 4, 20, 0),
        fh=1,
        var_spec_model=var_spec_model,
        var_capability=None,
        model_plugin=object(),
    )

    assert out_crs == crs
    assert out_transform == transform
    assert np.isfinite(data).all()
    temp_fetches = [(product, var_key, fh) for product, var_key, fh in seen_products if var_key.startswith("tmp")]
    assert temp_fetches
    assert all(product == "prs" for product, _, _ in temp_fetches)
    assert ("prs", "tmp925", 1) in temp_fetches
    assert ("prs", "tmp850", 1) in temp_fetches
    assert ("prs", "tmp700", 1) in temp_fetches
    assert ("prs", "tmp600", 1) in temp_fetches
    assert not any(var_key == "tmp500" for _, var_key, _ in temp_fetches)
    assert len(temp_fetches) <= 4
    assert not any(var_key.startswith("rh") for _, var_key, _ in seen_products)


def test_kuchera_can_use_direct_cumulative_sf_source(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    sf_cumulative = {
        3: np.full((1, 1), 0.01, dtype=np.float32),
        6: np.full((1, 1), 0.03, dtype=np.float32),
    }
    temp_850 = np.full((1, 1), -12.0, dtype=np.float32)
    temp_700 = np.full((1, 1), -10.0, dtype=np.float32)

    class _Plugin:
        def normalize_var_id(self, var_key: str) -> str:
            return str(var_key)

        def get_var_capability(self, var_key: str):
            del var_key
            return None

        def get_var(self, var_key: str):
            by_var = {
                "sf": [":sf:sfc:"],
                "tmp850": [":t:850:pl:"],
                "tmp700": [":t:700:pl:"],
            }
            search = by_var.get(str(var_key))
            if search is None:
                return None
            return SimpleNamespace(
                selectors=SimpleNamespace(search=search, filter_by_keys={}, hints={})
            )

    def _fake_fetch_step_component(**kwargs):
        var_key = str(kwargs["var_key"])
        step_fh = int(kwargs["step_fh"])
        return_meta = bool(kwargs.get("return_meta", False))
        if var_key == "sf":
            data = sf_cumulative[step_fh]
        elif var_key == "tmp850":
            data = temp_850
        elif var_key == "tmp700":
            data = temp_700
        else:
            raise AssertionError(f"unexpected component {var_key}")
        if return_meta:
            return data, crs, transform, {"inventory_line": f":{var_key}:"}
        return data, crs, transform

    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(derive_module, "_prefetch_components_parallel", lambda tasks, ctx, *, label="": len(tasks))
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: [step_fh for step_fh in [3, 6] if step_fh <= int(fh)],
    )
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", lambda **kwargs: None)

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "kuchera_lwe_component": "sf",
                "kuchera_lwe_component_scale": "1000",
                "step_hours": "3",
                "kuchera_levels_hpa": "850,700",
                "kuchera_profile_mode": "simplified",
                "kuchera_use_ptype_gate": "false",
            }
        )
    )

    data, out_crs, out_transform = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="ecmwf",
        var_key="snowfall_kuchera_total",
        product="oper",
        run_date=datetime(2026, 4, 13, 0, 0),
        fh=6,
        var_spec_model=var_spec_model,
        var_capability=None,
        model_plugin=_Plugin(),
    )

    expected_ratio = derive_module._compute_kuchera_slr(
        levels_hpa=[850, 700],
        temp_stack_c=[temp_850, temp_700],
    )
    expected = (
        np.full((1, 1), 30.0, dtype=np.float32)
        * expected_ratio
        * np.float32(0.03937007874015748)
    ).astype(np.float32)

    assert out_crs == crs
    assert out_transform == transform
    np.testing.assert_allclose(data, expected, rtol=0.0, atol=1e-5)
