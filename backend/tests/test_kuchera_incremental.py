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


class _Plugin:
    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_var_capability(self, var_key: str):
        del var_key
        return None

    def get_var(self, var_key: str):
        by_var = {
            "apcp_step": [r":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:$"],
            "tmp850": [":TMP:850 mb:"],
            "tmp700": [":TMP:700 mb:"],
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


def _var_spec(*, rebuild_window_steps: int = 6) -> SimpleNamespace:
    return SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "1",
                "kuchera_levels_hpa": "850,700",
                "kuchera_profile_mode": "simplified",
                "kuchera_use_ptype_gate": "false",
                "kuchera_incremental_rebuild_window_steps": str(int(rebuild_window_steps)),
            }
        )
    )


def _run_case(
    monkeypatch,
    *,
    fh: int,
    step_fhs: list[int],
    apcp_by_fh: dict[int, np.ndarray],
    prior_loader,
    rebuild_window_steps: int = 6,
):
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    tmp_850 = np.full((2, 2), -12.0, dtype=np.float32)
    tmp_700 = np.full((2, 2), -10.0, dtype=np.float32)
    apcp_calls: list[tuple[int, str]] = []

    inventory_by_fh = {
        int(step_fh): f":APCP:surface:{int(step_fh) - 1}-{int(step_fh)} hour acc fcst:"
        for step_fh in step_fhs
    }

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
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        step_fh = int(fh)
        if pattern.startswith(":APCP:surface:"):
            apcp_calls.append((step_fh, pattern))
            data = apcp_by_fh[step_fh]
            meta = {"inventory_line": pattern, "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":TMP:850 mb:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_850, crs, transform, meta) if return_meta else (tmp_850, crs, transform)
        if pattern == ":TMP:700 mb:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_700, crs, transform, meta) if return_meta else (tmp_700, crs, transform)
        raise AssertionError(f"unexpected pattern: {pattern}")

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [inventory_by_fh[int(fh)]],
    )
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: list(step_fhs),
    )
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", prior_loader)

    data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="hrrr",
        var_key="snowfall_kuchera_total",
        product="sfc",
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=int(fh),
        var_spec_model=_var_spec(rebuild_window_steps=rebuild_window_steps),
        var_capability=None,
        model_plugin=_Plugin(),
    )
    return data, apcp_calls, crs, transform


def test_incremental_matches_full_rebuild(monkeypatch) -> None:
    apcp_by_fh = {
        1: np.array([[1.0, 2.0], [0.0, 0.5]], dtype=np.float32),
        2: np.array([[0.5, 1.0], [1.0, 0.0]], dtype=np.float32),
        3: np.array([[2.0, 0.0], [1.0, 1.0]], dtype=np.float32),
    }
    step_fhs = [1, 2, 3]

    no_prior_loader = lambda **kwargs: None
    full_data, _, crs, transform = _run_case(
        monkeypatch,
        fh=3,
        step_fhs=step_fhs,
        apcp_by_fh=apcp_by_fh,
        prior_loader=no_prior_loader,
    )
    fh2_data, _, _, _ = _run_case(
        monkeypatch,
        fh=2,
        step_fhs=step_fhs[:2],
        apcp_by_fh=apcp_by_fh,
        prior_loader=no_prior_loader,
    )
    fh2_internal = (fh2_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, var_key, ctx, grid_cache_key
        if int(fh) == 2:
            return fh2_internal, crs, transform
        return None

    incremental_data, _, _, _ = _run_case(
        monkeypatch,
        fh=3,
        step_fhs=step_fhs,
        apcp_by_fh=apcp_by_fh,
        prior_loader=_prior_loader,
    )

    np.testing.assert_allclose(incremental_data, full_data, rtol=1e-6, atol=1e-6)


def test_incremental_does_not_recompute_full_history_when_prev_exists(monkeypatch, caplog) -> None:
    apcp_by_fh = {
        1: np.full((2, 2), 0.5, dtype=np.float32),
        2: np.full((2, 2), 0.5, dtype=np.float32),
        3: np.full((2, 2), 0.5, dtype=np.float32),
        4: np.full((2, 2), 0.5, dtype=np.float32),
        5: np.full((2, 2), 0.5, dtype=np.float32),
    }
    step_fhs = [1, 2, 3, 4, 5]

    no_prior_loader = lambda **kwargs: None
    fh4_data, _, crs, transform = _run_case(
        monkeypatch,
        fh=4,
        step_fhs=step_fhs[:-1],
        apcp_by_fh=apcp_by_fh,
        prior_loader=no_prior_loader,
    )
    fh4_internal = (fh4_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, var_key, ctx, grid_cache_key
        if int(fh) == 4:
            return fh4_internal, crs, transform
        return None

    with caplog.at_level("INFO"):
        _, apcp_calls, _, _ = _run_case(
            monkeypatch,
            fh=5,
            step_fhs=step_fhs,
            apcp_by_fh=apcp_by_fh,
            prior_loader=_prior_loader,
        )

    assert [fh for fh, _ in apcp_calls] == [5]
    assert "reused_prev_cumulative=true" in caplog.text
    assert "computed_steps=1" in caplog.text


def test_incremental_recovery_uses_bounded_window_when_prev_missing(monkeypatch, caplog) -> None:
    apcp_by_fh = {
        1: np.array([[0.4, 0.2], [0.1, 0.0]], dtype=np.float32),
        2: np.array([[0.6, 0.1], [0.3, 0.2]], dtype=np.float32),
        3: np.array([[0.3, 0.4], [0.2, 0.1]], dtype=np.float32),
        4: np.array([[0.5, 0.0], [0.2, 0.3]], dtype=np.float32),
        5: np.array([[0.7, 0.2], [0.1, 0.4]], dtype=np.float32),
    }
    step_fhs = [1, 2, 3, 4, 5]
    no_prior_loader = lambda **kwargs: None

    full_data, _, _, _ = _run_case(
        monkeypatch,
        fh=5,
        step_fhs=step_fhs,
        apcp_by_fh=apcp_by_fh,
        prior_loader=no_prior_loader,
        rebuild_window_steps=2,
    )

    fh3_data, _, crs, transform = _run_case(
        monkeypatch,
        fh=3,
        step_fhs=step_fhs[:3],
        apcp_by_fh=apcp_by_fh,
        prior_loader=no_prior_loader,
        rebuild_window_steps=2,
    )
    fh3_internal = (fh3_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, var_key, ctx, grid_cache_key
        if int(fh) == 3:
            return fh3_internal, crs, transform
        return None

    with caplog.at_level("INFO"):
        recovered_data, apcp_calls, _, _ = _run_case(
            monkeypatch,
            fh=5,
            step_fhs=step_fhs,
            apcp_by_fh=apcp_by_fh,
            prior_loader=_prior_loader,
            rebuild_window_steps=2,
        )

    np.testing.assert_allclose(recovered_data, full_data, rtol=1e-6, atol=1e-6)
    assert [fh for fh, _ in apcp_calls] == [4, 5]
    assert "computed_steps=2" in caplog.text


def test_incremental_reuse_with_cumulative_apcp_does_not_overcount(monkeypatch) -> None:
    """Regression: when incremental reuse is active and the only available APCP
    window for the final step is a cumulative 0-N field, the derive must NOT
    treat it as a step increment.  Before the fix, ``expected_start_fh`` was
    computed as 0 (from the subset's local index), so the classifier tagged
    the 0-N window as "step" and added the *entire* cumulative precipitation
    on top of ``base_cumulative``, massively over-counting snowfall."""

    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    tmp_850 = np.full((2, 2), -12.0, dtype=np.float32)
    tmp_700 = np.full((2, 2), -10.0, dtype=np.float32)

    # Per-step APCP increments.
    apcp_step = {
        1: np.array([[1.0, 2.0], [0.0, 0.5]], dtype=np.float32),
        2: np.array([[0.5, 1.0], [1.0, 0.0]], dtype=np.float32),
        3: np.array([[2.0, 0.0], [1.0, 1.0]], dtype=np.float32),
    }
    # Cumulative APCP (0-N totals).
    apcp_cumulative = {
        1: apcp_step[1].copy(),
        2: apcp_step[1] + apcp_step[2],
        3: apcp_step[1] + apcp_step[2] + apcp_step[3],
    }
    step_fhs = [1, 2, 3]

    # --- Full rebuild with step windows (ground truth). ---
    no_prior = lambda **kwargs: None
    full_data, _, _, _ = _run_case(
        monkeypatch,
        fh=3,
        step_fhs=step_fhs,
        apcp_by_fh=apcp_step,
        prior_loader=no_prior,
    )

    # --- Build fh2 result for reuse. ---
    fh2_data, _, _, _ = _run_case(
        monkeypatch,
        fh=2,
        step_fhs=step_fhs[:2],
        apcp_by_fh=apcp_step,
        prior_loader=no_prior,
    )
    fh2_internal = (fh2_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)

    # --- Incremental rebuild where APCP is only available as cumulative 0-N. ---
    # We must mock directly (not via _run_case) so inventory reports cumulative.
    cumulative_inventory = {
        1: ":APCP:surface:0-1 hour acc fcst:",
        2: ":APCP:surface:0-2 hour acc fcst:",
        3: ":APCP:surface:0-3 hour acc fcst:",
    }

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, return_meta=False, **kw):
        del model_id, product, run_date, kw
        sfh = int(fh)
        pattern = str(search_pattern)
        if "APCP" in pattern:
            data = apcp_cumulative[sfh]
            meta = {"inventory_line": cumulative_inventory[sfh], "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if "TMP:850" in pattern:
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_850, crs, transform, meta) if return_meta else (tmp_850, crs, transform)
        if "TMP:700" in pattern:
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_700, crs, transform, meta) if return_meta else (tmp_700, crs, transform)
        raise AssertionError(f"unexpected pattern: {pattern}")

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [cumulative_inventory[int(fh)]],
    )
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: list(step_fhs),
    )

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, var_key, ctx, grid_cache_key
        if int(fh) == 2:
            return fh2_internal, crs, transform
        return None

    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _prior_loader)

    incremental_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="hrrr",
        var_key="snowfall_kuchera_total",
        product="sfc",
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=3,
        var_spec_model=_var_spec(),
        var_capability=None,
        model_plugin=_Plugin(),
    )

    # The incremental result must match the full rebuild — not be inflated.
    np.testing.assert_allclose(incremental_data, full_data, rtol=1e-5, atol=1e-5)


def test_incremental_reuse_with_late_cumulative_apcp_rebuilds_from_start(monkeypatch, caplog) -> None:
    """Regression: cumulative APCP that appears after a step window in an
    incremental subset must trigger a full-history rebuild.

    Scenario:
      - Rebuild window selects subset [fh3, fh4] with base anchored at fh2.
      - fh3 inventory is step (2-3), fh4 inventory is cumulative (0-4).
      - If we only difference against the subset state, fh4 is overcounted.
    """

    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    tmp_850 = np.full((2, 2), -12.0, dtype=np.float32)
    tmp_700 = np.full((2, 2), -10.0, dtype=np.float32)
    step_fhs = [1, 2, 3, 4]

    apcp_step = {
        1: np.full((2, 2), 1.0, dtype=np.float32),
        2: np.full((2, 2), 2.0, dtype=np.float32),
        3: np.full((2, 2), 3.0, dtype=np.float32),
        4: np.full((2, 2), 4.0, dtype=np.float32),
    }
    apcp_cumulative = {
        1: apcp_step[1].copy(),
        2: apcp_step[1] + apcp_step[2],
        3: apcp_step[1] + apcp_step[2] + apcp_step[3],
        4: apcp_step[1] + apcp_step[2] + apcp_step[3] + apcp_step[4],
    }

    no_prior_loader = lambda **kwargs: None
    full_data, _, _, _ = _run_case(
        monkeypatch,
        fh=4,
        step_fhs=step_fhs,
        apcp_by_fh=apcp_step,
        prior_loader=no_prior_loader,
        rebuild_window_steps=2,
    )
    fh2_data, _, _, _ = _run_case(
        monkeypatch,
        fh=2,
        step_fhs=step_fhs[:2],
        apcp_by_fh=apcp_step,
        prior_loader=no_prior_loader,
        rebuild_window_steps=2,
    )
    fh2_internal = (fh2_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)

    inventory_by_fh = {
        1: ":APCP:surface:0-1 hour acc fcst:",
        2: ":APCP:surface:1-2 hour acc fcst:",
        3: ":APCP:surface:2-3 hour acc fcst:",
        4: ":APCP:surface:0-4 hour acc fcst:",
    }
    apcp_calls: list[int] = []

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
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        step_fh = int(fh)
        if pattern.startswith(":APCP:surface:"):
            apcp_calls.append(step_fh)
            data = apcp_cumulative[step_fh] if step_fh == 4 else apcp_step[step_fh]
            meta = {"inventory_line": inventory_by_fh[step_fh], "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":TMP:850 mb:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_850, crs, transform, meta) if return_meta else (tmp_850, crs, transform)
        if pattern == ":TMP:700 mb:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_700, crs, transform, meta) if return_meta else (tmp_700, crs, transform)
        raise AssertionError(f"unexpected pattern: {pattern}")

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, var_key, ctx, grid_cache_key
        if int(fh) == 2:
            return fh2_internal, crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [inventory_by_fh[int(fh)]],
    )
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: list(step_fhs),
    )
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _prior_loader)

    with caplog.at_level("INFO"):
        incremental_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
            model_id="hrrr",
            var_key="snowfall_kuchera_total",
            product="sfc",
            run_date=datetime(2026, 3, 5, 17, 0),
            fh=4,
            var_spec_model=_var_spec(rebuild_window_steps=2),
            var_capability=None,
            model_plugin=_Plugin(),
        )

    np.testing.assert_allclose(incremental_data, full_data, rtol=1e-5, atol=1e-5)
    assert 1 in apcp_calls and 2 in apcp_calls
    assert "cumulative_apcp_requires_full_rebuild" in caplog.text
