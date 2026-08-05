"""Cross-source canary and failover-seam audit (fast-path design §6/§8, WP4).

No network. The fast side is built by the real sub-loop against
``test_fastpath_subloop.FakeSource`` (so the frames under comparison are real
published artifacts, packed and promoted by production code), and the reference
side is a fake 0.25° product whose values the test controls — which is what
makes "the canary alerts when the two sources disagree" testable at all.

Ownership is pinned to the ``global`` 0.25° domain throughout, for the same
reason the sub-loop tests do it: a full NA pass packs 1825×1893 frames.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.registry import MODEL_REGISTRY
from app.services import grid as grid_module
from app.services import scheduler as scheduler_module
from app.services.fastpath import canary as canary_module
from app.services.fastpath import ownership
from app.services.fastpath import subloop as subloop_module
from app.services.fastpath.state import FastpathRunState
from app.services.sources.openmeteo.catalog import ECMWF_IFS_CATALOG
from app.services.sources.openmeteo.grids import REGULAR_025_SOURCE
from app.services.sources.openmeteo.source import RunObject

from tests.test_fastpath_subloop import (
    MM_TO_IN,
    RUN_DT,
    RUN_ID,
    STEP_PRECIP_MM,
    FakeSource,
    _LAUNCH_VARS,
)

C_TO_F_SLOPE = 9.0 / 5.0


@pytest.fixture(autouse=True)
def _fastpath_global_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "ecmwf")
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        ",".join(f"ecmwf:{var_id}:na=delayed" for var_id in _LAUNCH_VARS),
    )


def _plugin():
    return MODEL_REGISTRY.get("ecmwf")


def _state(data_root: Path, run_id: str = RUN_ID) -> FastpathRunState:
    return FastpathRunState.load_or_create(
        data_root=data_root, model="ecmwf", run_id=run_id
    )


# ---------------------------------------------------------------------------
# Fake 0.25 degree reference product
# ---------------------------------------------------------------------------


class FakeReferenceSource:
    """A stand-in for ``ecmwf_ifs025``: 721×1440 fields on a 3 h step ladder.

    Values are whatever the test asks for, so "the two sources agree" and "the
    two sources have drifted by 2 °C" are both one constructor argument.
    """

    def __init__(
        self,
        *,
        temperature_c: float = 15.0,
        dew_point_c: float = 5.0,
        gust_ms: float = 5.0,
        precip_mm_per_step: float = 3 * STEP_PRECIP_MM,
        max_fh: int = 12,
        step_hours: int = 3,
        missing: frozenset[str] = frozenset(),
    ) -> None:
        self.temperature_c = temperature_c
        self.dew_point_c = dew_point_c
        self.gust_ms = gust_ms
        self.precip_mm_per_step = precip_mm_per_step
        self.max_fh = max_fh
        self.step_hours = step_hours
        self.missing = missing
        self.fetched: list[tuple[str, tuple[str, ...]]] = []

    def list_run_objects(self, catalog, run_dt, **_kwargs):
        del catalog
        objects = []
        for fh in range(self.step_hours, self.max_fh + 1, self.step_hours):
            valid = run_dt + timedelta(hours=fh)
            objects.append(
                RunObject(
                    valid_dt=valid,
                    fh=fh,
                    url=f"https://reference.invalid/{valid:%Y-%m-%dT%H%M}.om",
                    last_modified=valid,
                    etag=f"ref-{fh:03d}",
                )
            )
        return objects

    def fetch_frame_fields(self, catalog, url, var_names, window="global", **_kwargs):
        del catalog, window
        names = tuple(var_names)
        self.fetched.append((url, names))
        shape = (REGULAR_025_SOURCE.nlat, REGULAR_025_SOURCE.nlon)
        values = {
            "temperature_2m": self.temperature_c,
            "dew_point_2m": self.dew_point_c,
            "wind_gusts_10m": self.gust_ms,
            "precipitation": self.precip_mm_per_step,
        }
        return {
            name: np.full(shape, np.float32(values[name]), dtype=np.float32)
            for name in names
            if name in values and name not in self.missing
        }


def _fast_run(data_root: Path, *, max_fh: int = 6) -> None:
    """Build a real fast run into ``data_root`` (staged AND promoted)."""
    subloop_module.run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=RUN_DT,
        source=FakeSource(max_fh=max_fh),
    )


def _canary(data_root: Path, source: FakeReferenceSource, **kwargs):
    return canary_module.run_canary_for_run(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=RUN_DT,
        run_id=RUN_ID,
        state=_state(data_root),
        source=source,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def test_coarsen_is_a_mean_pool_matching_the_phase4_port() -> None:
    values = np.arange(24 * 24, dtype=np.float64).reshape(24, 24)
    pooled = canary_module.coarsen(values, 12)
    assert pooled.shape == (2, 2)
    assert pooled[0, 0] == pytest.approx(values[:12, :12].mean())
    assert pooled[1, 1] == pytest.approx(values[12:, 12:].mean())


def test_coarsen_trims_a_ragged_edge_rather_than_padding_it() -> None:
    values = np.ones((25, 13), dtype=np.float64)
    assert canary_module.coarsen(values, 12).shape == (2, 1)


def test_coarsen_is_the_identity_below_a_factor_of_two() -> None:
    values = np.arange(9.0).reshape(3, 3)
    assert np.array_equal(canary_module.coarsen(values, 1), values)


def test_identical_fields_produce_zero_bias_and_unit_correlation() -> None:
    field = np.linspace(0.0, 40.0, 48 * 48).reshape(48, 48)
    n, bias, mae, synoptic, corr, notes = canary_module._metrics(
        field, field.copy(), coarsen_factor=12
    )
    assert n == 48 * 48
    assert bias == pytest.approx(0.0)
    assert mae == pytest.approx(0.0)
    assert synoptic == pytest.approx(0.0)
    assert corr == pytest.approx(1.0)
    assert notes == ()


def test_a_constant_field_reports_a_degenerate_correlation_not_a_failure() -> None:
    constant = np.full((48, 48), 3.0)
    _n, bias, _mae, synoptic, corr, notes = canary_module._metrics(
        constant, constant.copy(), coarsen_factor=12
    )
    assert not np.isfinite(corr)
    assert "degenerate_correlation_constant_field" in notes
    # An undefined correlation must not page: a dry precipitation frame is
    # normal weather, not a data bug.
    assert canary_module._evaluate(
        var_id="precip_total", bias=bias, synoptic_mae=synoptic, corr=corr
    ) == ()


def test_a_numerically_flat_field_is_treated_as_constant() -> None:
    """Resampling a constant field leaves float round-off, not real variance.

    Correlating a real field against one whose std is 1e-5 on a mean of 59
    produces a meaningless number near zero — which would read as the two
    sources disagreeing completely. The flatness test is relative for exactly
    that reason.
    """
    flat = np.full((48, 48), 59.0)
    flat[0, 0] = 59.00001
    varied = np.linspace(50.0, 70.0, 48 * 48).reshape(48, 48)
    _n, _bias, _mae, _syn, corr, notes = canary_module._metrics(
        varied, flat, coarsen_factor=12
    )
    assert not np.isfinite(corr)
    assert "degenerate_correlation_constant_field" in notes
    # Real weather is nowhere near the threshold.
    assert not canary_module._is_flat(varied)


def test_non_finite_cells_are_excluded_from_the_pointwise_metrics() -> None:
    fast = np.full((24, 24), 10.0)
    reference = np.full((24, 24), 10.0)
    fast[0, 0] = np.nan
    n, bias, _mae, _syn, _corr, _notes = canary_module._metrics(
        fast, reference, coarsen_factor=12
    )
    assert n == 24 * 24 - 1
    assert bias == pytest.approx(0.0)


def test_thresholds_are_per_variable_with_a_documented_default() -> None:
    assert canary_module.thresholds_for("tmp2m").max_synoptic_mae == pytest.approx(0.36)
    assert canary_module.thresholds_for("precip_total").max_abs_bias == pytest.approx(
        0.004
    )
    assert canary_module.thresholds_for("nothing_here") is canary_module.DEFAULT_THRESHOLDS


def test_each_threshold_fails_independently() -> None:
    assert canary_module._evaluate(
        var_id="tmp2m", bias=1.0, synoptic_mae=0.0, corr=1.0
    ) == ("bias",)
    assert canary_module._evaluate(
        var_id="tmp2m", bias=0.0, synoptic_mae=5.0, corr=1.0
    ) == ("synoptic_mae",)
    assert canary_module._evaluate(
        var_id="tmp2m", bias=0.0, synoptic_mae=0.0, corr=0.5
    ) == ("corr",)


# ---------------------------------------------------------------------------
# Reference catalog
# ---------------------------------------------------------------------------


def test_reference_catalog_is_the_025_sibling_product() -> None:
    reference = canary_module.reference_catalog_for(ECMWF_IFS_CATALOG)
    assert reference.bucket_dir == "ecmwf_ifs025"
    assert reference.grid.value == "regular025"
    assert reference.source_points == REGULAR_025_SOURCE.points
    # It must never be mistaken for a buildable fast catalog.
    assert reference.fast_domains == ()
    assert reference.derived_variables == ()


def test_reference_catalog_excludes_the_units_gotcha_variable() -> None:
    """``pressure_msl`` is Pa on the 9 km product and hPa on ``ifs025``.

    Carrying it over would apply the 9 km catalog's identity conversion to an
    hPa field — a silent 100× error in exactly the component whose job is to
    detect silent errors. The reference variable list is an explicit units
    audit, so an un-audited variable is simply absent.
    """
    reference = canary_module.reference_catalog_for(ECMWF_IFS_CATALOG)
    names = {variable.om_name for variable in reference.variables}
    assert "pressure_msl" not in names
    assert "temperature_2m" in names and "precipitation" in names


def test_the_reference_catalog_cannot_mutate_the_live_one() -> None:
    """``dataclasses.replace`` copies field REFERENCES, not values.

    ``source_cadence`` is a plain dict on the live catalog, so an un-copied
    replace hands out a live handle to the ladder the production fast path
    builds against — a write through the reference would rewrite what the real
    scheduler fetches.
    """
    reference = canary_module.reference_catalog_for(ECMWF_IFS_CATALOG)
    assert reference.source_cadence is not ECMWF_IFS_CATALOG.source_cadence
    assert dict(reference.source_cadence) == dict(ECMWF_IFS_CATALOG.source_cadence)

    with pytest.raises(TypeError):
        reference.source_cadence[0] = ()
    # Belt and braces: even a successful write to the copy must not reach the
    # live catalog.
    live_before = dict(ECMWF_IFS_CATALOG.source_cadence)
    try:
        dict(reference.source_cadence)[0] = ()
    except TypeError:
        pass
    assert dict(ECMWF_IFS_CATALOG.source_cadence) == live_before


def test_a_model_without_a_reference_product_skips_cleanly(tmp_path: Path) -> None:
    import dataclasses

    other = dataclasses.replace(ECMWF_IFS_CATALOG, model_id="icon")
    with pytest.raises(KeyError):
        canary_module.reference_catalog_for(other)


# ---------------------------------------------------------------------------
# Frame selection
# ---------------------------------------------------------------------------


def test_frame_selection_prefers_a_mid_run_hour_on_the_3h_ladder() -> None:
    chosen = canary_module.select_canary_fh(
        plugin=_plugin(), var_ids=("tmp2m",), cycle_hour=0, ready_through_fh=360
    )
    assert chosen == canary_module.PREFERRED_CANARY_FH


def test_frame_selection_never_exceeds_the_published_frontier() -> None:
    chosen = canary_module.select_canary_fh(
        plugin=_plugin(), var_ids=("tmp2m",), cycle_hour=0, ready_through_fh=12
    )
    assert chosen == 12


def test_frame_selection_uses_only_hours_common_to_every_canaried_variable() -> None:
    chosen = canary_module.select_canary_fh(
        plugin=_plugin(),
        var_ids=("tmp2m", "precip_total"),
        cycle_hour=0,
        ready_through_fh=360,
    )
    assert chosen is not None
    for var_id in ("tmp2m", "precip_total"):
        assert chosen in subloop_module._scheduled_fhs(_plugin(), var_id, 0)


def test_frame_selection_returns_none_with_nothing_published() -> None:
    assert (
        canary_module.select_canary_fh(
            plugin=_plugin(), var_ids=("tmp2m",), cycle_hour=0, ready_through_fh=0
        )
        is None
    )


# ---------------------------------------------------------------------------
# compare_frame end to end
# ---------------------------------------------------------------------------


@pytest.fixture()
def fast_run(tmp_path: Path) -> Path:
    _fast_run(tmp_path)
    return tmp_path


def test_agreeing_sources_pass_every_threshold(fast_run: Path) -> None:
    canary = _canary(fast_run, FakeReferenceSource(), fh=6)
    assert canary.status == canary_module.STATUS_OK, [
        result.to_json() for result in canary.results
    ]
    by_var = {result.var_id: result for result in canary.results}
    assert set(by_var) == {"tmp2m", "precip_total"}
    assert by_var["tmp2m"].unit in ("F", "°F")
    assert by_var["tmp2m"].reference_source == "ecmwf_ifs025"
    assert by_var["tmp2m"].n > 0
    for result in canary.results:
        assert not result.failed, result.to_json()
        assert abs(result.bias) <= canary_module.thresholds_for(result.var_id).max_abs_bias


def test_an_accumulation_frame_is_compared_as_an_increment_not_a_total(
    fast_run: Path,
) -> None:
    """The published series is run-cumulative; the reference is per-step.

    Comparing totals would need every reference step from FH000. The canary
    diffs the published frames instead, so a reference whose ONE step in the
    window equals the fast increment agrees exactly — and a reference carrying
    the run total instead does not.
    """
    agreeing = _canary(fast_run, FakeReferenceSource(), fh=6)
    precip = next(r for r in agreeing.results if r.var_id == "precip_total")
    assert not precip.failed
    # 3 hourly 0.5 mm steps into the FH003→FH006 window.
    assert precip.bias == pytest.approx(0.0, abs=0.004)

    as_total = _canary(
        fast_run, FakeReferenceSource(precip_mm_per_step=6 * STEP_PRECIP_MM), fh=6
    )
    precip_total = next(r for r in as_total.results if r.var_id == "precip_total")
    assert precip_total.failed
    assert "bias" in precip_total.failures
    # The fast increment is half the reference's, so the bias is one window.
    assert precip_total.bias == pytest.approx(-3 * STEP_PRECIP_MM * MM_TO_IN, abs=0.005)


def test_a_drifted_reference_trips_bias_and_synoptic_mae(fast_run: Path) -> None:
    canary = _canary(fast_run, FakeReferenceSource(temperature_c=17.0), fh=6)
    assert canary.status == canary_module.STATUS_FAILED
    tmp2m = next(result for result in canary.results if result.var_id == "tmp2m")
    assert set(tmp2m.failures) >= {"bias", "synoptic_mae"}
    # The reference reads 2 °C HIGH, so fast − reference is −3.6 °F.
    assert tmp2m.bias == pytest.approx(-2.0 * C_TO_F_SLOPE, abs=0.1)
    assert tmp2m.synoptic_mae == pytest.approx(2.0 * C_TO_F_SLOPE, abs=0.1)


def test_a_failing_canary_never_revokes_ownership(fast_run: Path) -> None:
    """Design §6: alert, do not auto-disable."""
    before = _state(fast_run).revoked_pairs()
    canary = _canary(fast_run, FakeReferenceSource(temperature_c=25.0), fh=6)
    assert canary.status == canary_module.STATUS_FAILED
    assert _state(fast_run).revoked_pairs() == before == frozenset()


def test_a_missing_reference_field_is_a_skip_not_a_failure(fast_run: Path) -> None:
    canary = _canary(
        fast_run, FakeReferenceSource(missing=frozenset({"temperature_2m"})), fh=6
    )
    assert {result.var_id for result in canary.results} == {"precip_total"}
    assert canary.status == canary_module.STATUS_OK


def test_a_reference_object_omitting_every_variable_is_retryable(
    fast_run: Path,
) -> None:
    """``fetch_frame_fields`` DROPS absent variables instead of raising.

    So a truncated or still-being-written reference object is indistinguishable
    from a legitimate omission at the fetch layer. Latching a skip on it would
    permanently forgo the run's only canary over a one-poll hiccup.
    """
    source = FakeReferenceSource(
        missing=frozenset({"temperature_2m", "precipitation"})
    )
    canary = _canary(fast_run, source, fh=6)
    assert canary.results == ()
    assert canary.status == canary_module.STATUS_ERROR
    assert "reference_field_missing" in canary.skip_reason


def test_an_unpublished_frame_is_a_retryable_skip_not_a_failure(fast_run: Path) -> None:
    """An unpublished frame may simply be a promote still in the retry queue.

    It is never a *failure* — nothing was compared — but it must not latch the
    run's one canary either, so it takes the retryable STATUS_ERROR path.
    """
    canary = _canary(fast_run, FakeReferenceSource(max_fh=400), fh=120)
    assert canary.results == ()
    assert canary.status == canary_module.STATUS_ERROR
    assert "frame_not_published" in canary.skip_reason


def test_a_terminal_skip_latches_rather_than_retrying(
    fast_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config-shaped skips will never resolve, so retrying them is pure cost."""
    monkeypatch.setattr(
        canary_module, "_display_prep_blocks", lambda model, var: "display_prep_group_2"
    )
    canary = _canary(fast_run, FakeReferenceSource(), fh=6)
    assert canary.results == ()
    assert canary.status == canary_module.STATUS_SKIPPED
    assert canary.skip_reason == "no_comparable_frames"


def test_skip_reasons_are_classified_as_terminal_or_retryable() -> None:
    terminal = canary_module.CanaryOutcome(None, "display_prep_shape:group_2")
    assert not terminal.retryable
    assert not canary_module.CanaryOutcome(None, "shape_mismatch").retryable
    assert canary_module.CanaryOutcome(None, "reference_field_missing").retryable
    assert canary_module.CanaryOutcome(None, "frame_not_published").retryable
    assert canary_module.CanaryOutcome(None, "no_overlapping_finite_cells").retryable


def test_an_upscaled_variable_is_excluded_because_its_binary_is_not_the_grid(
    fast_run: Path,
) -> None:
    """Display prep runs before packing, so an upscale changes the raster shape.

    Comparing such a variable would need the canary to reimplement display prep
    and then check itself against its own reimplementation. The guard is on the
    production shape classifier, so it tracks the config rather than a
    hand-maintained list of names.
    """
    # GFS precip_total carries upscale_factor=3 — shape group 2.
    assert canary_module._display_prep_blocks("gfs", "precip_total") == (
        "display_prep_group_2"
    )
    # Every ECMWF fast-owned variable is shape-preserving today (ECMWF's
    # snowfall_total entry is upscale_factor=1), so none of them are blocked.
    for var_id in _LAUNCH_VARS:
        assert canary_module._display_prep_blocks("ecmwf", var_id) == "", var_id


def test_a_blocked_variable_is_skipped_rather_than_compared(
    fast_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        canary_module, "_display_prep_blocks", lambda model, var: "display_prep_group_2"
    )
    outcome = canary_module.compare_frame(
        data_root=fast_run,
        model_id="ecmwf",
        run_dt=RUN_DT,
        run_id=RUN_ID,
        var_id="tmp2m",
        domain="global",
        fh=6,
        catalog=ECMWF_IFS_CATALOG,
        reference_catalog=canary_module.reference_catalog_for(ECMWF_IFS_CATALOG),
        om_source=FakeReferenceSource(),
    )
    assert outcome.result is None
    assert outcome.skip_reason.startswith("display_prep_shape")
    assert not outcome.retryable


def test_a_raising_reference_source_reports_an_error_not_a_crash(
    fast_run: Path,
) -> None:
    class _Boom(FakeReferenceSource):
        def fetch_frame_fields(self, *args, **kwargs):
            raise RuntimeError("bucket said no")

    canary = _canary(fast_run, _Boom(), fh=6)
    assert canary.status == canary_module.STATUS_ERROR
    assert "bucket said no" in canary.skip_reason


def test_reference_object_url_is_the_reference_products_run_directory() -> None:
    reference = canary_module.reference_catalog_for(ECMWF_IFS_CATALOG)
    url = canary_module._reference_object_url(
        reference_catalog=reference, run_dt=RUN_DT, fh=6, base_url=None
    )
    assert "/ecmwf_ifs025/2026/08/04/0000Z/2026-08-04T0600.om" in url


# ---------------------------------------------------------------------------
# Interaction with failover
# ---------------------------------------------------------------------------


def test_a_revoked_pair_is_dropped_from_the_comparison(fast_run: Path) -> None:
    """A revoked pair belongs to the delayed path now.

    Comparing it against the delayed reference would be comparing that source
    to itself — a guaranteed pass that says nothing.
    """
    state = _state(fast_run)
    state.revoke("tmp2m", "global", reason="test")
    state.save()

    canary = canary_module.run_canary_for_run(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=fast_run,
        run_dt=RUN_DT,
        run_id=RUN_ID,
        state=_state(fast_run),
        source=FakeReferenceSource(),
        fh=6,
    )
    assert {result.var_id for result in canary.results} == {"precip_total"}


def test_a_fully_revoked_run_is_skipped_cleanly(fast_run: Path) -> None:
    state = _state(fast_run)
    for var_id, domain in ownership.fast_owned_pairs("ecmwf"):
        state.revoke(var_id, domain, reason="test")
    state.save()

    canary = canary_module.run_canary_for_run(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=fast_run,
        run_dt=RUN_DT,
        run_id=RUN_ID,
        state=_state(fast_run),
        source=FakeReferenceSource(),
        fh=6,
    )
    assert canary.status == canary_module.STATUS_SKIPPED
    assert canary.skip_reason == "no_live_fast_owned_pairs"
    assert canary.results == ()


def test_the_canary_survives_a_failover_landing_between_its_own_reads(
    fast_run: Path,
) -> None:
    """A revocation mid-canary must not corrupt the run's state.

    The canary reads; the failover deadline writes. The one thing that must not
    happen is the canary's own state write clobbering the revocation.
    """
    state = _state(fast_run)
    canary = canary_module.run_canary_for_run(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=fast_run,
        run_dt=RUN_DT,
        run_id=RUN_ID,
        state=state,
        source=FakeReferenceSource(),
        fh=6,
    )
    assert canary.status == canary_module.STATUS_OK

    # Failover lands on disk while `state` is still the pre-failover object.
    other = _state(fast_run)
    other.revoke("precip_total", "global", reason="delayed_source_available")
    other.save()

    state.mark_canary(canary.to_json())
    state.save()

    replayed = _state(fast_run)
    assert replayed.is_revoked("precip_total", "global")
    assert replayed.canary_done


# ---------------------------------------------------------------------------
# Failover seams (design §8)
# ---------------------------------------------------------------------------


def _write_manifest(
    data_root: Path,
    *,
    var_id: str,
    frames: list[int],
    source_ranges: list[dict] | None,
    domain: str = "global",
) -> None:
    path = scheduler_module._manifest_path(
        data_root, "ecmwf", RUN_ID, region=domain
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict = {
        "expected_max_fh": max(frames) if frames else 0,
        "ready_through_fh": max(frames) if frames else None,
        "frames": [{"fh": fh} for fh in frames],
    }
    if source_ranges is not None:
        entry["source_ranges"] = source_ranges
    payload = {"model": "ecmwf", "run": RUN_ID, "variables": {var_id: entry}}
    path.write_text(json.dumps(payload))


def _write_published_frame(
    data_root: Path, *, var_id: str, fh: int, values: np.ndarray, domain: str = "global"
) -> None:
    run_root = (
        scheduler_module._published_model_root(data_root, "ecmwf", domain) / RUN_ID
    )
    grid_module.write_grid_frame_for_run_root(
        run_root=run_root,
        model="ecmwf",
        var=var_id,
        fh=fh,
        values=np.asarray(values, dtype=np.float32),
        bbox=[-180.0, -90.0, 180.0, 90.0],
    )


def _seams(data_root: Path):
    return canary_module.check_failover_seams(
        data_root=data_root,
        model_id="ecmwf",
        run_id=RUN_ID,
        domains=["global"],
        catalog=ECMWF_IFS_CATALOG,
    )


def test_a_homogeneous_run_records_no_seam(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        var_id="tmp2m",
        frames=[0, 3, 6],
        source_ranges=[
            {"from_fh": 0, "to_fh": 6, "source_id": "openmeteo", "generation": 1}
        ],
    )
    assert _seams(tmp_path) == ()


def test_a_delayed_only_variable_records_no_seam(tmp_path: Path) -> None:
    _write_manifest(tmp_path, var_id="tmp850", frames=[0, 3, 6], source_ranges=None)
    assert _seams(tmp_path) == ()


def test_an_instantaneous_seam_is_recorded_but_never_failed(tmp_path: Path) -> None:
    """A 9 km→0.25° seam is legitimate and is what the §8 label explains.

    Failing on it would train operators to ignore the metric.
    """
    _write_manifest(
        tmp_path,
        var_id="tmp2m",
        frames=[0, 3, 6, 9],
        source_ranges=[
            {"from_fh": 0, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    (seam,) = _seams(tmp_path)
    assert seam.var_id == "tmp2m"
    assert seam.seam_fh == 6
    assert seam.from_source == "openmeteo"
    assert seam.to_source == "delayed"
    assert seam.continuity_checked is False
    assert seam.ok is True


def test_an_accumulation_seam_passes_when_the_cumulative_series_keeps_rising(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        var_id="precip_total",
        frames=[3, 6],
        source_ranges=[
            {"from_fh": 3, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=3, values=np.full((8, 8), 0.10)
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=6, values=np.full((8, 8), 0.25)
    )
    (seam,) = _seams(tmp_path)
    assert seam.continuity_checked is True
    assert seam.ok is True
    assert seam.negative_jump_fraction == pytest.approx(0.0)


def test_an_accumulation_seam_fails_when_the_total_steps_backwards(
    tmp_path: Path,
) -> None:
    """A run-cumulative total that DROPS means two sources were mixed.

    ``enforce_failover_deadline`` purges an accumulation pair's fast frames so
    the delayed path rebuilds the whole series precisely to make this
    impossible; the check is the on-real-data proof of that invariant.
    """
    _write_manifest(
        tmp_path,
        var_id="precip_total",
        frames=[3, 6],
        source_ranges=[
            {"from_fh": 3, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=3, values=np.full((8, 8), 0.50)
    )
    dropped = np.full((8, 8), 0.50)
    dropped[0, :4] = 0.10
    _write_published_frame(tmp_path, var_id="precip_total", fh=6, values=dropped)

    (seam,) = _seams(tmp_path)
    assert seam.continuity_checked is True
    assert seam.ok is False
    assert seam.negative_jump_fraction == pytest.approx(4 / 64)
    assert seam.max_negative_jump == pytest.approx(0.40, abs=0.02)
    assert "mixed" in seam.note


def test_a_one_quantum_dip_is_tolerated_as_packing_noise(tmp_path: Path) -> None:
    """Two frames are quantised independently; one code of slack is not a bug."""
    _write_manifest(
        tmp_path,
        var_id="precip_total",
        frames=[3, 6],
        source_ranges=[
            {"from_fh": 3, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=3, values=np.full((8, 8), 0.50)
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=6, values=np.full((8, 8), 0.49)
    )
    (seam,) = _seams(tmp_path)
    assert seam.ok is True


def test_missing_seam_frames_are_reported_as_unverified_not_as_a_pass(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        var_id="precip_total",
        frames=[3, 6],
        source_ranges=[
            {"from_fh": 3, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    (seam,) = _seams(tmp_path)
    assert seam.continuity_checked is False
    assert "not verified" in seam.note


def test_a_failed_seam_fails_the_run_even_with_no_frame_comparisons(
    tmp_path: Path, short_ladder
) -> None:
    """A broken accumulation series does not become less broken because no
    frame comparison happened to be available.

    Regression: the status resolution used to test ``if not results`` first, so
    a failing seam on a run with zero comparable frames was downgraded to a
    silent skip — the one case where the seam check is the ONLY evidence.
    """
    _fast_run(tmp_path)
    _write_manifest(
        tmp_path,
        var_id="precip_total",
        frames=[3, 6],
        source_ranges=[
            {"from_fh": 3, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=3, values=np.full((8, 8), 0.50)
    )
    _write_published_frame(
        tmp_path, var_id="precip_total", fh=6, values=np.full((8, 8), 0.10)
    )
    # fh=900 is published by nobody, so every frame comparison skips.
    canary = _canary(tmp_path, FakeReferenceSource(), fh=900)
    assert canary.results == ()
    assert canary.status == canary_module.STATUS_FAILED
    assert [seam.var_id for seam in canary.failed_seams] == ["precip_total"]


def test_a_failed_seam_fails_the_run_alongside_passing_comparisons(
    fast_run: Path,
) -> None:
    """A green frame comparison must not mask a red seam.

    The 8×8 seam frames deliberately overwrite the fast-built precipitation
    frames, so precip's own comparison skips on a shape mismatch and ``tmp2m``
    is the passing comparison — which is exactly the arrangement under test.
    """
    _write_manifest(
        fast_run,
        var_id="precip_total",
        frames=[3, 6],
        source_ranges=[
            {"from_fh": 3, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    _write_published_frame(
        fast_run, var_id="precip_total", fh=3, values=np.full((8, 8), 0.50)
    )
    _write_published_frame(
        fast_run, var_id="precip_total", fh=6, values=np.full((8, 8), 0.10)
    )
    canary = _canary(fast_run, FakeReferenceSource(), fh=6)
    assert canary.failed_results == ()
    assert len(canary.failed_seams) == 1
    assert canary.status == canary_module.STATUS_FAILED


@pytest.mark.filterwarnings("ignore:Degrees of freedom <= 0:RuntimeWarning")
def test_zero_overlapping_cells_is_a_skip_never_a_failure(fast_run: Path) -> None:
    """All-NaN metrics are "no comparison", not "the comparison failed".

    Regression: ``_evaluate`` flags a non-finite bias and synoptic MAE as
    threshold failures, so an empty overlap used to surface as a canary FAILURE
    — contradicting ``compare_frame``'s own contract that an absent comparison
    is a skip.
    """
    all_nan = np.full(
        (REGULAR_025_SOURCE.nlat, REGULAR_025_SOURCE.nlon), np.nan, dtype=np.float32
    )

    class _AllNaN(FakeReferenceSource):
        def fetch_frame_fields(self, catalog, url, var_names, window="global", **kw):
            del catalog, url, window, kw
            return {name: all_nan.copy() for name in var_names}

    # The metric layer reports the empty overlap...
    n, bias, _mae, synoptic, _corr, notes = canary_module._metrics(
        np.full((8, 8), np.nan), np.full((8, 8), 1.0), coarsen_factor=1
    )
    assert n == 0 and "no_overlapping_finite_cells" in notes
    # ...and _evaluate WOULD call those NaNs failures, which is exactly why
    # compare_frame must not reach it.
    assert canary_module._evaluate(
        var_id="tmp2m", bias=bias, synoptic_mae=synoptic, corr=float("nan")
    ) == ("bias", "synoptic_mae")

    outcome = canary_module.compare_frame(
        data_root=fast_run,
        model_id="ecmwf",
        run_dt=RUN_DT,
        run_id=RUN_ID,
        var_id="tmp2m",
        domain="global",
        fh=6,
        catalog=ECMWF_IFS_CATALOG,
        reference_catalog=canary_module.reference_catalog_for(ECMWF_IFS_CATALOG),
        om_source=_AllNaN(),
    )
    assert outcome.result is None
    assert outcome.skip_reason == "no_overlapping_finite_cells"
    assert outcome.retryable

    canary = _canary(fast_run, _AllNaN(), fh=6)
    assert canary.status == canary_module.STATUS_ERROR
    assert canary.failed_results == ()


def test_the_run_canary_reports_seams_alongside_its_comparisons(
    fast_run: Path,
) -> None:
    _write_manifest(
        fast_run,
        var_id="tmp2m",
        frames=[0, 3, 6, 9],
        source_ranges=[
            {"from_fh": 0, "to_fh": 3, "source_id": "openmeteo", "generation": 1}
        ],
    )
    canary = _canary(fast_run, FakeReferenceSource(), fh=6)
    assert [seam.var_id for seam in canary.seams] == ["tmp2m"]
    assert canary.status == canary_module.STATUS_OK


# ---------------------------------------------------------------------------
# The scheduler trigger: once per run, both sources complete
# ---------------------------------------------------------------------------


@pytest.fixture()
def short_ladder(monkeypatch: pytest.MonkeyPatch):
    """Pin the production ladder to FH000–FH006 so a run can be 'complete'.

    Keeps production's own shape: accumulation and gust variables have no FH000
    frame, so their ladder starts at FH003 — a ladder that claimed FH000 for
    them would leave their contiguity frontier permanently ``None``.
    """
    real = subloop_module._scheduled_fhs

    def _fhs(plugin, var_id, cycle_hour):
        return tuple(fh for fh in real(plugin, var_id, cycle_hour) if fh <= 6)

    monkeypatch.setattr(subloop_module, "_scheduled_fhs", _fhs)


def _mark_delayed_complete(data_root: Path) -> None:
    """Add a delayed-built (no ``source_ranges``) variable at its horizon."""
    path = scheduler_module._manifest_path(data_root, "ecmwf", RUN_ID, region="global")
    payload = json.loads(path.read_text())
    payload["variables"]["tmp850"] = {
        "expected_max_fh": 6,
        "ready_through_fh": 6,
        "frames": [{"fh": 0}, {"fh": 3}, {"fh": 6}],
    }
    path.write_text(json.dumps(payload))


def _maybe(data_root: Path, source: FakeReferenceSource):
    return canary_module.maybe_run_canary(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=RUN_DT,
        source=source,
    )


def test_the_trigger_is_inert_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ownership.ENV_FASTPATH_MODELS, raising=False)
    assert _maybe(tmp_path, FakeReferenceSource()) is None
    assert not (tmp_path / "staging").exists()


def test_the_trigger_waits_for_the_fast_side_to_finish(
    tmp_path: Path, short_ladder
) -> None:
    _fast_run(tmp_path, max_fh=3)  # frontier stops at FH003, horizon is FH006
    _mark_delayed_complete(tmp_path)
    assert _maybe(tmp_path, FakeReferenceSource()) is None
    assert not _state(tmp_path).canary_done


def test_the_trigger_waits_for_the_delayed_side_to_finish(
    tmp_path: Path, short_ladder
) -> None:
    _fast_run(tmp_path)
    # The manifest holds only fast-written variables, so nothing proves the
    # delayed build for this cycle is done.
    assert _maybe(tmp_path, FakeReferenceSource()) is None
    assert not _state(tmp_path).canary_done


def test_the_trigger_fires_once_both_sources_are_complete(
    tmp_path: Path, short_ladder
) -> None:
    _fast_run(tmp_path)
    _mark_delayed_complete(tmp_path)
    canary = _maybe(tmp_path, FakeReferenceSource())
    assert canary is not None
    assert canary.status == canary_module.STATUS_OK
    assert {result.var_id for result in canary.results} == {"tmp2m", "precip_total"}


def test_the_trigger_runs_at_most_once_per_run(tmp_path: Path, short_ladder) -> None:
    _fast_run(tmp_path)
    _mark_delayed_complete(tmp_path)
    source = FakeReferenceSource()
    assert _maybe(tmp_path, source) is not None
    fetches = len(source.fetched)
    assert fetches > 0

    assert _maybe(tmp_path, source) is None
    assert len(source.fetched) == fetches, "a latched canary must not refetch"


def test_the_latch_survives_a_reload(tmp_path: Path, short_ladder) -> None:
    _fast_run(tmp_path)
    _mark_delayed_complete(tmp_path)
    _maybe(tmp_path, FakeReferenceSource())
    reloaded = _state(tmp_path)
    assert reloaded.canary_done
    assert reloaded.canary["status"] == canary_module.STATUS_OK
    assert reloaded.canary["run"] == RUN_ID


def test_a_transient_error_does_not_latch(tmp_path: Path, short_ladder) -> None:
    """A bucket blip must not consume the run's single canary attempt."""
    _fast_run(tmp_path)
    _mark_delayed_complete(tmp_path)

    class _Boom(FakeReferenceSource):
        def fetch_frame_fields(self, *args, **kwargs):
            raise RuntimeError("transient")

    first = _maybe(tmp_path, _Boom())
    assert first is not None and first.status == canary_module.STATUS_ERROR
    assert not _state(tmp_path).canary_done

    second = _maybe(tmp_path, FakeReferenceSource())
    assert second is not None and second.status == canary_module.STATUS_OK


def test_a_fully_revoked_run_latches_a_skip_without_fetching(
    tmp_path: Path, short_ladder
) -> None:
    _fast_run(tmp_path)
    state = _state(tmp_path)
    for var_id, domain in ownership.fast_owned_pairs("ecmwf"):
        state.revoke(var_id, domain, reason="delayed_source_available")
    state.save()

    source = FakeReferenceSource()
    canary = _maybe(tmp_path, source)
    assert canary is not None
    assert canary.status == canary_module.STATUS_SKIPPED
    assert canary.skip_reason == "no_live_fast_owned_pairs"
    assert source.fetched == []
    assert _state(tmp_path).canary_done
    assert _maybe(tmp_path, source) is None


def test_the_trigger_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("state exploded")

    monkeypatch.setattr(FastpathRunState, "load_or_create", staticmethod(_boom))
    assert _maybe(tmp_path, FakeReferenceSource()) is None


def test_the_scheduler_hook_runs_the_canary_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook wires the canary in without letting it break the scheduler."""
    calls: list[str] = []

    def _boom(**_kwargs):
        calls.append("canary")
        raise RuntimeError("canary exploded")

    monkeypatch.setattr(canary_module, "maybe_run_canary", _boom)
    monkeypatch.setattr(
        subloop_module, "run_fastpath_pass", lambda **_kwargs: subloop_module.FastpathPassResult(model_id="ecmwf")
    )
    monkeypatch.setattr(
        subloop_module, "enforce_failover_deadline", lambda **_kwargs: frozenset()
    )
    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
    )
    assert calls == ["canary"]
