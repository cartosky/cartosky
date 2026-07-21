"""Phase 6 stats grids: id grammar, descriptors, percentile/probability math.

See docs/ENSEMBLE_STATS_GRIDS_DESIGN.md. Synthetic arrays only — no network.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from backend.app.models.base import (
    classify_ensemble_var_id,
    ensemble_stats_descriptors,
    ensemble_stats_product_ids,
    format_prob_threshold,
    parse_prob_threshold,
)
from backend.app.models.registry import MODEL_REGISTRY
from backend.app.services.builder.stats_math import prob_exceedance, sorted_nanpercentile


# ── Shared id grammar (plan §4.1 — written once) ─────────────────────
def test_format_prob_threshold_round_trips() -> None:
    cases = {0.10: "0p1", 0.25: "0p25", 0.50: "0p5", 1.00: "1p0", 1.50: "1p5", 2.00: "2p0", 3: "3p0", 6: "6p0", 12: "12p0"}
    for value, token in cases.items():
        assert format_prob_threshold(value) == token, value
        assert parse_prob_threshold(token) == pytest.approx(float(value))


def test_stats_product_ids_ordered_and_named() -> None:
    descriptor = {
        "percentiles": [50, 10, 90],
        "prob_thresholds": [1.0, 0.25],
        "enabled": True,
    }
    products = ensemble_stats_product_ids("precip_total", descriptor)
    assert list(products) == ["p10", "p50", "p90", "prob_gt_0p25", "prob_gt_1p0"]
    assert products["p50"] == "precip_total__p50"
    assert products["prob_gt_0p25"] == "precip_total__prob_gt_0p25"


def test_classify_ensemble_var_id_grammar() -> None:
    assert classify_ensemble_var_id("tmp2m__mean") == ("tmp2m", "mean", None)
    assert classify_ensemble_var_id("tmp2m__control") == ("tmp2m", "control", None)
    assert classify_ensemble_var_id("tmp2m__m07") == ("tmp2m", "member", "m07")
    assert classify_ensemble_var_id("snowfall_total__p25") == ("snowfall_total", "percentile", 25)
    assert classify_ensemble_var_id("precip_total__prob_gt_0p5") == ("precip_total", "prob_gt", 0.5)
    # prob_lt implemented with B2 (temperature "below" thresholds).
    assert classify_ensemble_var_id("tmp2m__prob_lt_32p0") == ("tmp2m", "prob_lt", 32.0)
    assert classify_ensemble_var_id("tmp2m__prob_lt_0p0") == ("tmp2m", "prob_lt", 0.0)
    # Non-ensemble ids pass through as None.
    assert classify_ensemble_var_id("tmp2m") is None
    assert classify_ensemble_var_id("precip_total__prob_gt_") is None
    assert classify_ensemble_var_id("tmp2m__m1") is None


# ── Descriptors (rollout stages 6A/6B/6C) ────────────────────────────
def test_stats_descriptor_rollout_posture() -> None:
    gefs = ensemble_stats_descriptors(MODEL_REGISTRY["gefs"])
    # 6A (precip) gate passed 2026-07-08 -> 6B (snowfall) enabled; both carry
    # the LOCKED §4.2 thresholds. B2 (2026-07-10) adds two-sided tmp2m on
    # both models at once.
    assert set(gefs) == {"precip_total", "snowfall_total", "tmp2m"}
    assert gefs["precip_total"]["percentiles"] == [10, 25, 50, 75, 90]
    assert gefs["precip_total"]["prob_thresholds"] == [0.10, 0.25, 0.50, 1.00, 1.50, 2.00]
    assert gefs["snowfall_total"]["prob_thresholds"] == [1, 3, 6, 12]
    assert gefs["tmp2m"]["prob_lt_thresholds"] == [0, 20, 32]
    assert gefs["tmp2m"]["prob_thresholds"] == [50, 70, 90, 100]

    eps = ensemble_stats_descriptors(MODEL_REGISTRY["eps"])
    assert set(eps) == {"precip_total", "tmp2m"}
    assert eps["tmp2m"] == gefs["tmp2m"]


def test_stats_descriptor_requires_enabled_and_products() -> None:
    class _Cap:
        def __init__(self, buildable, ensemble):
            self.buildable = buildable
            self.ensemble = ensemble

    class _Capabilities:
        def __init__(self, catalog):
            self.variable_catalog = catalog

    class _Plugin:
        def __init__(self, catalog):
            self.capabilities = _Capabilities(catalog)

    plugin = _Plugin({
        "a": _Cap(True, {"stats": {"percentiles": [50], "enabled": True}}),
        "b": _Cap(True, {"stats": {"percentiles": [50], "enabled": False}}),
        "c": _Cap(False, {"stats": {"percentiles": [50], "enabled": True}}),
        "d": _Cap(True, {"stats": {"percentiles": [], "prob_thresholds": [], "enabled": True}}),
        "e": _Cap(True, {}),
    })
    assert set(ensemble_stats_descriptors(plugin)) == {"a"}


# ── Percentile engine parity (stats design §4 — method="linear") ─────
def _member_stack(members: int = 31, h: int = 40, w: int = 55, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    stack = rng.uniform(0.0, 30.0, size=(members, h, w)).astype(np.float32)
    stack[:, :4, :] = np.nan                       # never-valid band (out of coverage)
    stack[rng.random((members, h, w)) < 0.03] = np.nan  # scattered gaps
    stack[:-1, 5, 5] = np.nan                      # single-valid-member pixel
    return stack


def test_sorted_nanpercentile_matches_numpy_linear() -> None:
    stack = _member_stack()
    qs = [10, 25, 50, 75, 90]
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices
            ref = np.nanpercentile(stack.astype(np.float64), qs, axis=0, method="linear")
    fast = sorted_nanpercentile(stack, qs)
    assert fast.shape == ref.shape
    assert np.array_equal(np.isfinite(ref), np.isfinite(fast))
    both = np.isfinite(ref)
    assert np.allclose(fast[both], ref[both], atol=1e-4)


def test_sorted_nanpercentile_single_valid_member_pixel() -> None:
    stack = _member_stack()
    fast = sorted_nanpercentile(stack, [10, 90])
    # A pixel with exactly one valid member returns that value at every
    # percentile (rank interpolation over count-1 == 0).
    only = stack[-1, 5, 5]
    assert fast[0, 5, 5] == pytest.approx(only, abs=1e-5)
    assert fast[1, 5, 5] == pytest.approx(only, abs=1e-5)


def test_sorted_nanpercentile_all_nan_and_empty() -> None:
    stack = np.full((5, 4, 4), np.nan, dtype=np.float32)
    out = sorted_nanpercentile(stack, [50])
    assert np.all(np.isnan(out))
    assert sorted_nanpercentile(_member_stack(), []).shape == (0, 40, 55)
    with pytest.raises(ValueError, match="members, H, W"):
        sorted_nanpercentile(np.zeros((3, 4)), [50])


# ── Probability of exceedance ────────────────────────────────────────
def test_prob_exceedance_counts_and_nan_pattern() -> None:
    stack = np.full((4, 2, 2), np.nan, dtype=np.float32)
    stack[:, 0, 0] = [0.1, 0.6, 1.2, 2.5]   # 4 valid
    stack[:2, 0, 1] = [0.6, 0.7]            # 2 valid
    stack[0, 1, 0] = 0.4                    # 1 valid
    out = prob_exceedance(stack, [0.5, 1.0])
    assert out[0, 0, 0] == pytest.approx(75.0)   # 3/4 > 0.5
    assert out[1, 0, 0] == pytest.approx(50.0)   # 2/4 > 1.0
    assert out[0, 0, 1] == pytest.approx(100.0)  # 2/2 > 0.5
    assert out[0, 1, 0] == pytest.approx(0.0)    # 0/1 > 0.5
    assert np.isnan(out[0, 1, 1])                # no valid members
    # Strict '>' — a member exactly AT the threshold does not exceed it.
    at = np.full((2, 1, 1), 0.5, dtype=np.float32)
    assert prob_exceedance(at, [0.5])[0, 0, 0] == pytest.approx(0.0)
    # NaN pattern identical to the percentile products.
    stack2 = _member_stack()
    pct = sorted_nanpercentile(stack2, [50])
    prob = prob_exceedance(stack2, [15.0])
    assert np.array_equal(np.isfinite(pct[0]), np.isfinite(prob[0]))


def test_prob_non_exceedance_counts_and_nan_pattern() -> None:
    from backend.app.services.builder.stats_math import prob_non_exceedance

    stack = np.full((4, 2, 2), np.nan, dtype=np.float32)
    stack[:, 0, 0] = [10.0, 25.0, 33.0, 40.0]  # 4 valid
    stack[:2, 0, 1] = [15.0, 18.0]             # 2 valid
    stack[0, 1, 0] = 35.0                      # 1 valid
    out = prob_non_exceedance(stack, [32.0, 20.0])
    assert out[0, 0, 0] == pytest.approx(50.0)   # 2/4 < 32
    assert out[1, 0, 0] == pytest.approx(25.0)   # 1/4 < 20
    assert out[0, 0, 1] == pytest.approx(100.0)  # 2/2 < 32
    assert out[0, 1, 0] == pytest.approx(0.0)    # 0/1 < 32
    assert np.isnan(out[0, 1, 1])                # no valid members
    # Strict '<' — a member exactly AT the threshold counts toward NEITHER
    # direction (mirrors prob_exceedance's strict '>').
    at = np.full((2, 1, 1), 32.0, dtype=np.float32)
    assert prob_non_exceedance(at, [32.0])[0, 0, 0] == pytest.approx(0.0)
    # NaN pattern identical to the percentile products.
    stack2 = _member_stack()
    pct = sorted_nanpercentile(stack2, [50])
    prob = prob_non_exceedance(stack2, [15.0])
    assert np.array_equal(np.isfinite(pct[0]), np.isfinite(prob[0]))


def test_stats_product_ids_two_sided_and_sign_guard() -> None:
    from backend.app.models.base import ensemble_stats_product_ids

    descriptor = {
        "percentiles": [50],
        "prob_lt_thresholds": [32, 0, 20],
        "prob_thresholds": [100, 50],
        "enabled": True,
    }
    products = ensemble_stats_product_ids("tmp2m", descriptor)
    # Percentiles, then cold rungs ascending, then heat rungs ascending.
    assert list(products.items()) == [
        ("p50", "tmp2m__p50"),
        ("prob_lt_0p0", "tmp2m__prob_lt_0p0"),
        ("prob_lt_20p0", "tmp2m__prob_lt_20p0"),
        ("prob_lt_32p0", "tmp2m__prob_lt_32p0"),
        ("prob_gt_50p0", "tmp2m__prob_gt_50p0"),
        ("prob_gt_100p0", "tmp2m__prob_gt_100p0"),
    ]
    # The id grammar carries no sign: negative thresholds must fail loudly
    # at descriptor time, not mint an unclassifiable id.
    with pytest.raises(ValueError, match="Negative prob_lt"):
        ensemble_stats_product_ids("tmp2m", {"prob_lt_thresholds": [-10]})
    with pytest.raises(ValueError, match="Negative prob_gt"):
        ensemble_stats_product_ids("tmp2m", {"prob_thresholds": [-0.5]})


# ── Packing (stats design §6) ────────────────────────────────────────
def test_percentile_packing_falls_back_to_mean_twin() -> None:
    from backend.app.services.grid import _PACKING_BY_MODEL_VAR, _packing_config, normalize_grid_pack_var_id

    assert normalize_grid_pack_var_id("precip_total__p50") == "precip_total__mean"
    assert normalize_grid_pack_var_id("precip_total__prob_gt_0p5") == "precip_total__prob_gt_0p5"
    mean_packing = _PACKING_BY_MODEL_VAR[("gefs", "precip_total__mean")]
    assert _packing_config("gefs", "precip_total__p50") is mean_packing
    assert _packing_config("gefs", "precip_total__p10") is mean_packing


def test_probability_packing_explicit_generated_entries() -> None:
    from backend.app.services import grid

    packing = grid._packing_config("gefs", "precip_total__prob_gt_0p5")
    assert packing == {"scale": 0.1, "offset": 0.0, "nodata": 65535, "units": "%"}
    assert grid._packing_config("eps", "precip_total__prob_gt_1p0") is not None
    # 6B: snowfall prob entries generate from its (now enabled) descriptor.
    assert grid._packing_config("gefs", "snowfall_total__prob_gt_6p0") is not None
    # B2: tmp2m prob entries (both directions) generate from its descriptor.
    assert grid._packing_config("gefs", "tmp2m__prob_gt_90p0") is not None
    assert grid._packing_config("eps", "tmp2m__prob_lt_32p0") == {
        "scale": 0.1, "offset": 0.0, "nodata": 65535, "units": "%",
    }
    # Explicit entries only: a prob id no descriptor declares resolves to
    # nothing (never a fallback) — pwat has no stats descriptor at all.
    assert grid._packing_config("gefs", "pwat__prob_gt_1p0") is None


def test_probability_colormap_spec() -> None:
    from backend.app.services.colormaps import get_color_map_spec

    spec = get_color_map_spec("ensemble_probability")
    assert spec["units"] == "%"
    assert spec["range"] == (0.0, 100.0)
    assert spec["allow_dry_frame"] is True


# ── The stats pass (end-to-end, synthetic members) ───────────────────
@pytest.fixture()
def stats_roster_plugin(monkeypatch):
    """GEFS plugin proxy: 3-member roster on precip_total with a tiny stats
    descriptor; schedule [0, 6, 12] (fh 0 dropped by the derived filter)."""
    from backend.app.services.builder import stats as stats_mod

    plugin = MODEL_REGISTRY["gefs"]
    member_descriptor = {"count": 2, "control": True, "prefix": "m", "enabled": True}
    stats_descriptor = {"percentiles": [50], "prob_thresholds": [0.5], "enabled": True}
    monkeypatch.setattr(
        stats_mod, "ensemble_member_descriptors",
        lambda _plugin: {"precip_total": member_descriptor},
    )
    monkeypatch.setattr(
        stats_mod, "ensemble_stats_descriptors",
        lambda _plugin: {"precip_total": stats_descriptor},
    )

    class _PluginProxy:
        def __getattr__(self, name):
            return getattr(plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            return [0, 6, 12]

    return _PluginProxy()


def _publish_member_precip(data_root, run_id: str, member: str, fh: int, value: float) -> None:
    from rasterio.transform import from_origin

    from backend.app.services.grid import write_slim_grid_frame_for_run_root

    data = np.full((3, 3), value, dtype=np.float32)
    data[0, 0] = np.nan   # a nodata pixel shared by every member
    data[2, 2] = 0.05     # a shared dry corner — real fields vary spatially,
    # and the pre-encode gate correctly rejects flat non-zero products.
    write_slim_grid_frame_for_run_root(
        run_root=data_root / "published" / "gefs" / run_id, model="gefs",
        var=f"precip_total__{member}", fh=fh, values=data,
        transform=from_origin(-101.0, 46.0, 1.0, 1.0), projection="EPSG:4326",
    )


def test_stats_pass_end_to_end_manual_tally(tmp_path, stats_roster_plugin) -> None:
    from backend.app.services.builder import stats as stats_mod
    from backend.app.services.builder.members import _decode_member_frame

    run_id = "20260706_00z"
    # Members: m01=0.2", m02=0.8", control=1.4" at both fhs.
    values = {"m01": 0.2, "m02": 0.8, "control": 1.4}
    for member, value in values.items():
        for fh in (6, 12):
            _publish_member_precip(tmp_path, run_id, member, fh, value)

    assert stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )
    summary = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na",
    )
    # 2 products × 2 fhs; fh 0 dropped by the derived filter.
    assert summary.counts == {stats_mod.STATUS_WRITTEN: 4}
    assert summary.complete
    assert set(summary.stats_var_ids) == {"precip_total__p50", "precip_total__prob_gt_0p5"}

    staging = tmp_path / "staging" / "gefs" / run_id
    p50, _ = _decode_member_frame(staging, "gefs", "precip_total__p50", 6)
    prob, _ = _decode_member_frame(staging, "gefs", "precip_total__prob_gt_0p5", 6)
    # Manual member tally (the plan's verification bar): median of
    # (0.2, 0.8, 1.4) = 0.8; P(> 0.5") = 2/3 ≈ 66.7%.
    assert p50[1, 1] == pytest.approx(0.8, abs=0.011)      # precip packing = 0.01 in
    assert prob[1, 1] == pytest.approx(66.7, abs=0.11)     # prob packing = 0.1 %
    # The shared dry corner: all members at 0.05" -> P(> 0.5") is exactly 0.
    assert p50[2, 2] == pytest.approx(0.05, abs=0.011)
    assert prob[2, 2] == pytest.approx(0.0, abs=0.11)
    # The shared nodata pixel stays nodata in every product.
    assert np.isnan(p50[0, 0]) and np.isnan(prob[0, 0])

    # Second pass: everything resumes, nothing recomputed.
    summary2 = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na",
    )
    assert summary2.counts == {stats_mod.STATUS_RESUMED: 4}
    assert not stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )
    # Staged-but-unpublished stats register as promote-pending.
    assert stats_mod.stats_promote_pending(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )


def test_stats_pass_recomputes_frame_when_sidecar_is_missing(
    tmp_path,
    stats_roster_plugin,
) -> None:
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        for fh in (6, 12):
            _publish_member_precip(tmp_path, run_id, member, fh, 0.8)

    first = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )
    assert first.counts == {stats_mod.STATUS_WRITTEN: 4}

    staging = tmp_path / "staging" / "gefs" / run_id
    sidecar = staging / "precip_total__p50" / "fh006.json"
    frame = staging / "precip_total__p50" / "grid" / "fh006.l0.u16.bin"
    meta = staging / "precip_total__p50" / "grid" / "fh006.l0.meta.json"
    assert frame.is_file() and meta.is_file() and sidecar.is_file()
    sidecar.unlink()

    assert stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
    )
    second = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )

    assert second.counts == {
        stats_mod.STATUS_RESUMED: 3,
        stats_mod.STATUS_WRITTEN: 1,
    }
    assert sidecar.is_file()
    assert not stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
    )


def test_stats_promote_pending_requires_complete_sidecars(
    tmp_path,
    stats_roster_plugin,
) -> None:
    import shutil

    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        for fh in (6, 12):
            _publish_member_precip(tmp_path, run_id, member, fh, 0.8)

    first = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )
    assert first.counts == {stats_mod.STATUS_WRITTEN: 4}

    staging = tmp_path / "staging" / "gefs" / run_id
    sidecars = [
        staging / var_id / f"fh{fh:03d}.json"
        for var_id in ("precip_total__p50", "precip_total__prob_gt_0p5")
        for fh in (6, 12)
    ]
    for sidecar in sidecars:
        sidecar.unlink()

    assert not stats_mod.stats_promote_pending(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
    )

    repaired = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )
    assert repaired.counts == {stats_mod.STATUS_WRITTEN: 4}
    assert stats_mod.stats_promote_pending(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
    )

    published = tmp_path / "published" / "gefs" / run_id
    shutil.copytree(staging, published, dirs_exist_ok=True)
    assert not stats_mod.stats_promote_pending(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
    )

    (published / "precip_total__p50" / "fh006.json").unlink()
    assert stats_mod.stats_promote_pending(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
    )


def test_stats_pass_completeness_gate_skips_partial_rosters(
    tmp_path, stats_roster_plugin,
) -> None:
    """The LOCKED §3.3 gate: a missing member frame means NO stat for that
    fh — skipped (not failed), excluded from pending until the roster
    arrives, then computed."""
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        _publish_member_precip(tmp_path, run_id, member, 6, 0.8)
    # fh 12: control missing -> partial roster.
    for member in ("m01", "m02"):
        _publish_member_precip(tmp_path, run_id, member, 12, 0.8)

    summary = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na",
    )
    assert summary.counts == {
        stats_mod.STATUS_WRITTEN: 2,
        stats_mod.STATUS_SKIPPED_INCOMPLETE: 2,
    }
    assert summary.complete  # skipped-incomplete is not failure
    staging = tmp_path / "staging" / "gefs" / run_id
    assert not (staging / "precip_total__p50" / "grid" / "fh012.l0.u16.bin").exists()
    # The partial fh drives bounded health-only passes until the alert threshold.
    assert stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )

    # The missing member arrives (e.g. member backfill) -> pending again,
    # and the next pass fills exactly the gap.
    _publish_member_precip(tmp_path, run_id, "control", 12, 0.8)
    assert stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )
    summary2 = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na",
    )
    assert summary2.counts == {
        stats_mod.STATUS_WRITTEN: 2,
        stats_mod.STATUS_RESUMED: 2,
    }


def test_stats_pass_persists_and_clears_repeated_incomplete_roster_alert(
    tmp_path, stats_roster_plugin,
) -> None:
    """A roster gap becomes visible after three full stats passes and the
    durable warning disappears as soon as the missing member recovers."""
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        _publish_member_precip(tmp_path, run_id, member, 6, 0.8)
    for member in ("m01", "m02"):
        _publish_member_precip(tmp_path, run_id, member, 12, 0.8)

    health_path = (
        tmp_path / "status" / "ensemble_stats" / "gefs" / f"{run_id}.json"
    )
    for expected_passes in (1, 2, 3):
        summary = stats_mod.run_stats_pass(
            plugin=stats_roster_plugin,
            model_id="gefs",
            run_id=run_id,
            data_root=tmp_path,
            region="na",
        )
        assert summary.incomplete_units == [
            {
                "base_var": "precip_total",
                "forecast_hour": 12,
                "missing_members": ["control"],
            }
        ]
        payload = json.loads(health_path.read_text())
        assert payload["units"][0]["consecutive_passes"] == expected_passes
        assert payload["units"][0]["alerting"] is (expected_passes >= 3)
        assert stats_mod.stats_pass_pending(
            plugin=stats_roster_plugin,
            model_id="gefs",
            run_id=run_id,
            data_root=tmp_path,
        ) is (expected_passes < 3)

    _publish_member_precip(tmp_path, run_id, "control", 12, 0.8)
    stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )
    assert not health_path.exists()


def test_stats_pass_persists_processing_error_streak(
    tmp_path, stats_roster_plugin,
) -> None:
    """A fully present roster that cannot be processed must alert through
    the same durable stats-health channel as a partial roster."""
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        _publish_member_precip(tmp_path, run_id, member, 6, 0.8)

    broken_meta_path = (
        tmp_path / "published" / "gefs" / run_id
        / "precip_total__m01" / "grid" / "fh006.l0.meta.json"
    )
    broken_meta = json.loads(broken_meta_path.read_text())
    valid_transform = broken_meta["transform"]
    broken_meta["transform"] = [1.0, 0.0]
    broken_meta_path.write_text(json.dumps(broken_meta))

    health_path = (
        tmp_path / "status" / "ensemble_stats" / "gefs" / f"{run_id}.json"
    )
    for expected_passes in (1, 2, 3):
        summary = stats_mod.run_stats_pass(
            plugin=stats_roster_plugin,
            model_id="gefs",
            run_id=run_id,
            data_root=tmp_path,
            region="na",
        )
        assert summary.failed_units == [
            {
                "base_var": "precip_total",
                "forecast_hour": 6,
                "failure_statuses": [stats_mod.STATUS_ERROR],
            }
        ]
        payload = json.loads(health_path.read_text())
        assert payload["units"] == [
            {
                "alerting": expected_passes >= 3,
                "base_var": "precip_total",
                "consecutive_passes": expected_passes,
                "failure_statuses": [stats_mod.STATUS_ERROR],
                "first_seen_at": payload["units"][0]["first_seen_at"],
                "forecast_hour": 6,
                "last_seen_at": payload["units"][0]["last_seen_at"],
                "missing_members": [],
            }
        ]

    broken_meta["transform"] = valid_transform
    broken_meta_path.write_text(json.dumps(broken_meta))
    recovered = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )
    assert recovered.complete
    assert not health_path.exists()


def test_stats_pass_persists_gate_failure_streak(
    tmp_path, stats_roster_plugin, monkeypatch,
) -> None:
    """A persistent pre-encode rejection is visible instead of retrying
    forever with no health signal."""
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        _publish_member_precip(tmp_path, run_id, member, 6, 0.8)
    monkeypatch.setattr(
        stats_mod, "check_pre_encode_value_sanity", lambda *_args, **_kwargs: False,
    )

    for _ in range(3):
        summary = stats_mod.run_stats_pass(
            plugin=stats_roster_plugin,
            model_id="gefs",
            run_id=run_id,
            data_root=tmp_path,
            region="na",
        )
        assert summary.failed_units == [
            {
                "base_var": "precip_total",
                "forecast_hour": 6,
                "failure_statuses": [stats_mod.STATUS_GATE_FAILED],
            }
        ]

    health_path = (
        tmp_path / "status" / "ensemble_stats" / "gefs" / f"{run_id}.json"
    )
    unit = json.loads(health_path.read_text())["units"][0]
    assert unit["failure_statuses"] == [stats_mod.STATUS_GATE_FAILED]
    assert unit["missing_members"] == []
    assert unit["consecutive_passes"] == 3
    assert unit["alerting"] is True


def test_stats_pass_does_not_alert_for_future_hour_with_zero_member_frames(
    tmp_path, stats_roster_plugin,
) -> None:
    """A wholly absent roster is normal future work; only a partial roster
    is evidence that one or more members may be wedged."""
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        _publish_member_precip(tmp_path, run_id, member, 6, 0.8)

    for _ in range(3):
        summary = stats_mod.run_stats_pass(
            plugin=stats_roster_plugin,
            model_id="gefs",
            run_id=run_id,
            data_root=tmp_path,
            region="na",
        )
        assert summary.incomplete_units == []

    health_path = (
        tmp_path / "status" / "ensemble_stats" / "gefs" / f"{run_id}.json"
    )
    assert not health_path.exists()


def test_stats_health_persistence_failure_does_not_block_stats_publish(
    tmp_path, stats_roster_plugin, monkeypatch,
) -> None:
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        for fh in (6, 12):
            _publish_member_precip(tmp_path, run_id, member, fh, 0.8)

    def _fail_health_write(**_kwargs):
        raise OSError("status filesystem unavailable")

    monkeypatch.setattr(
        stats_mod, "update_ensemble_stats_health", _fail_health_write,
    )
    summary = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
    )

    assert summary.counts == {stats_mod.STATUS_WRITTEN: 4}
    assert summary.complete


def test_preempted_stats_pass_does_not_advance_incomplete_roster_alert(
    tmp_path,
) -> None:
    from backend.app.services.ensemble_stats_health import (
        load_ensemble_stats_health,
        update_ensemble_stats_health,
    )

    unit = {
        "base_var": "precip_total",
        "forecast_hour": 12,
        "missing_members": ["control"],
    }
    update_ensemble_stats_health(
        data_root=tmp_path,
        model_id="gefs",
        run_id="20260706_00z",
        incomplete_units=[unit],
        pass_complete=True,
        now_ts=100,
    )
    update_ensemble_stats_health(
        data_root=tmp_path,
        model_id="gefs",
        run_id="20260706_00z",
        incomplete_units=[unit],
        pass_complete=False,
        now_ts=200,
    )

    payload = load_ensemble_stats_health(
        tmp_path, "gefs", "20260706_00z",
    )
    assert payload is not None
    assert payload["units"][0]["consecutive_passes"] == 1
    assert payload["units"][0]["last_seen_at"] == 100


def test_stats_pass_preemption(tmp_path, stats_roster_plugin) -> None:
    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member in ("m01", "m02", "control"):
        for fh in (6, 12):
            _publish_member_precip(tmp_path, run_id, member, fh, 0.8)

    calls = {"n": 0}

    def _stop_after_one() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    summary = stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", should_stop=_stop_after_one,
    )
    assert summary.preempted
    assert not summary.complete
    # Remaining work still reads as pending for the next pass.
    assert stats_mod.stats_pass_pending(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )


def test_stats_pass_requires_member_descriptor(tmp_path, monkeypatch) -> None:
    from backend.app.services.builder import stats as stats_mod

    monkeypatch.setattr(
        stats_mod, "ensemble_stats_descriptors",
        lambda _plugin: {"precip_total": {"percentiles": [50], "enabled": True}},
    )
    monkeypatch.setattr(stats_mod, "ensemble_member_descriptors", lambda _plugin: {})
    with pytest.raises(ValueError, match="no enabled\nensemble.members|no enabled"):
        stats_mod.build_stats_plan(MODEL_REGISTRY["gefs"], "gefs", "20260706_00z", "na")


# ── Capabilities products payload (stats design §7 / D-D) ────────────
def test_capabilities_products_payload() -> None:
    from backend.app.models.serialization import serialize_variable_capability

    capability = MODEL_REGISTRY["gefs"].get_var_capability("precip_total")
    payload = serialize_variable_capability("gefs", capability)
    products = payload["ensemble"]["products"]
    assert products[0] == {
        "key": "mean", "var_id": None, "label": "Mean",
        "long_label": "Ensemble mean", "overlay_label": "Mean",
    }
    by_key = {p["key"]: p for p in products}
    assert by_key["p50"]["var_id"] == "precip_total__p50"
    assert by_key["p50"]["label"] == "P50"
    assert by_key["p50"]["long_label"] == "50th percentile"
    assert by_key["p50"]["overlay_label"] == "50th Percentile"
    assert by_key["mean"]["overlay_label"] == "Mean"
    assert by_key["prob_gt_0p5"]["label"] == 'P(> 0.5")'
    assert by_key["prob_gt_0p5"]["long_label"] == 'Probability of precipitation > 0.5"'
    assert by_key["prob_gt_0p5"]["overlay_label"] == 'Prob. > 0.5"'
    # Ordered: mean, percentiles ascending, thresholds ascending.
    assert [p["key"] for p in products] == [
        "mean", "p10", "p25", "p50", "p75", "p90",
        "prob_gt_0p1", "prob_gt_0p25", "prob_gt_0p5",
        "prob_gt_1p0", "prob_gt_1p5", "prob_gt_2p0",
    ]

    # 6B: snowfall products carry the snowfall noun and integer thresholds.
    snow = MODEL_REGISTRY["gefs"].get_var_capability("snowfall_total")
    snow_payload = serialize_variable_capability("gefs", snow)
    snow_products = {p["key"]: p for p in snow_payload["ensemble"]["products"]}
    assert snow_products["prob_gt_6p0"]["label"] == 'P(> 6")'
    assert snow_products["prob_gt_6p0"]["long_label"] == 'Probability of snowfall > 6"'
    # B2: tmp2m products are two-sided with degree-Fahrenheit labels,
    # ordered mean, percentiles, cold rungs ascending, heat rungs ascending.
    tmp = MODEL_REGISTRY["gefs"].get_var_capability("tmp2m")
    tmp_payload = serialize_variable_capability("gefs", tmp)
    tmp_products = tmp_payload["ensemble"]["products"]
    tmp_by_key = {p["key"]: p for p in tmp_products}
    assert tmp_by_key["prob_lt_32p0"]["label"] == "P(< 32\u00b0F)"
    assert tmp_by_key["prob_lt_32p0"]["long_label"] == "Probability of temperature < 32\u00b0F"
    assert tmp_by_key["prob_lt_32p0"]["overlay_label"] == "Prob. < 32\u00b0F"
    assert tmp_by_key["prob_gt_100p0"]["label"] == "P(> 100\u00b0F)"
    assert tmp_by_key["prob_lt_32p0"]["var_id"] == "tmp2m__prob_lt_32p0"
    assert [p["key"] for p in tmp_products] == [
        "mean", "p10", "p25", "p50", "p75", "p90",
        "prob_lt_0p0", "prob_lt_20p0", "prob_lt_32p0",
        "prob_gt_50p0", "prob_gt_70p0", "prob_gt_90p0", "prob_gt_100p0",
    ]
    # No stats descriptor at all -> unchanged shape.
    pwat = MODEL_REGISTRY["gefs"].get_var_capability("pwat")
    pwat_payload = serialize_variable_capability("gefs", pwat)
    assert "products" not in pwat_payload.get("ensemble", {})


def test_stats_pass_writes_frame_sidecars(tmp_path, stats_roster_plugin) -> None:
    """Sidecars make stats vars first-class run-manifest variables — the
    viewer scrubber/legend and the meteogram both consume them."""
    import json

    from backend.app.services.builder import stats as stats_mod

    run_id = "20260706_00z"
    for member, value in (("m01", 0.2), ("m02", 0.8), ("control", 1.4)):
        _publish_member_precip(tmp_path, run_id, member, 6, value)
    stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na",
    )

    staging = tmp_path / "staging" / "gefs" / run_id
    prob_sidecar = json.loads(
        (staging / "precip_total__prob_gt_0p5" / "fh006.json").read_text()
    )
    assert prob_sidecar["units"] == "%"
    assert prob_sidecar["valid_time"] == "2026-07-06T06:00:00Z"
    assert prob_sidecar["var"] == "precip_total__prob_gt_0p5"
    assert prob_sidecar["legend"]  # legend built from the probability spec
    p50_sidecar = json.loads(
        (staging / "precip_total__p50" / "fh006.json").read_text()
    )
    assert p50_sidecar["units"] == "in"


def test_stats_grid_manifest_palette_resolution(tmp_path, stats_roster_plugin) -> None:
    """The viewer colorizes from the GRID manifest's palette block — stats
    ids must resolve: percentiles -> the base variable's colormap,
    probabilities -> the shared 0-100% ramp."""
    import json

    from backend.app.services.builder import stats as stats_mod
    from backend.app.services.grid import build_grid_manifests_for_run_root
    from backend.app.services.render_resampling import variable_color_map_id

    assert variable_color_map_id("gefs", "precip_total__p50") == variable_color_map_id("gefs", "precip_total")
    assert variable_color_map_id("gefs", "precip_total__prob_gt_0p5") == "ensemble_probability"
    assert variable_color_map_id("gefs", "bogus__p50") is None

    run_id = "20260706_00z"
    for member, value in (("m01", 0.2), ("m02", 0.8), ("control", 1.4)):
        _publish_member_precip(tmp_path, run_id, member, 6, value)
    stats_mod.run_stats_pass(
        plugin=stats_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na",
    )
    staging = tmp_path / "staging" / "gefs" / run_id
    built = build_grid_manifests_for_run_root(
        run_root=staging, model="gefs", run=run_id,
        variables=("precip_total__p50", "precip_total__prob_gt_0p5"),
    )
    assert built == 2
    prob_manifest = json.loads(
        (staging / "precip_total__prob_gt_0p5" / "grid" / "manifest.json").read_text()
    )
    assert prob_manifest["palette"]["color_map_id"] == "ensemble_probability"
    p50_manifest = json.loads(
        (staging / "precip_total__p50" / "grid" / "manifest.json").read_text()
    )
    assert p50_manifest["palette"]["color_map_id"] == "precip_total"
