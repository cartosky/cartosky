"""Unit tests for the frames/grid 404 telemetry registry.

Covers classification bookkeeping, seconds-since-publish recency bucketing, and
survival of a simulated API restart (persist -> clear -> lazy reload). No
network, no FastAPI app — the routing-level classifier is exercised in
test_admin_status_api.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import frames_404_telemetry as telemetry


def test_record_aggregates_totals_recency_and_recent(tmp_path: Path) -> None:
    telemetry.reset()

    telemetry.record_frames_404(
        data_root=tmp_path, endpoint="grid_file", reason="stale_run",
        model="hrrr", run_requested="20260714_00z", run_resolved=None,
        var="tmp2m", filename_or_fh="fh006.l0.u16.bin",
    )
    telemetry.record_frames_404(
        data_root=tmp_path, endpoint="grid_file", reason="swap_gap",
        model="hrrr", run_requested="latest", run_resolved="20260714_12z",
        var="tmp2m", filename_or_fh="fh006.l0.u16.bin", seconds_since_publish=0.4,
    )
    telemetry.record_frames_404(
        data_root=tmp_path, endpoint="grid_file", reason="swap_gap",
        model="hrrr", run_requested="latest", run_resolved="20260714_12z",
        var="tmp2m", filename_or_fh="fh012.l0.u16.bin", seconds_since_publish=3.2,
    )
    telemetry.record_frames_404(
        data_root=tmp_path, endpoint="frames", reason="manifest_skew",
        model="gefs", run_requested="latest", run_resolved="20260714_12z",
        var="precip_total", seconds_since_publish=12.0,
    )

    summary = telemetry.load_frames_404_summary(tmp_path)

    assert summary["totals_by_reason"] == {
        "stale_run": 1,
        "swap_gap": 2,
        "manifest_skew": 1,
    }
    # swap_gap: one lt1s (0.4s), one lt5s (3.2s); manifest_skew: one gte5s (12s).
    assert summary["recency_buckets"]["swap_gap"] == {"lt1s": 1, "lt5s": 1, "gte5s": 0}
    assert summary["recency_buckets"]["manifest_skew"] == {"lt1s": 0, "lt5s": 0, "gte5s": 1}
    # Most recent first; stale_run carries a null seconds_since_publish.
    assert summary["recent"][0]["reason"] == "manifest_skew"
    assert summary["recent"][-1]["reason"] == "stale_run"
    assert summary["recent"][-1]["seconds_since_publish"] is None
    assert summary["today"]["swap_gap"] == 2
    assert summary["last_7_days"]["stale_run"] == 1
    assert isinstance(summary["since"], str) and summary["since"]


def test_counters_survive_simulated_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "_PERSIST_THROTTLE_S", 0.0)
    telemetry.reset()

    for _ in range(3):
        telemetry.record_frames_404(
            data_root=tmp_path, endpoint="grid_file", reason="swap_gap",
            model="hrrr", run_requested="latest", run_resolved="20260714_12z",
            var="tmp2m", filename_or_fh="fh006.l0.u16.bin", seconds_since_publish=0.2,
        )

    assert telemetry._telemetry_path(tmp_path).is_file()

    # Simulate an API restart: drop all in-memory state, then read back.
    telemetry.reset()
    reloaded = telemetry.load_frames_404_summary(tmp_path)

    assert reloaded["totals_by_reason"] == {"swap_gap": 3}
    assert reloaded["recency_buckets"]["swap_gap"]["lt1s"] == 3
    assert len(reloaded["recent"]) == 3


def test_reset_clears_state(tmp_path: Path) -> None:
    telemetry.reset()
    telemetry.record_frames_404(
        data_root=tmp_path, endpoint="grid_file", reason="stale_run",
        model="hrrr", run_requested="bad", run_resolved=None, var="tmp2m",
    )
    assert telemetry.load_frames_404_summary(tmp_path)["totals_by_reason"] == {"stale_run": 1}

    telemetry.reset()
    # A fresh tmp dir with no persisted file yields an empty summary.
    empty = telemetry.load_frames_404_summary(tmp_path / "other")
    assert empty["totals_by_reason"] == {}
    assert empty["recent"] == []
