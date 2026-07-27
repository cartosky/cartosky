from __future__ import annotations

from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import admin_telemetry  # noqa: E402
from app.services import scheduler  # noqa: E402
from app.services.builder import fetch as builder_fetch  # noqa: E402


def test_fetch_runtime_snapshot_round_trips_process_metrics(tmp_path: Path) -> None:
    admin_telemetry.write_fetch_runtime_snapshot(
        data_root=tmp_path,
        model_id="GFS",
        metrics={
            "counters": {
                "herbie_call_timeout": 2,
                "idx_cache_hit": 7,
            },
            "timers_ms": {
                "herbie_call_ms": {
                    "count": 4,
                    "sum_ms": 1250.5,
                    "avg_ms": 312.625,
                    "max_ms": 800.0,
                },
            },
        },
        recorded_at=1_775_000_000.0,
        process_id=4321,
    )

    assert admin_telemetry.load_fetch_runtime_snapshots(
        data_root=tmp_path,
        now=1_775_000_030.0,
    ) == [
        {
            "schema_version": 1,
            "model_id": "gfs",
            "process_id": 4321,
            "recorded_at": 1_775_000_000.0,
            "counters": {
                "herbie_call_timeout": 2,
                "idx_cache_hit": 7,
            },
            "timers_ms": {
                "herbie_call_ms": {
                    "count": 4,
                    "sum_ms": 1250.5,
                    "avg_ms": 312.625,
                    "max_ms": 800.0,
                },
            },
        }
    ]


def test_scheduler_exports_the_live_fetch_runtime_snapshot(tmp_path: Path) -> None:
    builder_fetch.reset_herbie_runtime_caches_for_tests()
    builder_fetch._metric_increment("herbie_call_timeout", 1)
    builder_fetch._metric_observe_ms("herbie_call_ms", 250.0)

    scheduler._export_fetch_runtime_metrics(
        data_root=tmp_path,
        model_id="gfs",
    )

    snapshots = admin_telemetry.load_fetch_runtime_snapshots(data_root=tmp_path)
    assert len(snapshots) == 1
    assert snapshots[0]["model_id"] == "gfs"
    assert snapshots[0]["counters"]["herbie_call_timeout"] == 1
    assert snapshots[0]["timers_ms"]["herbie_call_ms"]["count"] == 1


def test_new_scheduler_process_preserves_completed_process_metrics(tmp_path: Path) -> None:
    admin_telemetry.write_fetch_runtime_snapshot(
        data_root=tmp_path,
        model_id="gfs",
        metrics={
            "counters": {"herbie_call_timeout": 2},
            "timers_ms": {
                "herbie_call_ms": {
                    "count": 3,
                    "sum_ms": 900.0,
                    "max_ms": 500.0,
                },
            },
        },
        process_id=111,
    )
    admin_telemetry.write_fetch_runtime_snapshot(
        data_root=tmp_path,
        model_id="gfs",
        metrics={"counters": {}, "timers_ms": {}},
        process_id=222,
    )

    snapshots = admin_telemetry.load_fetch_runtime_snapshots(data_root=tmp_path)
    assert snapshots[0]["counters"]["herbie_call_timeout"] == 2
    assert snapshots[0]["timers_ms"]["herbie_call_ms"]["count"] == 3
    assert snapshots[0]["timers_ms"]["herbie_call_ms"]["sum_ms"] == 900.0
