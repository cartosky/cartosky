"""Accumulation-ledger tests for the Open-Meteo fast path (WP2).

Methodology mirrors prototype Phase 3 (``docs/ECMWF_OPENMETEO_9KM_PROTOTYPE_
2026-08-04.md`` §4), now as a regression test: synthetic per-step fields whose
value *is* the step's forecast hour, so every expected run-cumulative has a
closed form (``sum`` of the constituent fhs) and any error is exactly
attributable to a specific window. The two cadence transitions — FH90
(hourly → native 3 h) and FH144 (3 h → 6 h) — are asserted explicitly.

Ladder membership is never restated here: it comes from WP1's
``aggregation_boundaries``, which is what the ledger itself consumes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app.services.sources.openmeteo.accumulation import (
    CHECKPOINT_SCHEMA_VERSION,
    SOURCE_ID,
    AccumulationLedger,
    CheckpointError,
    CheckpointMismatch,
    MissingStepsError,
    OutOfOrderStepError,
    RetentionError,
    cadence_version,
)
from app.services.sources.openmeteo.catalog import (
    ECMWF_IFS_CATALOG,
    CadenceSegment,
    aggregation_boundaries,
    expected_fhs,
)

PRECIP = "precipitation"
SNOW = "snowfall_water_equivalent"
ACCUM_VARS = (PRECIP, SNOW)

RUN_00Z = datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)
RUN_18Z = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)

#: Three cells is enough to prove element-wise behaviour without hiding a
#: broadcasting bug behind a scalar.
POINTS = 3


def _step_fields(fh: int, *, points: int = POINTS) -> dict[str, np.ndarray]:
    """Per-step values with a closed-form sum: precip == fh, snow == fh/10."""
    return {
        PRECIP: np.full(points, float(fh), dtype=np.float32),
        SNOW: np.full(points, fh / 10.0, dtype=np.float32),
    }


def _ledger(run_dt: datetime = RUN_00Z, **kwargs) -> AccumulationLedger:
    return AccumulationLedger(ECMWF_IFS_CATALOG, run_dt, **kwargs)


def _feed_through(ledger: AccumulationLedger, through_fh: int) -> None:
    for fh in ledger.expected_source_fhs:
        if fh > through_fh:
            break
        ledger.ingest_step(fh, _step_fields(fh))


def _expected_total(cycle_hour: int, published_fh: int) -> float:
    """Closed form: sum of every .om step fh at or below ``published_fh``."""
    return float(
        sum(fh for fh in expected_fhs(ECMWF_IFS_CATALOG, cycle_hour) if 0 < fh <= published_fh)
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_ledger_tracks_exactly_the_catalog_accumulation_variables() -> None:
    ledger = _ledger()

    assert ledger.var_names == ACCUM_VARS
    assert ledger.model_id == "ecmwf"
    assert ledger.cycle_hour == 0
    assert ledger.last_ingested_fh == 0
    assert ledger.frontier_published_fh == 0
    assert ledger.ready_published_fhs() == []


def test_ledger_uses_wp1_ladder_verbatim() -> None:
    ledger = _ledger()
    windows = aggregation_boundaries(ECMWF_IFS_CATALOG, 0)

    assert ledger.published_fhs == tuple(w.target_fh for w in windows)
    assert ledger.expected_source_fhs == tuple(
        fh for fh in expected_fhs(ECMWF_IFS_CATALOG, 0) if fh > 0
    )
    assert ledger.window_for(90).source_fhs == (88, 89, 90)


def test_ledger_rejects_instantaneous_and_disabled_variables() -> None:
    with pytest.raises(ValueError, match="instantaneous"):
        _ledger(var_names=["temperature_2m"])
    with pytest.raises(ValueError, match="disabled"):
        _ledger(var_names=["showers"])
    with pytest.raises(KeyError, match="cloud_cover"):
        _ledger(var_names=["cloud_cover"])


def test_ledger_can_track_a_single_variable() -> None:
    ledger = _ledger(var_names=[PRECIP])

    assert ledger.var_names == (PRECIP,)
    ledger.ingest_step(1, {PRECIP: np.full(POINTS, 1.0, dtype=np.float32)})
    assert ledger.last_ingested_fh == 1


# ---------------------------------------------------------------------------
# In-order enforcement
# ---------------------------------------------------------------------------


def test_fh000_is_never_ingested() -> None:
    ledger = _ledger()

    with pytest.raises(ValueError, match="FH000 carries no accumulation"):
        ledger.ingest_step(0, _step_fields(0))


def test_out_of_order_step_raises() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    with pytest.raises(OutOfOrderStepError, match="ascending"):
        ledger.ingest_step(2, _step_fields(2))


def test_duplicate_step_raises() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    with pytest.raises(OutOfOrderStepError, match="ascending"):
        ledger.ingest_step(3, _step_fields(3))


def test_gap_raises_and_names_the_missing_steps() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    with pytest.raises(OutOfOrderStepError, match=r"skips 2 step\(s\) \[4, 5\]"):
        ledger.ingest_step(6, _step_fields(6))
    # The ledger is untouched: the caller may still wait for FH004.
    assert ledger.next_expected_fh() == 4
    ledger.ingest_step(4, _step_fields(4))
    assert ledger.last_ingested_fh == 4


def test_step_outside_the_cycle_ladder_raises() -> None:
    ledger = _ledger(RUN_18Z)  # short cycle, horizon FH144

    _feed_through(ledger, 144)
    with pytest.raises(ValueError, match="not an expected .om step"):
        ledger.ingest_step(150, _step_fields(150))


def test_ingest_requires_every_tracked_variable_and_no_extras() -> None:
    ledger = _ledger()

    with pytest.raises(ValueError, match="missing accumulation variable"):
        ledger.ingest_step(1, {PRECIP: np.zeros(POINTS)})
    fields = _step_fields(1)
    fields["showers"] = np.zeros(POINTS)
    with pytest.raises(ValueError, match="untracked variable"):
        ledger.ingest_step(1, fields)


def test_shape_must_stay_consistent() -> None:
    ledger = _ledger()
    ledger.ingest_step(1, _step_fields(1))

    with pytest.raises(ValueError, match="ledger established"):
        ledger.ingest_step(2, _step_fields(2, points=POINTS + 1))


# ---------------------------------------------------------------------------
# Ladder assembly below FH90
# ---------------------------------------------------------------------------


def test_first_published_frame_sums_the_first_three_hourly_steps() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    totals = ledger.cumulative_at(3)

    assert np.allclose(totals[PRECIP], 1 + 2 + 3)
    assert np.allclose(totals[SNOW], (1 + 2 + 3) / 10.0)
    assert totals[PRECIP].dtype == np.float32


def test_cumulative_is_run_total_not_window_increment() -> None:
    ledger = _ledger()
    _feed_through(ledger, 12)

    assert np.allclose(ledger.cumulative_at(12)[PRECIP], _expected_total(0, 12))
    assert _expected_total(0, 12) == sum(range(1, 13))


@pytest.mark.parametrize("published_fh", [3, 6, 24, 60, 90])
def test_hourly_regime_matches_the_closed_form(published_fh: int) -> None:
    ledger = _ledger()
    _feed_through(ledger, published_fh)

    totals = ledger.cumulative_at(published_fh)

    assert np.allclose(totals[PRECIP], _expected_total(0, published_fh))
    assert np.allclose(totals[SNOW], _expected_total(0, published_fh) / 10.0)


def test_partial_window_is_not_publishable() -> None:
    ledger = _ledger()
    _feed_through(ledger, 5)  # FH006's window needs 4, 5, 6

    assert ledger.ready_published_fhs() == [3]
    with pytest.raises(MissingStepsError, match=r"missing .om steps \[6\]"):
        ledger.cumulative_at(6)


def test_requesting_a_far_future_frame_lists_every_missing_step() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    with pytest.raises(MissingStepsError) as excinfo:
        ledger.cumulative_at(9)

    message = str(excinfo.value)
    assert "FH009" in message
    assert "[4, 5, 6, 7, 8, 9]" in message


def test_non_published_hours_are_rejected() -> None:
    ledger = _ledger()
    _feed_through(ledger, 12)

    with pytest.raises(ValueError, match="not a published frame hour"):
        ledger.cumulative_at(10)
    with pytest.raises(ValueError, match="not a published frame hour"):
        ledger.cumulative_at(0)


def test_ledger_retains_only_the_frontier() -> None:
    ledger = _ledger()
    _feed_through(ledger, 6)

    assert np.allclose(ledger.cumulative_at(6)[PRECIP], _expected_total(0, 6))
    with pytest.raises(RetentionError, match="behind the ledger frontier"):
        ledger.cumulative_at(3)


# ---------------------------------------------------------------------------
# Cadence transitions
# ---------------------------------------------------------------------------


def test_fh90_transition_last_hourly_window_then_native_3h_step() -> None:
    ledger = _ledger()
    _feed_through(ledger, 90)

    # Last hourly window: (88, 89, 90) — three .om steps into one frame.
    assert ledger.window_for(90).source_fhs == (88, 89, 90)
    hourly_total = float(sum(range(1, 91)))
    assert np.allclose(ledger.cumulative_at(90)[PRECIP], hourly_total)

    # First native-3 h step: the ladders coincide 1:1 from here.
    ledger.ingest_step(93, _step_fields(93))
    assert ledger.window_for(93).source_fhs == (93,)
    assert np.allclose(ledger.cumulative_at(93)[PRECIP], hourly_total + 93)
    assert np.allclose(ledger.cumulative_at(93)[SNOW], (hourly_total + 93) / 10.0)

    ledger.ingest_step(96, _step_fields(96))
    assert np.allclose(ledger.cumulative_at(96)[PRECIP], hourly_total + 93 + 96)


def test_fh144_transition_3h_to_6h() -> None:
    ledger = _ledger()
    _feed_through(ledger, 144)

    total_144 = _expected_total(0, 144)
    assert ledger.window_for(144).source_fhs == (144,)
    assert np.allclose(ledger.cumulative_at(144)[PRECIP], total_144)

    ledger.ingest_step(150, _step_fields(150))
    assert ledger.window_for(150).source_fhs == (150,)
    assert np.allclose(ledger.cumulative_at(150)[PRECIP], total_144 + 150)

    ledger.ingest_step(156, _step_fields(156))
    assert np.allclose(ledger.cumulative_at(156)[PRECIP], total_144 + 150 + 156)


def test_full_00z_run_reaches_the_360h_horizon() -> None:
    ledger = _ledger()
    boundaries = set(ledger.published_fhs)
    seen: list[int] = []
    for fh in ledger.expected_source_fhs:
        ledger.ingest_step(fh, _step_fields(fh))
        if fh in boundaries:
            assert np.allclose(ledger.cumulative_at(fh)[PRECIP], _expected_total(0, fh))
            seen.append(fh)

    assert ledger.last_ingested_fh == 360
    assert len(seen) == 84  # 48 three-hourly + 36 six-hourly frames
    assert np.allclose(ledger.cumulative_at(360)[PRECIP], _expected_total(0, 360))


def test_short_cycle_horizon_stops_at_fh144() -> None:
    ledger = _ledger(RUN_18Z)

    assert ledger.published_fhs[-1] == 144
    assert len(ledger.published_fhs) == 48
    assert ledger.expected_source_fhs[-1] == 144
    _feed_through(ledger, 144)

    assert np.allclose(ledger.cumulative_at(144)[PRECIP], _expected_total(18, 144))
    assert ledger.next_expected_fh() is None
    # 06z has the same short ladder.
    assert _ledger(RUN_18Z.replace(hour=6)).published_fhs == ledger.published_fhs


# ---------------------------------------------------------------------------
# ready_published_fhs progression
# ---------------------------------------------------------------------------


def test_ready_published_fhs_advances_with_the_stream() -> None:
    ledger = _ledger()
    progression: list[tuple[int, list[int]]] = []
    for fh in range(1, 10):
        ledger.ingest_step(fh, _step_fields(fh))
        progression.append((fh, ledger.ready_published_fhs()))

    assert progression == [
        (1, []),
        (2, []),
        (3, [3]),
        (4, [3]),
        (5, [3]),
        (6, [6]),
        (7, [6]),
        (8, [6]),
        (9, [9]),
    ]


# ---------------------------------------------------------------------------
# NaN semantics
# ---------------------------------------------------------------------------


def test_nan_propagates_to_every_later_cumulative() -> None:
    ledger = _ledger()
    for fh in (1, 2, 3):
        fields = _step_fields(fh)
        if fh == 2:
            fields[PRECIP] = np.array([1.0, np.nan, 3.0], dtype=np.float32)
        ledger.ingest_step(fh, fields)

    first = ledger.cumulative_at(3)
    assert np.isnan(first[PRECIP][1])
    assert not np.isnan(first[PRECIP][0])
    assert not np.isnan(first[SNOW]).any()  # only the poisoned var is affected

    for fh in (4, 5, 6):
        ledger.ingest_step(fh, _step_fields(fh))

    later = ledger.cumulative_at(6)
    assert np.isnan(later[PRECIP][1])  # never silently zero-filled
    assert np.allclose(later[PRECIP][0], 1 + 1 + 3 + 4 + 5 + 6)


# ---------------------------------------------------------------------------
# Float discipline
# ---------------------------------------------------------------------------


def test_running_sums_are_float64_and_exposed_as_float32() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    for array in ledger.state_arrays():
        assert array.dtype == np.float64
    for values in ledger.cumulative_at(3).values():
        assert values.dtype == np.float32


def test_float64_accumulation_beats_float32_over_a_full_run() -> None:
    """36+ small additions into a large total — the reason for float64."""
    increments = np.float64(0.07)
    ledger = _ledger(var_names=[PRECIP])
    naive = np.float32(0.0)
    for fh in ledger.expected_source_fhs:
        ledger.ingest_step(fh, {PRECIP: np.full(POINTS, increments, dtype=np.float32)})
        naive = np.float32(naive + np.float32(increments))

    exact = float(np.float32(increments)) * len(ledger.expected_source_fhs)
    ledger_total = float(ledger.cumulative_at(360)[PRECIP][0])

    assert abs(ledger_total - exact) <= abs(float(naive) - exact)
    assert abs(ledger_total - exact) < 1e-4


def test_returned_arrays_are_copies_not_ledger_state() -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)

    totals = ledger.cumulative_at(3)
    totals[PRECIP][:] = -999.0

    assert np.allclose(ledger.cumulative_at(3)[PRECIP], 6.0)


# ---------------------------------------------------------------------------
# Memory shape
# ---------------------------------------------------------------------------


def test_ledger_holds_two_arrays_per_variable_regardless_of_step_count() -> None:
    ledger = _ledger()

    _feed_through(ledger, 3)
    after_three_steps = len(ledger.state_arrays())
    _feed_through_rest = [
        ledger.ingest_step(fh, _step_fields(fh))
        for fh in ledger.expected_source_fhs
        if fh > 3
    ]

    assert after_three_steps == 2 * len(ACCUM_VARS)
    assert len(ledger.state_arrays()) == 2 * len(ACCUM_VARS)
    assert len(_feed_through_rest) > 100  # a lot of steps, still 4 arrays
    assert sum(array.nbytes for array in ledger.state_arrays()) == 4 * POINTS * 8


# ---------------------------------------------------------------------------
# Cadence version
# ---------------------------------------------------------------------------


def test_cadence_version_is_stable_and_ladder_sensitive() -> None:
    from dataclasses import replace

    version = cadence_version(ECMWF_IFS_CATALOG, 0)

    assert version == cadence_version(ECMWF_IFS_CATALOG, 0)
    assert len(version) == 16
    # Different cycle ladder → different version.
    assert cadence_version(ECMWF_IFS_CATALOG, 18) != version
    # Different target ladder → different version.
    hourly = replace(
        ECMWF_IFS_CATALOG,
        target_cadence=(
            CadenceSegment(step_hours=1, through_fh=90),
            CadenceSegment(step_hours=3, through_fh=144),
            CadenceSegment(step_hours=6, through_fh=360),
        ),
    )
    assert cadence_version(hourly, 0) != version


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


def test_checkpoint_round_trip_is_bit_identical_to_a_cold_ledger(tmp_path: Path) -> None:
    warm = _ledger(domain="na")
    _feed_through(warm, 24)
    path = warm.save_checkpoint(tmp_path)
    assert path.exists()

    resumed = AccumulationLedger.load_checkpoint(
        tmp_path, ECMWF_IFS_CATALOG, RUN_00Z, domain="na"
    )
    assert resumed.last_ingested_fh == 24
    assert resumed.frontier_published_fh == 24

    for fh in range(25, 94):
        if fh in resumed.expected_source_fhs:
            resumed.ingest_step(fh, _step_fields(fh))

    cold = _ledger(domain="na")
    _feed_through(cold, 93)

    assert resumed.last_ingested_fh == cold.last_ingested_fh == 93
    for name in ACCUM_VARS:
        # Bit-identical, not merely close: same float64 additions in the same
        # order on both paths.
        assert np.array_equal(resumed.cumulative_at(93)[name], cold.cumulative_at(93)[name])
        assert (
            resumed.cumulative_at(93)[name].tobytes()
            == cold.cumulative_at(93)[name].tobytes()
        )


def test_checkpoint_mid_window_resumes_the_partial_sum(tmp_path: Path) -> None:
    """The seam falls inside a window: FH004/FH005 ingested, FH006 pending."""
    warm = _ledger()
    _feed_through(warm, 5)
    warm.save_checkpoint(tmp_path)

    resumed = AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)
    assert resumed.last_ingested_fh == 5
    assert resumed.frontier_published_fh == 3
    assert resumed.next_expected_fh() == 6

    resumed.ingest_step(6, _step_fields(6))

    assert np.allclose(resumed.cumulative_at(6)[PRECIP], _expected_total(0, 6))


def test_checkpoint_survives_the_fh90_seam(tmp_path: Path) -> None:
    warm = _ledger()
    _feed_through(warm, 90)
    warm.save_checkpoint(tmp_path)

    resumed = AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)
    resumed.ingest_step(93, _step_fields(93))

    assert np.allclose(resumed.cumulative_at(93)[PRECIP], float(sum(range(1, 91))) + 93)


def test_checkpoint_is_written_atomically_and_leaves_no_tmp(tmp_path: Path) -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)
    path = ledger.save_checkpoint(tmp_path)

    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    assert path.name == f"{SOURCE_ID}-ecmwf-20260804T0000Z-all.accum.npz"


def test_checkpoint_meta_carries_the_full_key(tmp_path: Path) -> None:
    ledger = _ledger(domain="global")
    _feed_through(ledger, 6)
    path = ledger.save_checkpoint(tmp_path)

    with np.load(path, allow_pickle=False) as payload:
        meta = json.loads(str(payload["__meta__"]))

    assert meta == {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "model_id": "ecmwf",
        "run": "20260804T0000Z",
        "cycle_hour": 0,
        "domain": "global",
        "cadence_version": cadence_version(ECMWF_IFS_CATALOG, 0),
        "var_names": list(ACCUM_VARS),
        "last_ingested_fh": 6,
        "frontier_published_fh": 6,
    }


def test_checkpoint_refuses_a_different_run(tmp_path: Path) -> None:
    ledger = _ledger()
    _feed_through(ledger, 6)
    ledger.save_checkpoint(tmp_path)

    # A different run has a different filename, so it simply is not there.
    with pytest.raises(CheckpointError, match="no accumulation checkpoint"):
        AccumulationLedger.load_checkpoint(
            tmp_path, ECMWF_IFS_CATALOG, RUN_00Z.replace(day=5)
        )

    # And if a stale payload is moved under the new run's name, the embedded
    # key catches it.
    stale = ledger.checkpoint_path(tmp_path)
    victim = _ledger(RUN_00Z.replace(day=5))
    stale.rename(victim.checkpoint_path(tmp_path))
    with pytest.raises(CheckpointMismatch, match="run="):
        AccumulationLedger.load_checkpoint(
            tmp_path, ECMWF_IFS_CATALOG, RUN_00Z.replace(day=5)
        )


def test_checkpoint_refuses_a_different_domain(tmp_path: Path) -> None:
    ledger = _ledger(domain="na")
    _feed_through(ledger, 6)
    written = ledger.save_checkpoint(tmp_path)
    written.rename(_ledger(domain="global").checkpoint_path(tmp_path))

    with pytest.raises(CheckpointMismatch, match="domain="):
        AccumulationLedger.load_checkpoint(
            tmp_path, ECMWF_IFS_CATALOG, RUN_00Z, domain="global"
        )


def test_checkpoint_refuses_a_different_cadence_version(tmp_path: Path) -> None:
    ledger = _ledger()
    _feed_through(ledger, 6)
    path = ledger.save_checkpoint(tmp_path)

    with np.load(path, allow_pickle=False) as payload:
        arrays = {key: np.asarray(payload[key]) for key in payload.files}
    meta = json.loads(str(arrays.pop("__meta__")))
    meta["cadence_version"] = "deadbeefdeadbeef"
    arrays["__meta__"] = np.array(json.dumps(meta, sort_keys=True, separators=(",", ":")))
    np.savez_compressed(path, **arrays)

    with pytest.raises(CheckpointMismatch, match="cadence_version="):
        AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)


def test_checkpoint_refuses_a_different_variable_set(tmp_path: Path) -> None:
    ledger = _ledger(var_names=[PRECIP])
    for fh in (1, 2, 3, 4, 5, 6):
        ledger.ingest_step(fh, {PRECIP: np.full(POINTS, float(fh), dtype=np.float32)})
    ledger.save_checkpoint(tmp_path)

    with pytest.raises(CheckpointMismatch, match="tracks"):
        AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)


def test_checkpoint_refuses_a_corrupted_file(tmp_path: Path) -> None:
    ledger = _ledger()
    _feed_through(ledger, 6)
    path = ledger.save_checkpoint(tmp_path)
    payload = bytearray(path.read_bytes())
    path.write_bytes(payload[: len(payload) // 2])  # truncated mid-archive

    with pytest.raises(CheckpointError, match="unreadable accumulation checkpoint"):
        AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)


def test_checkpoint_refuses_a_non_npz_file(tmp_path: Path) -> None:
    ledger = _ledger()
    ledger.checkpoint_path(tmp_path).write_text("not an npz")

    with pytest.raises(CheckpointError, match="unreadable accumulation checkpoint"):
        AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)


def test_missing_checkpoint_raises_rather_than_returning_empty(tmp_path: Path) -> None:
    with pytest.raises(CheckpointError, match="no accumulation checkpoint"):
        AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)


def test_empty_ledger_round_trips(tmp_path: Path) -> None:
    _ledger().save_checkpoint(tmp_path)

    resumed = AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)

    assert resumed.last_ingested_fh == 0
    assert resumed.ready_published_fhs() == []
    resumed.ingest_step(1, _step_fields(1))
    assert resumed.last_ingested_fh == 1


def test_checkpoint_overwrite_is_idempotent(tmp_path: Path) -> None:
    ledger = _ledger()
    _feed_through(ledger, 3)
    ledger.save_checkpoint(tmp_path)
    _feed_through_more = [ledger.ingest_step(fh, _step_fields(fh)) for fh in (4, 5, 6)]
    ledger.save_checkpoint(tmp_path)

    resumed = AccumulationLedger.load_checkpoint(tmp_path, ECMWF_IFS_CATALOG, RUN_00Z)

    assert len(_feed_through_more) == 3
    assert resumed.last_ingested_fh == 6
    assert np.allclose(resumed.cumulative_at(6)[PRECIP], _expected_total(0, 6))
    assert len(list(tmp_path.iterdir())) == 1
