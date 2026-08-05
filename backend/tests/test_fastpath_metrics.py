"""Fast-path ops metrics: JSON snapshot → Prometheus gauges (design §8, WP4).

The scheduler has no HTTP server, so its metrics reach Prometheus through the
same JSON handoff its Herbie runtime metrics already use: it writes
``{data_root}/status/fastpath/{model}.json`` and the API republishes it on the
next ``/metrics`` scrape. Both halves are covered here.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import prometheus_metrics
from app.services.fastpath import metrics as metrics_module
from app.services.fastpath import ownership
from app.services.fastpath.state import FastpathRunState
from app.services.fastpath.subloop import FastpathPassResult

RUN_A = "20260804_00z"
RUN_B = "20260804_12z"


@pytest.fixture(autouse=True)
def _fastpath_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")


def _state(data_root: Path, run_id: str) -> FastpathRunState:
    return FastpathRunState.load_or_create(
        data_root=data_root, model="ecmwf", run_id=run_id
    )


def _pass_result(run_id: str = RUN_A, **kwargs) -> FastpathPassResult:
    return FastpathPassResult(model_id="ecmwf", run_id=run_id, **kwargs)


def _snapshot(data_root: Path) -> dict:
    path = metrics_module.fastpath_snapshot_path(data_root, "ecmwf")
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_nothing_is_written_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ownership.ENV_FASTPATH_MODELS, raising=False)
    assert (
        metrics_module.write_pass_snapshot(
            data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result()
        )
        is None
    )
    assert not (tmp_path / "status").exists()


def test_a_snapshot_carries_the_current_runs_frontier(tmp_path: Path) -> None:
    state = _state(tmp_path, RUN_A)
    state.record_published("tmp2m", "global", 3, expected_fhs=[3, 6, 9])
    state.record_published("tmp2m", "global", 6, expected_fhs=[3, 6, 9])
    state.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result()
    )
    payload = _snapshot(tmp_path)
    assert payload["schema_version"] == metrics_module.FASTPATH_SNAPSHOT_SCHEMA_VERSION
    assert payload["model_id"] == "ecmwf"
    assert payload["run"] == RUN_A
    assert payload["ready_through_fh"] == [
        {"var": "tmp2m", "domain": "global", "ready_through_fh": 6}
    ]


def test_blocked_pairs_from_an_older_run_still_surface(tmp_path: Path) -> None:
    """A blocked pair is the page-worthy condition and it lives on an OLD run.

    By the time anyone looks, the run that failed over is a cycle or two behind
    whatever the bucket is publishing — so a snapshot scoped to the current run
    would drop exactly the signal an operator needs.
    """
    old = _state(tmp_path, RUN_A)
    old.revoke("precip_total", "global", reason="delayed_source_available")
    old.pair("precip_total", "global").blocked = True
    old.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result(run_id=RUN_B)
    )
    payload = _snapshot(tmp_path)
    assert payload["blocked_pairs"] == [["precip_total", "global"]]
    assert payload["blocked_pair_count"] == 1
    assert payload["run"] == RUN_B


def test_the_same_blocked_pair_across_runs_counts_once(tmp_path: Path) -> None:
    """One pair blocked over several cycles is ONE outage.

    Regression: counting occurrences made ``blocked_pair_count`` scale with
    retention depth, so any alert threshold would drift as runs accumulated.
    """
    for run_id in (RUN_A, RUN_B):
        state = _state(tmp_path, run_id)
        state.revoke("precip_total", "global", reason="delayed_source_available")
        state.pair("precip_total", "global").blocked = True
        state.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result(run_id=RUN_B)
    )
    payload = _snapshot(tmp_path)
    assert payload["blocked_pair_count"] == 1
    assert payload["blocked_pairs"] == [["precip_total", "global"]]
    # Per-run detail is still available for whoever investigates.
    assert set(payload["blocked_pairs_by_run"]) == {RUN_A, RUN_B}


def test_distinct_blocked_pairs_still_count_separately(tmp_path: Path) -> None:
    state = _state(tmp_path, RUN_A)
    for var_id in ("precip_total", "snowfall_total"):
        state.pair(var_id, "global").blocked = True
    state.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result()
    )
    assert _snapshot(tmp_path)["blocked_pair_count"] == 2


def test_stall_counts_are_reported_per_run(tmp_path: Path) -> None:
    first = _state(tmp_path, RUN_A)
    first.note_stall(3)
    first.save()
    second = _state(tmp_path, RUN_B)
    second.note_stall()
    second.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result(run_id=RUN_B)
    )
    assert _snapshot(tmp_path)["stall_count_by_run"] == {RUN_A: 3, RUN_B: 1}


def test_pending_promotion_retries_are_reported(tmp_path: Path) -> None:
    metrics_module.write_pass_snapshot(
        data_root=tmp_path,
        model_id="ecmwf",
        pass_result=_pass_result(promotion_retries_pending=2),
    )
    assert _snapshot(tmp_path)["promotion_retries_pending"] == 2


def test_canary_results_persist_between_cycles(tmp_path: Path) -> None:
    """Gauges must hold the last verdict, not drop out between canary runs."""
    state = _state(tmp_path, RUN_A)
    state.mark_canary(
        {
            "status": "ok",
            "completed_at": "2026-08-04T06:00:00Z",
            "results": [
                {
                    "var": "tmp2m",
                    "domain": "global",
                    "bias": 0.01,
                    "mae": 0.2,
                    "synoptic_mae": 0.05,
                    "corr": 0.999,
                    "failed": False,
                }
            ],
        }
    )
    state.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result(run_id=RUN_B)
    )
    payload = _snapshot(tmp_path)
    assert len(payload["canary_results"]) == 1
    assert payload["canary_results"][0]["var"] == "tmp2m"
    assert payload["canary_results"][0]["run"] == RUN_A


def test_a_recorded_seam_failure_reaches_the_snapshot(tmp_path: Path) -> None:
    """Regression: seams were audited, logged — and then dropped on the floor.

    A failed continuity check means an accumulation series was spliced from two
    sources. That has to be gaugeable, not log-only.
    """
    state = _state(tmp_path, RUN_A)
    state.mark_canary(
        {
            "status": "failed",
            "results": [],
            "seams": [
                {
                    "var": "precip_total",
                    "domain": "global",
                    "seam_fh": 42,
                    "continuity_checked": True,
                    "ok": False,
                    "negative_jump_fraction": 0.06,
                }
            ],
        }
    )
    state.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result()
    )
    seams = _snapshot(tmp_path)["canary_seams"]
    assert len(seams) == 1
    assert seams[0]["var"] == "precip_total"
    assert seams[0]["ok"] is False
    assert seams[0]["run"] == RUN_A


def test_a_fresh_canary_seam_wins_over_a_recorded_one(tmp_path: Path) -> None:
    from app.services.fastpath.canary import CanaryRun, SeamCheck

    state = _state(tmp_path, RUN_A)
    state.mark_canary(
        {
            "status": "failed",
            "seams": [
                {
                    "var": "precip_total",
                    "domain": "global",
                    "continuity_checked": True,
                    "ok": False,
                }
            ],
        }
    )
    state.save()

    fresh = CanaryRun(
        model_id="ecmwf",
        run_id=RUN_B,
        status="ok",
        completed_at="2026-08-05T00:00:00Z",
        seams=(
            SeamCheck(
                var_id="precip_total",
                domain="global",
                seam_fh=42,
                from_source="openmeteo",
                to_source="delayed",
                continuity_checked=True,
                ok=True,
            ),
        ),
    )
    metrics_module.write_pass_snapshot(
        data_root=tmp_path,
        model_id="ecmwf",
        pass_result=_pass_result(run_id=RUN_B),
        canary=fresh,
    )
    seams = _snapshot(tmp_path)["canary_seams"]
    assert len(seams) == 1
    assert seams[0]["ok"] is True
    assert seams[0]["run"] == RUN_B


def test_a_fresh_canary_wins_over_a_recorded_one(tmp_path: Path) -> None:
    from app.services.fastpath.canary import CanaryResult, CanaryRun

    state = _state(tmp_path, RUN_A)
    state.mark_canary(
        {
            "status": "ok",
            "results": [
                {"var": "tmp2m", "domain": "global", "bias": 9.0, "failed": True}
            ],
        }
    )
    state.save()

    fresh = CanaryRun(
        model_id="ecmwf",
        run_id=RUN_B,
        status="ok",
        completed_at="2026-08-04T18:00:00Z",
        results=(
            CanaryResult(
                var_id="tmp2m",
                domain="global",
                fh=96,
                n=10,
                bias=0.02,
                mae=0.1,
                synoptic_mae=0.04,
                corr=0.998,
            ),
        ),
    )
    metrics_module.write_pass_snapshot(
        data_root=tmp_path,
        model_id="ecmwf",
        pass_result=_pass_result(run_id=RUN_B),
        canary=fresh,
    )
    payload = _snapshot(tmp_path)
    assert payload["canary_results"] == [
        {
            "var": "tmp2m",
            "domain": "global",
            "fh": 96,
            "n": 10,
            "bias": 0.02,
            "mae": 0.1,
            "synoptic_mae": 0.04,
            "corr": 0.998,
            "unit": "",
            "reference_source": "",
            "failed": False,
            "failures": [],
            "notes": [],
            "run": RUN_B,
        }
    ]


def test_a_write_failure_never_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(metrics_module, "_write_atomic", _boom)
    assert (
        metrics_module.write_pass_snapshot(
            data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result()
        )
        is None
    )


def test_an_invalid_model_id_cannot_escape_the_status_directory() -> None:
    with pytest.raises(ValueError):
        metrics_module.fastpath_snapshot_path(Path("/tmp"), "../../etc")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _write_raw(tmp_path: Path, name: str, payload: dict) -> Path:
    directory = metrics_module.fastpath_snapshot_dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


def _valid_payload(**overrides) -> dict:
    payload = {
        "schema_version": metrics_module.FASTPATH_SNAPSHOT_SCHEMA_VERSION,
        "model_id": "ecmwf",
        "recorded_at": time.time(),
        "run": RUN_A,
        "ready_through_fh": [],
        "stall_count_by_run": {},
        "blocked_pairs": [],
        "blocked_pair_count": 0,
        "blocked_pairs_by_run": {},
        "promotion_retries_pending": 0,
        "canary_results": [],
        "canary_seams": [],
    }
    payload.update(overrides)
    return payload


def test_loading_an_empty_data_root_yields_nothing(tmp_path: Path) -> None:
    assert metrics_module.load_fastpath_snapshots(data_root=tmp_path) == []


def test_a_fresh_snapshot_loads(tmp_path: Path) -> None:
    _write_raw(tmp_path, "ecmwf.json", _valid_payload())
    rows = metrics_module.load_fastpath_snapshots(data_root=tmp_path)
    assert [row["model_id"] for row in rows] == ["ecmwf"]


def test_a_stale_snapshot_is_dropped(tmp_path: Path) -> None:
    """A stopped scheduler must stop publishing, not freeze its last reading."""
    _write_raw(
        tmp_path, "ecmwf.json", _valid_payload(recorded_at=time.time() - 48 * 3600)
    )
    assert metrics_module.load_fastpath_snapshots(data_root=tmp_path) == []


def test_an_unknown_schema_version_is_dropped(tmp_path: Path) -> None:
    _write_raw(tmp_path, "ecmwf.json", _valid_payload(schema_version=99))
    assert metrics_module.load_fastpath_snapshots(data_root=tmp_path) == []


def test_a_filename_payload_mismatch_is_dropped(tmp_path: Path) -> None:
    _write_raw(tmp_path, "ecmwf.json", _valid_payload(model_id="gfs"))
    assert metrics_module.load_fastpath_snapshots(data_root=tmp_path) == []


def test_unparseable_json_is_skipped_not_raised(tmp_path: Path) -> None:
    directory = metrics_module.fastpath_snapshot_dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ecmwf.json").write_text("{not json")
    assert metrics_module.load_fastpath_snapshots(data_root=tmp_path) == []


def test_blocked_pairs_flatten_with_their_model(tmp_path: Path) -> None:
    rows = [_valid_payload(blocked_pairs=[["precip_total", "na"]])]
    assert metrics_module.blocked_pairs_from_snapshots(rows) == [
        ["ecmwf", "precip_total", "na"]
    ]


# ---------------------------------------------------------------------------
# Prometheus republish
# ---------------------------------------------------------------------------


def _sample(name: str, **labels) -> float | None:
    return prometheus_metrics._REGISTRY.get_sample_value(name, labels)


def test_gauges_are_published_from_a_snapshot() -> None:
    prometheus_metrics.replace_fastpath_metrics(
        [
            _valid_payload(
                ready_through_fh=[
                    {"var": "tmp2m", "domain": "global", "ready_through_fh": 72}
                ],
                stall_count_by_run={RUN_A: 4},
                blocked_pairs=[["precip_total", "na"]],
                blocked_pair_count=1,
                promotion_retries_pending=3,
                canary_results=[
                    {
                        "var": "tmp2m",
                        "domain": "global",
                        "bias": -0.02,
                        "mae": 0.31,
                        "synoptic_mae": 0.08,
                        "corr": 0.9991,
                        "failed": False,
                    }
                ],
            )
        ]
    )
    assert _sample(
        "cartosky_fastpath_ready_through_fh",
        model_id="ecmwf",
        var="tmp2m",
        domain="global",
    ) == 72
    assert _sample("cartosky_fastpath_stall_count", model_id="ecmwf", run=RUN_A) == 4
    assert _sample("cartosky_fastpath_blocked_pairs", model_id="ecmwf") == 1
    assert (
        _sample("cartosky_fastpath_promotion_retries_pending", model_id="ecmwf") == 3
    )
    labels = {"model_id": "ecmwf", "var": "tmp2m", "domain": "global"}
    assert _sample("cartosky_fastpath_canary_bias", **labels) == pytest.approx(-0.02)
    assert _sample("cartosky_fastpath_canary_mae", **labels) == pytest.approx(0.31)
    assert _sample(
        "cartosky_fastpath_canary_synoptic_mae", **labels
    ) == pytest.approx(0.08)
    assert _sample("cartosky_fastpath_canary_corr", **labels) == pytest.approx(0.9991)
    assert _sample("cartosky_fastpath_canary_failed", **labels) == 0


def test_a_failed_canary_raises_the_failure_gauge() -> None:
    prometheus_metrics.replace_fastpath_metrics(
        [
            _valid_payload(
                canary_results=[
                    {
                        "var": "precip_total",
                        "domain": "na",
                        "bias": 0.9,
                        "failed": True,
                        "failures": ["bias"],
                    }
                ]
            )
        ]
    )
    assert (
        _sample(
            "cartosky_fastpath_canary_failed",
            model_id="ecmwf",
            var="precip_total",
            domain="na",
        )
        == 1
    )


def test_a_failed_seam_raises_the_seam_gauge() -> None:
    prometheus_metrics.replace_fastpath_metrics(
        [
            _valid_payload(
                canary_seams=[
                    {
                        "var": "precip_total",
                        "domain": "global",
                        "seam_fh": 42,
                        "continuity_checked": True,
                        "ok": False,
                    }
                ]
            )
        ]
    )
    assert (
        _sample(
            "cartosky_fastpath_canary_seam_failures",
            model_id="ecmwf",
            var="precip_total",
            domain="global",
        )
        == 1
    )
    prometheus_metrics.replace_fastpath_metrics([])


def test_a_passing_seam_publishes_a_zero() -> None:
    prometheus_metrics.replace_fastpath_metrics(
        [
            _valid_payload(
                canary_seams=[
                    {
                        "var": "precip_total",
                        "domain": "na",
                        "continuity_checked": True,
                        "ok": True,
                    }
                ]
            )
        ]
    )
    assert (
        _sample(
            "cartosky_fastpath_canary_seam_failures",
            model_id="ecmwf",
            var="precip_total",
            domain="na",
        )
        == 0
    )
    prometheus_metrics.replace_fastpath_metrics([])


def test_an_unchecked_seam_publishes_no_series() -> None:
    """An instantaneous 9 km→0.25° seam is expected and was never checked.

    Publishing a 0 for it would imply a passing check that never ran.
    """
    prometheus_metrics.replace_fastpath_metrics(
        [
            _valid_payload(
                canary_seams=[
                    {
                        "var": "tmp2m",
                        "domain": "global",
                        "continuity_checked": False,
                        "ok": True,
                    }
                ]
            )
        ]
    )
    assert (
        _sample(
            "cartosky_fastpath_canary_seam_failures",
            model_id="ecmwf",
            var="tmp2m",
            domain="global",
        )
        is None
    )
    prometheus_metrics.replace_fastpath_metrics([])


def test_an_undefined_metric_leaves_its_series_absent() -> None:
    """A null correlation means "undefined" (a constant field), not zero."""
    prometheus_metrics.replace_fastpath_metrics(
        [
            _valid_payload(
                canary_results=[
                    {
                        "var": "precip_total",
                        "domain": "global",
                        "bias": 0.0,
                        "corr": None,
                        "failed": False,
                    }
                ]
            )
        ]
    )
    labels = {"model_id": "ecmwf", "var": "precip_total", "domain": "global"}
    assert _sample("cartosky_fastpath_canary_bias", **labels) == 0.0
    assert _sample("cartosky_fastpath_canary_corr", **labels) is None


def test_series_are_cleared_when_a_condition_resolves() -> None:
    """A stale ``blocked_pairs`` reading would page for an outage that is over."""
    prometheus_metrics.replace_fastpath_metrics(
        [_valid_payload(blocked_pairs=[["precip_total", "na"]], blocked_pair_count=1)]
    )
    assert _sample("cartosky_fastpath_blocked_pairs", model_id="ecmwf") == 1
    prometheus_metrics.replace_fastpath_metrics([])
    assert _sample("cartosky_fastpath_blocked_pairs", model_id="ecmwf") is None


def test_republishing_nothing_is_safe() -> None:
    prometheus_metrics.replace_fastpath_metrics([])
    assert _sample("cartosky_fastpath_snapshot_timestamp_seconds", model_id="ecmwf") is None


def test_end_to_end_scheduler_write_to_api_read(tmp_path: Path) -> None:
    """The whole handoff: scheduler writes JSON, API turns it into gauges."""
    state = _state(tmp_path, RUN_A)
    state.record_published("tmp2m", "global", 3, expected_fhs=[3, 6])
    state.pair("snowfall_total", "global").blocked = True
    state.save()

    metrics_module.write_pass_snapshot(
        data_root=tmp_path, model_id="ecmwf", pass_result=_pass_result()
    )
    prometheus_metrics.replace_fastpath_metrics(
        metrics_module.load_fastpath_snapshots(data_root=tmp_path)
    )
    assert _sample("cartosky_fastpath_blocked_pairs", model_id="ecmwf") == 1
    assert _sample(
        "cartosky_fastpath_ready_through_fh",
        model_id="ecmwf",
        var="tmp2m",
        domain="global",
    ) == 3
    assert (
        _sample("cartosky_fastpath_snapshot_timestamp_seconds", model_id="ecmwf")
        is not None
    )
    prometheus_metrics.replace_fastpath_metrics([])
