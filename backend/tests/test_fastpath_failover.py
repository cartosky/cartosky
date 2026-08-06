"""Failover state machine + polling (fast-path design §6, WP3).

The interleavings design §6 asks to be tested explicitly: stall→failover,
resume-during-failover, and retry-after-partial-write. Ownership is pinned to
the ``global`` 0.25° target for speed; the mechanism is domain-agnostic and the
NA target is covered in ``test_fastpath_subloop.py``.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.registry import MODEL_REGISTRY
from app.services import scheduler as scheduler_module
from app.services.fastpath import ownership
from app.services.fastpath import subloop as subloop_module
from app.services.fastpath.state import OWNER_DELAYED, OWNER_FAST, FastpathRunState
from app.services.sources.openmeteo.catalog import ECMWF_IFS_CATALOG

from tests.test_fastpath_subloop import (  # reuse the fake bucket
    RUN_DT,
    RUN_ID,
    FakeSource,
    _LAUNCH_VARS,
    _frame_exists,
    _sidecar,
)

ACCUMULATION_VARS = {"precip_total", "snowfall_total"}


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


def _run_pass(data_root: Path, source: FakeSource, **kwargs):
    return subloop_module.run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=RUN_DT,
        source=source,
        **kwargs,
    )


def _failover(
    data_root: Path,
    *,
    delayed_available: bool = True,
    stalled: bool = True,
    now: datetime | None = None,
    grace_seconds: float | None = None,
):
    """Run the deadline.

    ``stalled=True`` (the default) evaluates it an hour in the future, past the
    default grace window, so tests about revocation *mechanics* still see a
    revocation. The stall gate itself is covered by its own tests below —
    trailing alone must never be enough.
    """
    if now is None and stalled:
        now = datetime.now(timezone.utc) + timedelta(hours=1)
    return subloop_module.enforce_failover_deadline(
        plugin=_plugin(),
        model_id="ecmwf",
        run_dt=RUN_DT,
        data_root=data_root,
        delayed_available=delayed_available,
        now=now,
        grace_seconds=grace_seconds,
    )


def _age_state(data_root: Path, *, seconds: float) -> None:
    """Backdate the run's progress clock, simulating an outage already underway."""
    state = _state(data_root)
    stale = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    stamp = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    state.created_at = stamp
    state.last_progress_at = stamp
    state.save()
    # save() merges disk progress forward, so re-assert the backdate on disk.
    payload = json.loads(state.path.read_text())
    payload["created_at"] = stamp
    payload["last_progress_at"] = stamp
    state.path.write_text(json.dumps(payload))


def _state(data_root: Path) -> FastpathRunState:
    return FastpathRunState.load_or_create(
        data_root=data_root, model="ecmwf", run_id=RUN_ID
    )


# ---------------------------------------------------------------------------
# The deadline
# ---------------------------------------------------------------------------


def test_no_revocation_while_the_delayed_source_lacks_the_run(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    assert _failover(tmp_path, delayed_available=False) == frozenset()
    assert _state(tmp_path).revoked_pairs() == frozenset()


def test_stall_then_failover_revokes_trailing_pairs(tmp_path: Path) -> None:
    """Design §6 row 1: the bucket stalls, fast pairs stop advancing, and at
    the delayed source's availability the deadline fires."""
    source = FakeSource(max_fh=12, fail_after_fh=6)
    result = _run_pass(tmp_path, source)
    assert result.stalled, "a mid-run bucket stall must surface as a stall"

    before = _state(tmp_path)
    assert before.pair("tmp2m", "global").owner == OWNER_FAST
    assert before.pair("tmp2m", "global").generation == 1
    assert before.pair("tmp2m", "global").ready_through_fh == 6

    revoked = _failover(tmp_path)

    assert revoked == ownership.fast_owned_pairs("ecmwf")
    after = _state(tmp_path)
    for var_id, domain in sorted(revoked):
        entry = after.pair(var_id, domain)
        assert entry.owner == OWNER_DELAYED
        assert entry.generation == 2, "the generation must bump on revocation"
        assert entry.revoke_reason == "delayed_source_available"
        assert entry.revoked_at
        assert entry.delayed_rebuild_required is (var_id in ACCUMULATION_VARS)


def test_a_complete_pair_survives_the_deadline(tmp_path: Path) -> None:
    """Only pairs whose ``ready_through_fh`` TRAILS the horizon are revoked."""
    plugin = _plugin()
    state = _state(tmp_path)
    expected = plugin.scheduled_fhs_for_var("tmp2m", RUN_DT.hour)
    for fh in expected:
        state.record_frame("tmp2m", "global", fh, expected_fhs=expected)
    state.save()

    revoked = _failover(tmp_path)

    assert ("tmp2m", "global") not in revoked
    assert _state(tmp_path).pair("tmp2m", "global").owner == OWNER_FAST
    # Its untouched siblings still get revoked.
    assert ("dp2m", "global") in revoked


def test_failover_is_idempotent(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    first = _failover(tmp_path)
    assert first
    assert _failover(tmp_path) == frozenset(), "a second deadline must be a no-op"
    assert _state(tmp_path).pair("tmp2m", "global").generation == 2


# ---------------------------------------------------------------------------
# Resume during failover
# ---------------------------------------------------------------------------


def test_a_resumed_fast_loop_refuses_a_revoked_pair(tmp_path: Path) -> None:
    """Design §6: "a fast source that resumes after revocation finds its
    generation stale and stops"."""
    _run_pass(tmp_path, FakeSource(max_fh=6))
    _failover(tmp_path)

    resumed = _run_pass(tmp_path, FakeSource(max_fh=12))

    assert resumed.active_pairs == ()
    assert resumed.frames_written == 0
    assert set(resumed.revoked_pairs) == set(ownership.fast_owned_pairs("ecmwf"))
    # Nothing new appeared past the seam.
    assert not _frame_exists(tmp_path, "tmp2m", 9)


def test_a_partial_revocation_only_stops_the_revoked_pair(tmp_path: Path) -> None:
    """Ownership is per ``(variable, domain)``: revoking one pair must not
    stop the others, and the surviving accumulation series must stay correct
    across the ownership change (the ledger tracks a fixed variable set so the
    checkpoint lineage does not shift under it)."""
    _run_pass(tmp_path, FakeSource(max_fh=6))
    state = _state(tmp_path)
    state.revoke("precip_total", "global", reason="manual", rebuild_required=True)
    state.save()

    resumed = _run_pass(tmp_path, FakeSource(max_fh=12))

    assert ("precip_total", "global") not in resumed.active_pairs
    assert ("snowfall_total", "global") in resumed.active_pairs
    assert not resumed.stalled, resumed.stall_reasons
    # The surviving pairs keep advancing...
    assert _frame_exists(tmp_path, "tmp2m", 12)
    assert _frame_exists(tmp_path, "snowfall_total", 12)
    # ...with a correct run-cumulative total across the ownership change.
    from tests.test_fastpath_subloop import MM_TO_IN, STEP_SNOW_MM_SWE

    snow = _sidecar(tmp_path, "snowfall_total", 12)
    assert snow["max"] == pytest.approx(12 * STEP_SNOW_MM_SWE * MM_TO_IN * 10.0, rel=1e-4)
    # ...and the revoked one writes nothing new.
    assert not _frame_exists(tmp_path, "precip_total", 12)


def test_a_revocation_landing_mid_pass_stops_the_loop(tmp_path: Path) -> None:
    """Design §6: "a fast source that resumes after revocation finds its
    generation stale and stops" — including when the revocation lands *during*
    a pass, which is why the guard re-reads the state file per timestep."""

    class _RevokingSource(FakeSource):
        def __init__(self, data_root: Path, revoke_at_fh: int) -> None:
            super().__init__(max_fh=12)
            self._data_root = data_root
            self._revoke_at_fh = revoke_at_fh

        def fetch_frame_fields(self, catalog, url, var_names, window="global", **kwargs):
            if self._fh_from_url(url) == self._revoke_at_fh:
                state = _state(self._data_root)
                state.revoke("tmp2m", "global", reason="mid_pass")
                state.save()
            return super().fetch_frame_fields(catalog, url, var_names, window, **kwargs)

    result = _run_pass(tmp_path, _RevokingSource(tmp_path, revoke_at_fh=3))

    assert result.stalled
    assert "ownership_revoked" in result.stall_reasons
    assert ("tmp2m", "global") in result.revoked_pairs
    assert not _frame_exists(tmp_path, "tmp2m", 6)


# ---------------------------------------------------------------------------
# Accumulation series purity
# ---------------------------------------------------------------------------


def test_revoked_accumulation_frames_are_purged_for_a_whole_delayed_rebuild(
    tmp_path: Path,
) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    assert _frame_exists(tmp_path, "precip_total", 3)
    assert _frame_exists(tmp_path, "precip_total", 6)

    _failover(tmp_path)

    # The delayed catch-up loop advances past any fh whose staging artifacts
    # exist, so a mixed series is prevented by removing them: the delayed path
    # rebuilds the pair from its own GRIB values, start to finish.
    assert not _frame_exists(tmp_path, "precip_total", 3)
    assert not _frame_exists(tmp_path, "precip_total", 6)
    assert not _frame_exists(tmp_path, "snowfall_total", 3)


def test_revoked_instantaneous_frames_are_kept_as_a_recorded_seam(
    tmp_path: Path,
) -> None:
    """An instantaneous variable may legitimately carry a 9 km→0.25° seam
    (design §8 says so explicitly); it is the accumulation series that must
    never mix. The seam stays auditable through per-frame provenance."""
    _run_pass(tmp_path, FakeSource(max_fh=6))
    _failover(tmp_path)

    assert _frame_exists(tmp_path, "tmp2m", 3)
    sidecar = _sidecar(tmp_path, "tmp2m", 3)
    assert sidecar["source_id"] == "openmeteo"
    assert sidecar["generation"] == 1, "already-written frames keep their generation"


def test_provenance_is_uniform_across_a_surviving_series(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    _failover(tmp_path)

    signatures = set()
    for fh in (0, 3, 6):
        sidecar = _sidecar(tmp_path, "tmp2m", fh)
        assert sidecar is not None
        signatures.add(
            (sidecar["source_id"], sidecar["source_resolution"], sidecar["generation"])
        )
    assert len(signatures) == 1, f"a series must not silently mix sources: {signatures}"


def test_a_seam_is_visible_in_the_manifest_summary() -> None:
    """Purely a summariser check: two sources over one variable must collapse
    into two ranges, not one."""
    mixed = {
        0: {"source_id": "openmeteo", "source_resolution": "9 km native", "generation": 1},
        3: {"source_id": "openmeteo", "source_resolution": "9 km native", "generation": 1},
        6: {"source_id": "herbie", "source_resolution": "0.25deg", "generation": 2},
        9: {"source_id": "herbie", "source_resolution": "0.25deg", "generation": 2},
    }
    ranges = scheduler_module._summarize_source_ranges(mixed)
    assert [(item["from_fh"], item["to_fh"], item["source_id"]) for item in ranges] == [
        (0, 3, "openmeteo"),
        (6, 9, "herbie"),
    ]


def test_delayed_path_reclaims_revoked_pairs(tmp_path: Path) -> None:
    """The end of the failover story: the delayed builder's work list now
    contains exactly the revoked pairs it must rebuild."""
    _run_pass(tmp_path, FakeSource(max_fh=6))
    _failover(tmp_path)

    targets = scheduler_module._delayed_targets_for_cycle(
        _plugin(), ["precip_total"], RUN_DT.hour, data_root=tmp_path, run_id=RUN_ID
    )
    global_targets = [target for target in targets if target[0] == "global"]
    assert global_targets, "a revoked pair must be delayed-owned again"
    assert ("global", "precip_total", 3) in global_targets
    assert ("global", "precip_total", 360) in global_targets


# ---------------------------------------------------------------------------
# State file shape and crash replay
# ---------------------------------------------------------------------------


def test_state_file_is_json_with_the_documented_shape(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    _failover(tmp_path)

    payload = json.loads(_state(tmp_path).path.read_text())
    assert payload["schema_version"] == 1
    assert payload["model"] == "ecmwf"
    assert payload["run"] == RUN_ID
    assert payload["source_id"] == "openmeteo"
    assert payload["stall_count"] >= 1
    entry = payload["pairs"]["tmp2m|global"]
    assert set(entry) == {
        "owner",
        "generation",
        "ready_through_fh",
        "staged_fhs",
        "published_fhs",
        "revoked_at",
        "revoke_reason",
        "delayed_rebuild_required",
        "blocked",
    }
    assert entry["published_fhs"] == [0, 3, 6]
    assert entry["staged_fhs"] == [0, 3, 6], "everything staged also promoted"
    assert entry["blocked"] is False


def test_a_corrupt_state_file_does_not_wedge_the_scheduler(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.save()
    state.path.write_text("{not json")
    replayed = _state(tmp_path)
    assert replayed.pairs == {}
    assert not replayed.is_revoked("tmp2m", "global")


def test_state_survives_a_reload_round_trip(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.record_frame("tmp2m", "global", 3, expected_fhs=[0, 3, 6])
    state.record_frame("tmp2m", "global", 0, expected_fhs=[0, 3, 6])
    state.save()

    reloaded = _state(tmp_path)
    entry = reloaded.pair("tmp2m", "global")
    assert entry.published_fhs == [0, 3]
    # Contiguity edge over the EXPECTED order, not max(published).
    assert entry.ready_through_fh == 3


def test_ready_through_fh_is_contiguity_not_max(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.record_frame("tmp2m", "global", 0, expected_fhs=[0, 3, 6, 9])
    state.record_frame("tmp2m", "global", 9, expected_fhs=[0, 3, 6, 9])
    assert state.pair("tmp2m", "global").ready_through_fh == 0


# ---------------------------------------------------------------------------
# Polling (design §4)
# ---------------------------------------------------------------------------


def test_poll_interval_is_short_inside_the_dissemination_window() -> None:
    inside = RUN_DT + timedelta(hours=5, minutes=45)
    outside_early = RUN_DT + timedelta(hours=2)
    outside_late = RUN_DT + timedelta(hours=8)
    assert subloop_module.in_dissemination_window(RUN_DT, inside)
    assert not subloop_module.in_dissemination_window(RUN_DT, outside_early)
    assert not subloop_module.in_dissemination_window(RUN_DT, outside_late)
    assert subloop_module.poll_interval_seconds(RUN_DT, inside) == subloop_module.FAST_POLL_SECONDS
    assert (
        subloop_module.poll_interval_seconds(RUN_DT, outside_early)
        == subloop_module.SLOW_POLL_SECONDS
    )


def test_discovery_uses_the_hint_but_trusts_the_object_list() -> None:
    source = FakeSource(max_fh=2)
    found = subloop_module.discover_fast_run(
        ECMWF_IFS_CATALOG, source=source, now=RUN_DT + timedelta(hours=6)
    )
    assert found == RUN_DT


def test_discovery_returns_none_when_no_objects_exist() -> None:
    class _EmptySource(FakeSource):
        def list_run_objects(self, catalog, run_dt, **_kwargs):
            return []

    found = subloop_module.discover_fast_run(
        ECMWF_IFS_CATALOG, source=_EmptySource(), now=RUN_DT + timedelta(hours=6)
    )
    assert found is None


# ---------------------------------------------------------------------------
# Scheduler wiring
# ---------------------------------------------------------------------------


def test_scheduler_hook_is_a_no_op_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ownership.ENV_FASTPATH_MODELS, raising=False)
    called: list[str] = []
    monkeypatch.setattr(
        subloop_module, "run_fastpath_pass", lambda **_kw: called.append("pass")
    )
    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
    )
    assert called == []


def test_scheduler_hook_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fast-path failure must degrade to "the delayed path builds it", never
    take the scheduler process down."""

    def _boom(**_kwargs):
        raise RuntimeError("bucket on fire")

    monkeypatch.setattr(subloop_module, "run_fastpath_pass", _boom)
    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
    )


def _prune(tmp_path: Path, *, keep_runs: int = 4) -> None:
    scheduler_module._prune_fastpath_namespace(
        data_root=tmp_path, model_id="ecmwf", plugin=_plugin(), keep_runs=keep_runs
    )


def test_namespace_prune_keeps_state_for_a_live_run(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=3))
    namespace = _state(tmp_path).path.parent
    assert {path.suffix for path in namespace.iterdir()} == {".json", ".npz"}

    _prune(tmp_path)

    survivors = sorted(path.name for path in namespace.iterdir())
    assert any(name.endswith(".state.json") for name in survivors), survivors
    assert any(name.endswith(".accum.npz") for name in survivors), survivors


def test_namespace_prune_keeps_state_when_no_run_dirs_exist_at_all(
    tmp_path: Path,
) -> None:
    """The outage case that made the old presence-based rule wrong: state
    exists for a run that produced no frames, so no run directory exists."""
    state = _state(tmp_path)
    state.revoke("tmp2m", "global", reason="delayed_source_available")
    state.save()
    assert state.path.exists()

    _prune(tmp_path)

    assert state.path.exists(), "state for a frameless run must not be pruned"
    assert _state(tmp_path).is_revoked("tmp2m", "global")


def test_namespace_prune_drops_only_runs_past_the_retention_cutoff(
    tmp_path: Path,
) -> None:
    """Age-based, on retention's own terms: with keep_runs=2 and four runs on
    disk, only the two oldest namespace files go."""
    from app.services.fastpath.state import fastpath_state_path

    run_dts = [RUN_DT - timedelta(hours=12 * offset) for offset in range(4)]
    run_ids = [scheduler_module._run_id_from_dt(run_dt) for run_dt in run_dts]
    staging_model_root = tmp_path / "staging" / "ecmwf"
    for run_id in run_ids:
        (staging_model_root / run_id).mkdir(parents=True, exist_ok=True)
        state = FastpathRunState.load_or_create(
            data_root=tmp_path, model="ecmwf", run_id=run_id
        )
        state.record_frame("tmp2m", "na", 0, expected_fhs=[0])
        state.save()
        # A checkpoint under the WP2 ledger's own run-token spelling.
        token = f"{run_dt_token(run_id)}"
        (fastpath_state_path(tmp_path, "ecmwf", run_id).parent /
         f"openmeteo-ecmwf-{token}-all.accum.npz").write_bytes(b"stub")

    _prune(tmp_path, keep_runs=2)

    surviving = {path.name for path in _state(tmp_path).path.parent.iterdir()}
    for run_id in run_ids[:2]:
        assert f"{run_id}.state.json" in surviving, run_id
        assert any(run_dt_token(run_id) in name for name in surviving), run_id
    for run_id in run_ids[2:]:
        assert f"{run_id}.state.json" not in surviving, run_id
        assert not any(run_dt_token(run_id) in name for name in surviving), run_id


def run_dt_token(run_id: str) -> str:
    """``20260804_00z`` -> ``20260804T0000Z`` (WP2 checkpoint spelling)."""
    date_part, _, cycle_part = run_id.partition("_")
    return f"{date_part}T{int(cycle_part.rstrip('z')):02d}00Z"


def test_namespace_prune_is_a_no_op_below_the_keep_count(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=3))
    namespace = _state(tmp_path).path.parent
    before = {path.name for path in namespace.iterdir()}

    _prune(tmp_path, keep_runs=6)

    assert {path.name for path in namespace.iterdir()} == before


# ---------------------------------------------------------------------------
# Total-outage failover through the real scheduler hook
# ---------------------------------------------------------------------------


def test_total_outage_failover_survives_the_whole_scheduler_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the prune defect, exercised through
    ``_maybe_run_fastpath_pass`` rather than the sub-loop functions directly.

    Scenario: the bucket is unreachable, so the fast path writes nothing and no
    run directory exists for the run. The delayed probe resolves the run, the
    deadline revokes every pair — and the state recording that must still be
    there when the delayed target selection runs *later in the same poll*.
    """

    monkeypatch.setattr(subloop_module, "_default_source", lambda: _DeadSource())
    # The delayed source demonstrably HAS this run (a verified probe hit), which
    # is what licenses the deadline to fire at all...
    monkeypatch.setattr(scheduler_module, "_probe_run_exists", lambda **_kw: True)
    # ...and the outage has been running well past the grace window, so this is
    # a stall rather than a run that merely trails.
    _state(tmp_path).save()
    _age_state(tmp_path, seconds=3600.0)

    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
        primary_vars=["tmp2m"],
        keep_runs=4,
        probe_var="tmp2m",
    )

    # 1. The state file survived the pass's own prune.
    state = _state(tmp_path)
    assert state.path.exists(), "the deadline's state must outlive the same pass"
    revoked = state.revoked_pairs()
    assert revoked == ownership.fast_owned_pairs("ecmwf"), revoked
    assert all(state.pair(*pair).generation == 2 for pair in revoked)
    assert state.pair("precip_total", "global").delayed_rebuild_required is True
    assert state.stall_count >= 1

    # 2. The scheduler's own reader agrees.
    assert scheduler_module._fastpath_revoked_pairs(_plugin(), tmp_path, RUN_ID) == revoked

    # 3. The delayed selection picks the pairs up in this same poll — nobody
    #    building them for a cycle was the user-visible symptom.
    targets = scheduler_module._delayed_targets_for_cycle(
        _plugin(),
        ["tmp2m", "precip_total"],
        RUN_DT.hour,
        data_root=tmp_path,
        run_id=RUN_ID,
    )
    assert ("global", "tmp2m", 0) in targets
    assert ("global", "precip_total", 3) in targets
    assert ("na", "tmp2m", 0) in targets


# ---------------------------------------------------------------------------
# Promotion failures must not be recorded as progress
# ---------------------------------------------------------------------------


def _fail_promote_at(monkeypatch: pytest.MonkeyPatch, fhs: set[int]):
    """Make ``_publish_timestep`` raise for the named timestep fhs.

    Returns a callable that restores ONLY the promote patch — ``monkeypatch.undo()``
    would also revert the autouse env fixture and silently disable the fast path.
    """
    original = subloop_module._publish_timestep

    def _maybe_fail(*, reason: str, **kwargs):
        fh = int(reason.rsplit("fh", 1)[-1])
        if fh in fhs:
            raise OSError(f"simulated promote failure at fh{fh:03d}")
        return original(reason=reason, **kwargs)

    monkeypatch.setattr(subloop_module, "_publish_timestep", _maybe_fail)

    def _restore() -> None:
        monkeypatch.setattr(subloop_module, "_publish_timestep", original)

    return _restore


def test_a_failed_promote_is_not_recorded_as_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frames staged, the promote raised — the timestep must look pending,
    not complete, or the next pass sees nothing to do and the frames never
    reach the published tree."""
    _fail_promote_at(monkeypatch, {3})
    result = _run_pass(tmp_path, FakeSource(max_fh=3))

    entry = _state(tmp_path).pair("tmp2m", "global")
    assert 3 in entry.staged_fhs
    assert 3 not in entry.published_fhs
    assert entry.ready_through_fh == 0, "the frontier must not advance past a failed promote"
    assert result.promotion_retries_pending == 1
    assert result.timesteps_promoted == 1, "only FH000 actually promoted"
    assert _state(tmp_path).pending_promotions() == {3: {"global"}}


def test_the_next_pass_retries_a_failed_promote_before_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    restore = _fail_promote_at(monkeypatch, {3})
    _run_pass(tmp_path, FakeSource(max_fh=3))
    restore()

    second = _run_pass(tmp_path, FakeSource(max_fh=6))

    assert second.promotion_retries_succeeded == 1
    assert not second.stalled, second.stall_reasons
    entry = _state(tmp_path).pair("tmp2m", "global")
    assert 3 in entry.published_fhs
    assert entry.pending_promotion_fhs == []
    assert entry.ready_through_fh == 6
    published = (
        scheduler_module._published_model_root(tmp_path, "ecmwf", "global") / RUN_ID
    )
    assert (published / "tmp2m" / "fh003.json").is_file()


def test_a_failed_promote_on_the_FINAL_timestep_is_still_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last timestep has no successor to carry it, so without an explicit
    retry queue its frames would stay staged forever."""
    restore = _fail_promote_at(monkeypatch, {6})
    _run_pass(tmp_path, FakeSource(max_fh=6))
    assert _state(tmp_path).pending_promotions() == {6: {"global"}}
    restore()

    # A later pass finds no NEW objects at all — the retry must still happen.
    second = _run_pass(tmp_path, FakeSource(max_fh=6))

    assert second.promotion_retries_succeeded == 1
    entry = _state(tmp_path).pair("tmp2m", "global")
    assert entry.published_fhs == [0, 3, 6]
    assert entry.pending_promotion_fhs == []
    published = (
        scheduler_module._published_model_root(tmp_path, "ecmwf", "global") / RUN_ID
    )
    assert (published / "precip_total" / "fh006.json").is_file()


def test_swallowed_domain_publish_failure_must_not_mark_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: ``_publish_run_snapshot_for(strict_canonical=False)`` used to
    catch domain publish exceptions and return normally. The sub-loop then called
    ``record_published``, clearing ``pending_promotions`` with frames still only
    in staging — permanent gaps, no retry, and failover skipped the "complete"
    pair. Failures inside the shared publish path must propagate."""
    original = scheduler_module._publish_domain

    def _fail_global(*, domain: str, reason: str, **kwargs):
        if domain == "global" and reason.endswith("fh003"):
            raise OSError("simulated ENOSPC during domain publish")
        return original(domain=domain, reason=reason, **kwargs)

    monkeypatch.setattr(scheduler_module, "_publish_domain", _fail_global)

    result = _run_pass(tmp_path, FakeSource(max_fh=3))

    entry = _state(tmp_path).pair("tmp2m", "global")
    assert 3 in entry.staged_fhs
    assert 3 not in entry.published_fhs
    assert entry.ready_through_fh == 0
    assert result.promotion_retries_pending == 1
    assert _state(tmp_path).pending_promotions() == {3: {"global"}}
    published = (
        scheduler_module._published_model_root(tmp_path, "ecmwf", "global") / RUN_ID
    )
    assert not (published / "tmp2m" / "fh003.json").exists()


def test_a_persistent_promote_failure_stops_the_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry that fails again must stop rather than pile more staged frames
    on top of an unpublished timestep."""
    _fail_promote_at(monkeypatch, {3})
    _run_pass(tmp_path, FakeSource(max_fh=3))

    second = _run_pass(tmp_path, FakeSource(max_fh=12))

    assert second.stalled
    assert any("promotion_retry_failed" in reason for reason in second.stall_reasons)
    assert second.steps_ingested == 0, "no new ingest while a promote is outstanding"


# ---------------------------------------------------------------------------
# Purge must be all-or-nothing (design §6: never mix sources in one series)
# ---------------------------------------------------------------------------


def test_a_successful_purge_quarantines_frames_and_hands_over_cleanly(
    tmp_path: Path,
) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    _failover(tmp_path)

    entry = _state(tmp_path).pair("precip_total", "global")
    assert entry.revoked and not entry.blocked
    assert entry.delayed_rebuild_required is True
    assert not _frame_exists(tmp_path, "precip_total", 3)
    # Frames are quarantined, not destroyed — evidence for whoever investigates.
    quarantine = _state(tmp_path).path.parent / "quarantine"
    assert quarantine.is_dir()
    assert any("precip_total" in child.name for child in quarantine.iterdir())
    # And the delayed path may build the pair.
    assert ("precip_total", "global") in _state(tmp_path).reclaimable_pairs()


def test_a_failed_purge_blocks_the_pair_instead_of_handing_over_a_dirty_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Partial deletion would let the delayed rebuild skip the surviving fhs
    and silently produce a mixed-source accumulation series. Better: nobody
    builds it, loudly."""
    _run_pass(tmp_path, FakeSource(max_fh=6))
    monkeypatch.setattr(
        subloop_module, "_purge_fast_frames", lambda **_kwargs: False
    )

    with caplog.at_level("ERROR", logger="app.services.fastpath.subloop"):
        _failover(tmp_path)

    state = _state(tmp_path)
    entry = state.pair("precip_total", "global")
    assert entry.revoked, "the fast path still stops"
    assert entry.blocked, "but the delayed path must not pick it up"
    assert entry.revoke_reason == "purge_failed"
    assert ("precip_total", "global") in state.blocked_pairs()
    assert ("precip_total", "global") not in state.reclaimable_pairs()
    assert any(
        "fastpath_blocked" in record.getMessage()
        for record in caplog.records
        if record.levelname == "ERROR"
    ), "a blocked pair must be loud"

    # The scheduler's delayed selection leaves the blocked pair alone...
    targets = scheduler_module._delayed_targets_for_cycle(
        _plugin(),
        ["precip_total", "tmp2m"],
        RUN_DT.hour,
        data_root=tmp_path,
        run_id=RUN_ID,
    )
    assert not [
        target
        for target in targets
        if target[0] == "global" and target[1] == "precip_total"
    ]
    # ...while a non-blocked revoked pair in the same run is handed over.
    assert ("global", "tmp2m", 0) in targets


def test_purge_verification_catches_a_partial_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly exercise the verification: if the move/delete leaves frame
    artifacts behind, ``_purge_fast_frames`` must report failure."""
    _run_pass(tmp_path, FakeSource(max_fh=6))

    def _rename_but_leave_a_frame(src, dst):
        # Simulate a partial move: the directory survives with one frame in it.
        raise OSError("cross-device link")

    monkeypatch.setattr(subloop_module.os, "rename", _rename_but_leave_a_frame)
    monkeypatch.setattr(
        subloop_module.shutil if hasattr(subloop_module, "shutil") else subloop_module,
        "rmtree",
        lambda *a, **k: None,
        raising=False,
    )
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "rmtree", lambda *a, **k: None)

    ok = subloop_module._purge_fast_frames(
        data_root=tmp_path,
        model_id="ecmwf",
        run_id=RUN_ID,
        var_id="precip_total",
        domain="global",
    )
    assert ok is False, "surviving frame artifacts must fail verification"


# ---------------------------------------------------------------------------
# Trailing is not stalled (WP5 live-run regression)
# ---------------------------------------------------------------------------


def test_cold_start_does_not_revoke_a_run_the_fast_source_can_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The WP5 12:47Z regression, end to end through the scheduler hook.

    A restart mid-cycle resolves back to an older run that the delayed source
    genuinely has (probe HIT). The bucket holds every object for it. Nothing
    may be revoked: the fast path must get first move and build the run in
    seconds, instead of the deadline handing 12 pairs to a full Herbie build
    because a brand-new state trivially "trails".
    """
    monkeypatch.setattr(scheduler_module, "_probe_run_exists", lambda **_kw: True)
    monkeypatch.setattr(subloop_module, "_default_source", lambda: FakeSource(max_fh=6))

    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
        primary_vars=["tmp2m"],
        keep_runs=4,
        probe_var="tmp2m",
    )

    state = _state(tmp_path)
    assert state.revoked_pairs() == frozenset(), "cold start must not revoke"
    # The fast pass built the run in the SAME poll — that is the whole point.
    assert _frame_exists(tmp_path, "tmp2m", 0)
    assert _frame_exists(tmp_path, "precip_total", 6)
    assert state.pair("tmp2m", "global").ready_through_fh == 6
    # And the delayed loop still skips the pairs, because they are still fast-owned.
    targets = scheduler_module._delayed_targets_for_cycle(
        _plugin(), ["tmp2m"], RUN_DT.hour, data_root=tmp_path, run_id=RUN_ID
    )
    assert not [target for target in targets if target[0] == "global"]


def test_a_trailing_run_with_recent_progress_is_never_revoked(
    tmp_path: Path,
) -> None:
    """Mid-catchup: the fast source is steadily working an old run and trails
    the delayed horizon the entire time. Trailing is the normal state of a
    run being built; it must never on its own trigger failover."""
    for max_fh in (3, 6, 9, 12):
        _run_pass(tmp_path, FakeSource(max_fh=max_fh))
        entry = _state(tmp_path).pair("tmp2m", "global")
        expected_last = _plugin().scheduled_fhs_for_var("tmp2m", RUN_DT.hour)[-1]
        assert entry.ready_through_fh is not None
        assert entry.ready_through_fh < expected_last, "trailing throughout"
        assert _failover(tmp_path, stalled=False) == frozenset()

    assert _state(tmp_path).revoked_pairs() == frozenset()
    assert _frame_exists(tmp_path, "tmp2m", 12)


def test_the_deadline_fires_once_the_grace_window_elapses(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6, fail_after_fh=6))
    grace = subloop_module.failover_grace_seconds()

    # Just inside the window: still considered alive.
    just_inside = datetime.now(timezone.utc) + timedelta(seconds=grace - 60)
    assert _failover(tmp_path, now=just_inside, stalled=False) == frozenset()

    # Past it: a real stall.
    past = datetime.now(timezone.utc) + timedelta(seconds=grace + 60)
    revoked = _failover(tmp_path, now=past, stalled=False)
    assert revoked == ownership.fast_owned_pairs("ecmwf")


def test_progress_resets_the_stall_clock(tmp_path: Path) -> None:
    """A bucket that goes quiet and then resumes must not be revoked for the
    earlier gap."""
    _run_pass(tmp_path, FakeSource(max_fh=3))
    _age_state(tmp_path, seconds=3600.0)
    assert _state(tmp_path).seconds_since_progress() > 3000

    _run_pass(tmp_path, FakeSource(max_fh=6))

    assert _state(tmp_path).seconds_since_progress() < 60
    assert _failover(tmp_path, stalled=False) == frozenset()


def test_the_fast_pass_runs_before_the_deadline_within_one_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering inside ``_maybe_run_fastpath_pass`` is load-bearing, not
    cosmetic.

    The setup is a bucket that goes quiet just past the grace window and then
    resumes on this very poll. Fast-first: the pass makes progress, the
    deadline judges *including* it, nothing is revoked. Deadline-first: the
    pairs are revoked a moment before the evidence that the source is alive
    arrives — the run gets handed to Herbie for nothing.
    """
    _run_pass(tmp_path, FakeSource(max_fh=3))
    _age_state(tmp_path, seconds=subloop_module.failover_grace_seconds() + 120)
    assert _state(tmp_path).is_stalled(
        grace_seconds=subloop_module.failover_grace_seconds()
    ), "precondition: the run looks stalled going into this poll"

    # The bucket is back, with new objects beyond what we already have.
    monkeypatch.setattr(scheduler_module, "_probe_run_exists", lambda **_kw: True)
    monkeypatch.setattr(subloop_module, "_default_source", lambda: FakeSource(max_fh=9))

    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
        primary_vars=["tmp2m"],
        keep_runs=4,
        probe_var="tmp2m",
    )

    state = _state(tmp_path)
    assert state.revoked_pairs() == frozenset(), (
        "the fast source proved it is alive during this poll — the deadline "
        "must see that progress, which only happens if the pass runs first"
    )
    assert _frame_exists(tmp_path, "tmp2m", 9)
    assert state.pair("tmp2m", "global").ready_through_fh == 9


# ---------------------------------------------------------------------------
# Completed is not stalled (WP5 live observation)
# ---------------------------------------------------------------------------


def _complete_the_run(data_root: Path, *, mark: bool = True) -> None:
    """Mark every active pair as having reached its cycle horizon."""
    state = _state(data_root)
    for var_id, domain in sorted(ownership.fast_owned_pairs("ecmwf")):
        expected = _plugin().scheduled_fhs_for_var(var_id, RUN_DT.hour)
        for fh in expected:
            state.record_published(var_id, domain, fh, expected_fhs=expected)
    if mark:
        state.mark_completed()
    state.save()


def _patch_state_json(data_root: Path, **fields) -> None:
    """Rewrite state fields straight on disk.

    ``save()`` deliberately merges completion forward (it is monotonic), so
    clearing the flag has to bypass it — otherwise a test that means to
    exercise the pair-level path silently exercises the flag shortcut instead.
    """
    path = _state(data_root).path
    payload = json.loads(path.read_text())
    payload.update(fields)
    path.write_text(json.dumps(payload))


def test_a_completed_run_is_never_judged_against_the_stall_clock(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """WP5: once 06z finished, progress stopped *because the run was done*, and
    the deadline warned "no fast progress for 2235s" on every single poll —
    revoking nothing, but inflating stall_count into a false alarm at the end
    of every cycle. A finished run has nothing left to progress on."""
    _complete_the_run(tmp_path)
    # Leave ONE pair looking short of its horizon, so this test exercises the
    # completion flag itself rather than incidentally hitting the
    # "nothing trails" path. The flag is the authoritative done signal and must
    # short-circuit even when per-pair bookkeeping disagrees.
    state = _state(tmp_path)
    state.pair("tmp2m", "global").ready_through_fh = 6
    state.save()
    assert _state(tmp_path).completed is True
    before = _state(tmp_path).stall_count

    with caplog.at_level("INFO", logger="app.services.fastpath.subloop"):
        # Idle far beyond the grace window, delayed available — the exact
        # conditions that used to warn every poll.
        revoked = _failover(tmp_path, now=datetime.now(timezone.utc) + timedelta(hours=5))

    assert revoked == frozenset()
    state = _state(tmp_path)
    assert state.stall_count == before, "a finished run must not accrue stalls"
    assert state.revoked_pairs() == frozenset()
    assert not [
        record for record in caplog.records if record.levelname in ("WARNING", "ERROR")
    ], "a completed run must be silent"


def test_completion_is_recorded_by_the_pass(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6))
    assert _state(tmp_path).completed is False, "still mid-run"

    _complete_the_run(tmp_path)
    assert _state(tmp_path).completed is True
    assert json.loads(_state(tmp_path).path.read_text())["completed"] is True


def test_a_short_cycle_run_is_complete_at_its_own_horizon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """06z/18z run to FH144, not FH360. A short cycle judged against the long
    horizon would look permanently incomplete — and so permanently stalled."""
    short_run_dt = RUN_DT.replace(hour=6)
    short_run_id = scheduler_module._run_id_from_dt(short_run_dt)
    plugin = _plugin()

    assert plugin.scheduled_fhs_for_var("tmp2m", 6)[-1] == 144
    assert plugin.scheduled_fhs_for_var("tmp2m", 0)[-1] == 360

    state = FastpathRunState.load_or_create(
        data_root=tmp_path, model="ecmwf", run_id=short_run_id
    )
    for var_id, domain in sorted(ownership.fast_owned_pairs("ecmwf")):
        expected = plugin.scheduled_fhs_for_var(var_id, 6)
        for fh in expected:
            state.record_published(var_id, domain, fh, expected_fhs=expected)
    state.save()
    assert state.pair("tmp2m", "global").ready_through_fh == 144

    revoked = subloop_module.enforce_failover_deadline(
        plugin=plugin,
        model_id="ecmwf",
        run_dt=short_run_dt,
        data_root=tmp_path,
        delayed_available=True,
        now=datetime.now(timezone.utc) + timedelta(hours=5),
    )
    assert revoked == frozenset(), "FH144 IS the 06z horizon — nothing trails"


def test_an_idle_run_with_nothing_trailing_stays_below_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Even without the completed flag, a deadline that would revoke nothing
    must not announce itself. Warning loudly and then doing nothing is the
    noisiest possible way to say 'fine'."""
    _complete_the_run(tmp_path, mark=False)
    _patch_state_json(tmp_path, completed=False)
    assert _state(tmp_path).completed is False, "the flag shortcut must be OFF here"

    with caplog.at_level("INFO", logger="app.services.fastpath.subloop"):
        revoked = _failover(tmp_path, now=datetime.now(timezone.utc) + timedelta(hours=5))

    assert revoked == frozenset()
    assert _state(tmp_path).stall_count == 0
    assert not [
        record for record in caplog.records if record.levelname in ("WARNING", "ERROR")
    ]


def test_stall_count_only_increments_on_a_genuine_stall(tmp_path: Path) -> None:
    _run_pass(tmp_path, FakeSource(max_fh=6, fail_after_fh=6))
    baseline = _state(tmp_path).stall_count

    # Idle but inside the grace window: no stall recorded.
    _failover(tmp_path, stalled=False)
    assert _state(tmp_path).stall_count == baseline

    # Past the window with pairs genuinely trailing: one stall recorded.
    assert _failover(tmp_path) == ownership.fast_owned_pairs("ecmwf")
    assert _state(tmp_path).stall_count == baseline + 1


def test_the_grace_window_is_env_tunable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(subloop_module.ENV_FAILOVER_GRACE_SECONDS, raising=False)
    assert subloop_module.failover_grace_seconds() == (
        subloop_module.DEFAULT_FAILOVER_GRACE_SECONDS
    )
    monkeypatch.setenv(subloop_module.ENV_FAILOVER_GRACE_SECONDS, "300")
    assert subloop_module.failover_grace_seconds() == 300.0
    # A malformed value must not take the scheduler down.
    monkeypatch.setenv(subloop_module.ENV_FAILOVER_GRACE_SECONDS, "soon")
    assert subloop_module.failover_grace_seconds() == (
        subloop_module.DEFAULT_FAILOVER_GRACE_SECONDS
    )


def test_the_deadline_persists_a_new_run_state_so_the_clock_can_start(
    tmp_path: Path,
) -> None:
    """Without persisting ``created_at`` on first observation, every poll would
    mint a fresh clock and the deadline could never fire at all."""
    state_path = _state(tmp_path).path
    assert not state_path.exists()

    assert _failover(tmp_path, stalled=False) == frozenset()

    assert state_path.exists(), "the grace clock must be anchored on disk"
    payload = json.loads(state_path.read_text())
    assert payload["created_at"]
    assert payload["last_progress_at"]


def test_state_without_last_progress_at_migrates_without_instant_revoke(
    tmp_path: Path,
) -> None:
    """Upgrade path: a file written before the stall clock existed must load,
    and must not be treated as instantly stalled."""
    _run_pass(tmp_path, FakeSource(max_fh=3))
    state_path = _state(tmp_path).path
    payload = json.loads(state_path.read_text())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["updated_at"] = stamp
    payload.pop("last_progress_at", None)
    state_path.write_text(json.dumps(payload))

    migrated = _state(tmp_path)
    assert migrated.last_progress_at == stamp
    assert migrated.seconds_since_progress() < 60
    assert _failover(tmp_path, stalled=False) == frozenset()


# ---------------------------------------------------------------------------
# The deadline requires VERIFIED delayed availability
# ---------------------------------------------------------------------------


class _DeadSource:
    def list_run_objects(self, catalog, run_dt, **_kwargs):
        raise RuntimeError("bucket unreachable")

    def read_in_progress(self, catalog, **_kwargs):
        raise RuntimeError("bucket unreachable")


def _stalled_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    probe_hit: bool,
    probe_var: str | None = "tmp2m",
    stalled_for_seconds: float | None = 3600.0,
) -> None:
    """Drive the real scheduler hook with a dead bucket.

    ``stalled_for_seconds`` backdates the run's progress clock so the outage
    looks like it has been going on that long — the deadline needs a stall, not
    merely a trailing run.
    """
    monkeypatch.setattr(subloop_module, "_default_source", lambda: _DeadSource())
    monkeypatch.setattr(
        scheduler_module, "_probe_run_exists", lambda **_kw: bool(probe_hit)
    )
    if stalled_for_seconds is not None:
        _state(tmp_path).save()
        _age_state(tmp_path, seconds=stalled_for_seconds)
    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
        primary_vars=["tmp2m"],
        keep_runs=4,
        probe_var=probe_var,
    )


def test_fallback_resolved_run_does_not_trigger_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_resolve_latest_run_dt`` returns a datetime whether it probed
    successfully or fell back to ``fallback_lag_hours``. Revoking on the guess
    would black the pairs out for the whole Herbie outage: fast stops by
    generation, and delayed cannot fetch either."""
    _stalled_hook(tmp_path, monkeypatch, probe_hit=False)

    assert _state(tmp_path).revoked_pairs() == frozenset()
    targets = scheduler_module._delayed_targets_for_cycle(
        _plugin(), ["tmp2m"], RUN_DT.hour, data_root=tmp_path, run_id=RUN_ID
    )
    assert not [
        target for target in targets if target[1] == "tmp2m" and target[0] == "global"
    ], "the pair stays fast-owned so the fast path can keep retrying"


def test_probe_verified_run_does_trigger_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stalled_hook(tmp_path, monkeypatch, probe_hit=True)
    assert _state(tmp_path).revoked_pairs() == ownership.fast_owned_pairs("ecmwf")


def test_no_probe_var_means_no_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails closed: without a way to verify, the deadline never fires."""
    _stalled_hook(tmp_path, monkeypatch, probe_hit=True, probe_var=None)
    assert _state(tmp_path).revoked_pairs() == frozenset()


def test_a_raising_probe_means_no_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("herbie exploded")

    monkeypatch.setattr(subloop_module, "_default_source", lambda: _DeadSource())
    monkeypatch.setattr(scheduler_module, "_probe_run_exists", _boom)
    scheduler_module._maybe_run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        delayed_run_dt=RUN_DT,
        primary_vars=["tmp2m"],
        keep_runs=4,
        probe_var="tmp2m",
    )
    assert _state(tmp_path).revoked_pairs() == frozenset()
