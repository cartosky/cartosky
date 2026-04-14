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
    def __init__(self, extra_search: dict[str, list[str]] | None = None) -> None:
        self._by_var = {
            "apcp_step": [r":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:$"],
            "tmp850": [":TMP:850 mb:"],
            "tmp700": [":TMP:700 mb:"],
        }
        if extra_search:
            self._by_var.update({str(key): list(value) for key, value in extra_search.items()})

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_var_capability(self, var_key: str):
        del var_key
        return None

    def get_var(self, var_key: str):
        search = self._by_var.get(str(var_key))
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

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        run_date: datetime | None = None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ):
        del var_key, run_date, fh, search_pattern
        return SimpleNamespace(product=product, herbie_kwargs={})


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
        lambda *, hints, fh, run_date=None, default_step_hours=6: [
            int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)
        ],
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


def test_incremental_prefetches_profile_temps_for_active_subset(monkeypatch) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    apcp_by_fh = {
        1: np.full((2, 2), 0.5, dtype=np.float32),
        2: np.full((2, 2), 0.5, dtype=np.float32),
        3: np.full((2, 2), 0.5, dtype=np.float32),
    }
    prefetched: list[tuple[int, str, str]] = []

    def _fake_prefetch(tasks, ctx, *, label=""):
        del ctx, label
        prefetched.extend((int(task.fh), str(task.product), str(task.var_key)) for task in tasks)
        return len(tasks)

    inventory_by_fh = {
        int(step_fh): f":APCP:surface:{int(step_fh) - 1}-{int(step_fh)} hour acc fcst:"
        for step_fh in [1, 2, 3]
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
            data = apcp_by_fh[step_fh]
            meta = {"inventory_line": pattern, "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":TMP:850 mb:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            data = np.full((2, 2), -12.0, dtype=np.float32)
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":TMP:700 mb:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            data = np.full((2, 2), -10.0, dtype=np.float32)
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        raise AssertionError(f"unexpected pattern: {pattern}")

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, var_key, ctx, grid_cache_key
        if int(fh) == 2:
            return np.full((2, 2), 1.0 / 0.03937007874015748, dtype=np.float32), crs, transform
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
        lambda *, hints, fh, run_date=None, default_step_hours=6: [1, 2, 3],
    )
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _prior_loader)
    monkeypatch.setattr(derive_module, "_prefetch_components_parallel", _fake_prefetch)

    ctx = derive_module.FetchContext(coverage="conus")
    derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="hrrr",
        var_key="snowfall_kuchera_total",
        product="sfc",
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=3,
        var_spec_model=_var_spec(),
        var_capability=None,
        model_plugin=_Plugin(),
        ctx=ctx,
    )

    assert prefetched == [
        (3, "sfc", "tmp850"),
        (3, "sfc", "tmp700"),
    ]


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
        lambda *, hints, fh, run_date=None, default_step_hours=6: [
            int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)
        ],
    )

    prior_precip_fh2 = apcp_cumulative[2].astype(np.float32, copy=False)

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, ctx, grid_cache_key
        if int(fh) == 2:
            if str(var_key) == "precip_total":
                return prior_precip_fh2, crs, transform
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


def test_incremental_reuse_with_late_cumulative_apcp_stays_incremental(monkeypatch, caplog) -> None:
    """Regression: cumulative APCP that appears after a step window in an
    incremental subset should reuse the carried precip baseline, not rebuild.

        Scenario:
            - Rebuild window selects subset [fh3, fh4] with base anchored at fh2.
            - fh3 inventory is step (2-3), fh4 inventory is cumulative (0-4).
            - The carried precip_total baseline at fh2 should let the subset stay incremental.
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

    prior_precip_fh2 = apcp_cumulative[2].astype(np.float32, copy=False)

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, ctx, grid_cache_key
        if int(fh) == 2:
            if str(var_key) == "precip_total":
                return prior_precip_fh2, crs, transform
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
        lambda *, hints, fh, run_date=None, default_step_hours=6: [
            int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)
        ],
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
    assert apcp_calls == [3, 4]
    assert "cumulative_apcp_requires_full_rebuild" not in caplog.text
    assert "base_fh=002" in caplog.text
    assert "computed_steps=2" in caplog.text


def test_direct_cumulative_kuchera_reuses_prior_sf_tail_window(monkeypatch, caplog) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    tmp_925 = np.full((2, 2), -10.0, dtype=np.float32)
    tmp_850 = np.full((2, 2), -12.0, dtype=np.float32)
    tmp_700 = np.full((2, 2), -10.0, dtype=np.float32)
    tmp_600 = np.full((2, 2), -8.0, dtype=np.float32)
    tmp2m = np.full((2, 2), 28.0, dtype=np.float32)
    pres_sfc = np.full((2, 2), 100000.0, dtype=np.float32)
    step_fhs = [3, 6, 9, 12]

    sf_cumulative = {
        3: np.full((2, 2), 1.0, dtype=np.float32),
        6: np.full((2, 2), 3.0, dtype=np.float32),
        9: np.full((2, 2), 6.0, dtype=np.float32),
        12: np.full((2, 2), 10.0, dtype=np.float32),
    }
    sf_calls: list[int] = []

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
        if pattern in {":sf:sfc:", ":sf:"}:
            sf_calls.append(step_fh)
            meta = {"inventory_line": "", "search_pattern": pattern}
            data = sf_cumulative[step_fh]
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        if pattern == ":t:925:pl:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_925, crs, transform, meta) if return_meta else (tmp_925, crs, transform)
        if pattern == ":t:850:pl:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_850, crs, transform, meta) if return_meta else (tmp_850, crs, transform)
        if pattern == ":t:700:pl:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_700, crs, transform, meta) if return_meta else (tmp_700, crs, transform)
        if pattern == ":t:600:pl:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp_600, crs, transform, meta) if return_meta else (tmp_600, crs, transform)
        if pattern == ":2t:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (tmp2m, crs, transform, meta) if return_meta else (tmp2m, crs, transform)
        if pattern == ":sp:sfc:":
            meta = {"inventory_line": "", "search_pattern": pattern}
            return (pres_sfc, crs, transform, meta) if return_meta else (pres_sfc, crs, transform)
        raise AssertionError(f"unexpected pattern: {pattern}")

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, ctx, grid_cache_key
        if int(fh) == 6 and str(var_key) == "snowfall_kuchera_total":
            return fh6_internal, crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: [
            int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)
        ],
    )
    monkeypatch.setattr(derive_module, "_prefetch_components_parallel", lambda tasks, ctx, *, label="": 0)

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "kuchera_lwe_component": "sf",
                "kuchera_lwe_component_scale": "1",
                "step_hours": "3",
                "kuchera_levels_hpa": "925,850,700,600",
                "kuchera_profile_mode": "simplified",
                "kuchera_use_surface_temp_cap": "true",
                "kuchera_surface_temp_cap_cold_f": "30",
                "kuchera_surface_temp_cap_warm_f": "34",
                "kuchera_surface_temp_cap_cold_ratio": "18",
                "kuchera_surface_temp_cap_warm_ratio": "10",
                "kuchera_use_sfc_pressure_mask": "true",
                "kuchera_incremental_rebuild_window_steps": "2",
            }
        )
    )

    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", lambda **kwargs: None)
    fh6_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="ecmwf",
        var_key="snowfall_kuchera_total",
        product="oper",
        run_date=datetime(2026, 4, 14, 12, 0),
        fh=6,
        var_spec_model=var_spec_model,
        var_capability=None,
        model_plugin=_Plugin(
            {
                "sf": [":sf:sfc:", ":sf:"],
                "tmp925": [":t:925:pl:"],
                "tmp850": [":t:850:pl:"],
                "tmp700": [":t:700:pl:"],
                "tmp600": [":t:600:pl:"],
                "tmp2m": [":2t:"],
                "pres_sfc": [":sp:sfc:"],
            }
        ),
    )
    fh6_internal = (fh6_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)
    full_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="ecmwf",
        var_key="snowfall_kuchera_total",
        product="oper",
        run_date=datetime(2026, 4, 14, 12, 0),
        fh=12,
        var_spec_model=var_spec_model,
        var_capability=None,
        model_plugin=_Plugin(
            {
                "sf": [":sf:sfc:", ":sf:"],
                "tmp925": [":t:925:pl:"],
                "tmp850": [":t:850:pl:"],
                "tmp700": [":t:700:pl:"],
                "tmp600": [":t:600:pl:"],
                "tmp2m": [":2t:"],
                "pres_sfc": [":sp:sfc:"],
            }
        ),
    )

    sf_calls.clear()
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _prior_loader)

    with caplog.at_level("INFO"):
        data, out_crs, out_transform = derive_module._derive_snowfall_kuchera_total_cumulative(
            model_id="ecmwf",
            var_key="snowfall_kuchera_total",
            product="oper",
            run_date=datetime(2026, 4, 14, 12, 0),
            fh=12,
            var_spec_model=var_spec_model,
            var_capability=None,
            model_plugin=_Plugin(
                {
                    "sf": [":sf:sfc:", ":sf:"],
                    "tmp925": [":t:925:pl:"],
                    "tmp850": [":t:850:pl:"],
                    "tmp700": [":t:700:pl:"],
                    "tmp600": [":t:600:pl:"],
                    "tmp2m": [":2t:"],
                    "pres_sfc": [":sp:sfc:"],
                }
            ),
        )

    assert out_crs == crs
    assert out_transform == transform
    assert sf_calls == [6, 9, 12]
    assert "retrying full rebuild" not in caplog.text
    assert "reused_prev_cumulative=true" in caplog.text
    assert "base_fh=006" in caplog.text
    assert "computed_steps=2" in caplog.text
    np.testing.assert_allclose(data, full_data, rtol=1e-6, atol=1e-6)


def test_snow10to1_incremental_reuses_prior_overlap_bucket_window(monkeypatch, caplog) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    step_fhs = [1, 2, 3, 4, 5, 6]
    apcp_calls: list[str] = []
    csnow_calls: list[int] = []

    apcp_data = {
        1: np.full((2, 2), 1.0, dtype=np.float32),
        2: np.full((2, 2), 2.0, dtype=np.float32),
        3: np.full((2, 2), 3.0, dtype=np.float32),
        4: np.full((2, 2), 1.0, dtype=np.float32),
        5: np.full((2, 2), 3.0, dtype=np.float32),
        6: np.full((2, 2), 6.0, dtype=np.float32),
    }
    inventory_by_fh = {
        1: ":APCP:surface:0-1 hour acc fcst:",
        2: ":APCP:surface:0-2 hour acc fcst:",
        3: ":APCP:surface:0-3 hour acc fcst:",
        4: ":APCP:surface:3-4 hour acc fcst:",
        5: ":APCP:surface:3-5 hour acc fcst:",
        6: ":APCP:surface:3-6 hour acc fcst:",
    }

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None, return_meta=False):
        del model_id, product, run_date, herbie_kwargs
        pattern = str(search_pattern)
        step_fh = int(fh)
        if pattern.startswith(":APCP:surface:"):
            apcp_calls.append(f"{step_fh}:{pattern}")
            data = apcp_data[step_fh]
            meta = {"inventory_line": inventory_by_fh[step_fh], "search_pattern": pattern}
            return (data, crs, transform, meta) if return_meta else (data, crs, transform)
        raise AssertionError(f"unexpected pattern: {pattern}")

    def _fake_fetch_step_component(**kwargs):
        step_fh = int(kwargs["step_fh"])
        var_key = str(kwargs["var_key"])
        if var_key == "csnow":
            csnow_calls.append(step_fh)
            return np.ones((2, 2), dtype=np.float32), crs, transform
        raise AssertionError(f"unexpected var_key: {var_key}")

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None, scale_divisor=0.03937007874015748):
        del model_id, run_date, ctx, grid_cache_key, scale_divisor
        if int(fh) == 5:
            return np.full((2, 2), 6.0, dtype=np.float32), crs, transform
        return None

    monkeypatch.setattr(derive_module, "fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr(derive_module, "_fetch_step_component", _fake_fetch_step_component)
    monkeypatch.setattr(
        derive_module,
        "_kuchera_inventory_lines",
        lambda *, model_id, product, run_date, fh, search_pattern: [inventory_by_fh[int(fh)]],
    )
    monkeypatch.setattr(
        derive_module,
        "_resolve_cumulative_step_fhs",
        lambda *, hints, fh, run_date=None, default_step_hours=6: [int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)],
    )
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _prior_loader)

    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "snow_component": "csnow",
                "step_hours": "1",
            }
        )
    )

    with caplog.at_level("INFO"):
        data, out_crs, out_transform = derive_module._derive_snowfall_total_10to1_cumulative(
            model_id="nam",
            var_key="snowfall_total",
            product="conusnest.hiresf",
            run_date=datetime(2026, 4, 7, 18, 0),
            fh=6,
            var_spec_model=var_spec_model,
            var_capability=None,
            model_plugin=object(),
        )

    assert out_crs == crs
    assert out_transform == transform
    assert apcp_calls == [
        "6::APCP:surface:3-6 hour acc fcst:$",
        "5::APCP:surface:3-5 hour acc fcst:$",
    ]
    assert csnow_calls == [5, 6]
    assert "retrying full rebuild" not in caplog.text
    assert "computed_steps=1" in caplog.text
    assert "reused_prev_cumulative=true" in caplog.text
    expected_inches = np.full((2, 2), 9.0 * 0.03937007874015748 * 10.0, dtype=np.float32)
    np.testing.assert_allclose(data, expected_inches, rtol=1e-6, atol=1e-6)


def test_incremental_overlap_apcp_uses_seeded_prior_exact_window(monkeypatch, caplog) -> None:
    crs = CRS.from_epsg(4326)
    transform = Affine.identity()
    tmp_850 = np.full((2, 2), -12.0, dtype=np.float32)
    tmp_700 = np.full((2, 2), -10.0, dtype=np.float32)
    step_fhs = [1, 2, 3, 4, 5]
    apcp_calls: list[str] = []

    apcp_step = {
        1: np.full((2, 2), 1.0, dtype=np.float32),
        2: np.full((2, 2), 2.0, dtype=np.float32),
        3: np.full((2, 2), 3.0, dtype=np.float32),
        4: np.full((2, 2), 4.0, dtype=np.float32),
        5: np.full((2, 2), 5.0, dtype=np.float32),
    }
    apcp_window_total = {
        1: apcp_step[1],
        2: apcp_step[1] + apcp_step[2],
        3: apcp_step[1] + apcp_step[2] + apcp_step[3],
        4: apcp_step[4],
        5: apcp_step[4] + apcp_step[5],
    }
    inventory_by_fh = {
        1: ":APCP:surface:0-1 hour acc fcst:",
        2: ":APCP:surface:0-2 hour acc fcst:",
        3: ":APCP:surface:0-3 hour acc fcst:",
        4: ":APCP:surface:3-4 hour acc fcst:",
        5: ":APCP:surface:3-5 hour acc fcst:",
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
            apcp_calls.append(f"{step_fh}:{pattern}")
            data = apcp_window_total[step_fh]
            meta = {"inventory_line": inventory_by_fh[step_fh], "search_pattern": pattern}
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
        lambda *, hints, fh, run_date=None, default_step_hours=6: [
            int(step_fh) for step_fh in step_fhs if int(step_fh) <= int(fh)
        ],
    )
    monkeypatch.setattr(derive_module, "_prefetch_components_parallel", lambda tasks, ctx, *, label="": 0)

    no_prior = lambda **kwargs: None
    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", no_prior)
    full_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="nam",
        var_key="snowfall_kuchera_total",
        product="conusnest.hiresf",
        run_date=datetime(2026, 4, 7, 18, 0),
        fh=5,
        var_spec_model=_var_spec(rebuild_window_steps=1),
        var_capability=None,
        model_plugin=_Plugin(),
    )

    fh4_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
        model_id="nam",
        var_key="snowfall_kuchera_total",
        product="conusnest.hiresf",
        run_date=datetime(2026, 4, 7, 18, 0),
        fh=4,
        var_spec_model=_var_spec(rebuild_window_steps=5),
        var_capability=None,
        model_plugin=_Plugin(),
    )
    fh4_internal = (fh4_data / np.float32(0.03937007874015748)).astype(np.float32, copy=False)
    prior_precip_fh4 = (
        apcp_step[1] + apcp_step[2] + apcp_step[3] + apcp_step[4]
    ).astype(np.float32, copy=False)

    def _prior_loader(*, model_id, run_date, var_key, fh, ctx, grid_cache_key=None):
        del model_id, run_date, ctx, grid_cache_key
        if int(fh) == 4:
            if str(var_key) == "precip_total":
                return prior_precip_fh4, crs, transform
            return fh4_internal, crs, transform
        return None

    monkeypatch.setattr(derive_module, "_kuchera_load_prior_cumulative", _prior_loader)
    apcp_calls.clear()

    with caplog.at_level("INFO"):
        recovered_data, _, _ = derive_module._derive_snowfall_kuchera_total_cumulative(
            model_id="nam",
            var_key="snowfall_kuchera_total",
            product="conusnest.hiresf",
            run_date=datetime(2026, 4, 7, 18, 0),
            fh=5,
            var_spec_model=_var_spec(rebuild_window_steps=1),
            var_capability=None,
            model_plugin=_Plugin(),
        )

    np.testing.assert_allclose(recovered_data, full_data, rtol=1e-5, atol=1e-5)
    assert apcp_calls == [
        "5::APCP:surface:3-5 hour acc fcst:$",
        "4::APCP:surface:3-4 hour acc fcst:$",
    ]
    assert "retrying full rebuild" not in caplog.text
    assert "computed_steps=1" in caplog.text
    assert "reused_prev_cumulative=true" in caplog.text
