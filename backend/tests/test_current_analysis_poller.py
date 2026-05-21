from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import rtma_ru_poller
from app.services.rtma_ru_publish import CurrentAnalysisBundleFrame, CurrentAnalysisPublishResult


def _config(tmp_path: Path) -> rtma_ru_poller.CurrentAnalysisPollerConfig:
    return rtma_ru_poller.CurrentAnalysisPollerConfig(
        data_root=tmp_path,
        cache_dir=tmp_path / "cache",
        product="anl",
        poll_seconds=300,
        keep_runs=4,
        window_minutes=120,
        frame_cadence_minutes=15,
        lookback_minutes=240,
        allow_grib_without_idx=False,
        source_priority=("aws", "nomads"),
    )


def _frame(valid_time: datetime) -> CurrentAnalysisBundleFrame:
    values = np.ones((2, 2), dtype=np.float32)
    return CurrentAnalysisBundleFrame(
        valid_time=valid_time,
        values_by_var={
            "tmp2m": values,
            "dp2m": values,
            "wspd10m": values,
            "wgst10m": values,
            "spres": values,
        },
        transform=None,
    )


def test_compute_target_frame_count_uses_inclusive_window() -> None:
    assert rtma_ru_poller.compute_target_frame_count(window_minutes=120, frame_cadence_minutes=15) == 9


def test_run_once_publishes_when_new_cycle_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    run_times = [
        datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 21, 12, 15, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(rtma_ru_poller, "discover_recent_run_times", lambda *_args, **_kwargs: run_times)
    monkeypatch.setattr(rtma_ru_poller, "freeze_bundle_run_times", lambda items, **_: items)
    monkeypatch.setattr(rtma_ru_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(rtma_ru_poller, "load_latest_published_current_analysis_frames", lambda _: (None, []))
    monkeypatch.setattr(rtma_ru_poller, "build_bundle_frame", lambda **kwargs: _frame(kwargs["run_time"]))
    monkeypatch.setattr(
        rtma_ru_poller,
        "publish_current_analysis_bundle",
        lambda **_: CurrentAnalysisPublishResult(
            run_id="20260521_1217z",
            published_run_dir=tmp_path / "published" / "current_analysis" / "20260521_1217z",
            manifest_path=tmp_path / "manifests" / "current_analysis" / "20260521_1217z.json",
            frame_count=2,
        ),
    )
    monkeypatch.setattr(rtma_ru_poller, "_enforce_retention", lambda _: None)

    result = rtma_ru_poller.run_once(config)
    assert result.action == "published"
    assert result.published_run_id == "20260521_1217z"
    assert result.expected_frame_count == 2
    assert result.decoded_frame_count == 2
    assert result.failed_scan_count == 0


def test_run_once_skips_when_latest_cycle_already_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    newest = datetime(2026, 5, 21, 12, 15, tzinfo=timezone.utc)
    run_times = [datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc), newest]
    monkeypatch.setattr(rtma_ru_poller, "discover_recent_run_times", lambda *_args, **_kwargs: run_times)
    monkeypatch.setattr(rtma_ru_poller, "freeze_bundle_run_times", lambda items, **_: items)
    monkeypatch.setattr(rtma_ru_poller, "_latest_published_bundle_state", lambda _: (newest, True))

    result = rtma_ru_poller.run_once(config)
    assert result.action == "noop"
    assert result.published_run_id is None
    assert "No new Current Analysis cycle" in result.message


def test_run_once_publishes_partial_bundle_when_one_cycle_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    run_times = [
        datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 21, 12, 15, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(rtma_ru_poller, "discover_recent_run_times", lambda *_args, **_kwargs: run_times)
    monkeypatch.setattr(rtma_ru_poller, "freeze_bundle_run_times", lambda items, **_: items)
    monkeypatch.setattr(rtma_ru_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(rtma_ru_poller, "load_latest_published_current_analysis_frames", lambda _: (None, []))

    def _build(**kwargs):
        if kwargs["run_time"].minute == 15:
            raise RuntimeError("boom")
        return _frame(kwargs["run_time"])

    monkeypatch.setattr(rtma_ru_poller, "build_bundle_frame", _build)
    monkeypatch.setattr(
        rtma_ru_poller,
        "publish_current_analysis_bundle",
        lambda **_: CurrentAnalysisPublishResult(
            run_id="20260521_1217z",
            published_run_dir=tmp_path / "published" / "current_analysis" / "20260521_1217z",
            manifest_path=tmp_path / "manifests" / "current_analysis" / "20260521_1217z.json",
            frame_count=1,
        ),
    )
    monkeypatch.setattr(rtma_ru_poller, "_enforce_retention", lambda _: None)

    result = rtma_ru_poller.run_once(config)
    assert result.action == "published"
    assert result.decoded_frame_count == 1
    assert result.failed_scan_count == 1


def test_build_bundle_frame_derives_wind_and_uses_surface_pressure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    captured_patterns: list[str] = []
    shared_transform = object()
    warped_transform = object()

    def _fetch_variable(*, search_pattern: str, **_kwargs):
        captured_patterns.append(search_pattern)
        values_map = {
            ":TMP:2 m above ground:": np.array([[10.0, 12.0], [14.0, 16.0]], dtype=np.float32),
            ":DPT:2 m above ground:": np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
            ":GUST:10 m above ground:": np.array([[20.0, 22.0], [24.0, 26.0]], dtype=np.float32),
            ":PRES:surface:": np.array([[100000.0, 100100.0], [100200.0, 100300.0]], dtype=np.float32),
            ":UGRD:10 m above ground:": np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32),
            ":VGRD:10 m above ground:": np.array([[4.0, 3.0], [0.0, 5.0]], dtype=np.float32),
        }
        return values_map[search_pattern], "EPSG:4326", shared_transform, {
            "inventory_line": f"{search_pattern}anl:",
            "search_pattern": search_pattern,
            "product": "anl",
        }

    monkeypatch.setattr(rtma_ru_poller, "fetch_variable", _fetch_variable)
    monkeypatch.setattr(
        rtma_ru_poller,
        "convert_units",
        lambda data, var_key, **_kwargs: (
            np.asarray(data, dtype=np.float32) / 100.0
            if var_key == "spres"
            else np.asarray(data, dtype=np.float32) * 2.23694
            if var_key in {"wspd10m", "wgst10m"}
            else np.asarray(data, dtype=np.float32) * 9.0 / 5.0 + 32.0
        ),
    )
    monkeypatch.setattr(rtma_ru_poller, "warp_to_target_grid", lambda data, *_args, **_kwargs: (np.asarray(data, dtype=np.float32), warped_transform))

    frame = rtma_ru_poller.build_bundle_frame(
        run_time=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        config=config,
        bundle_fetch_cache=None,
    )

    assert set(frame.values_by_var) == {"tmp2m", "dp2m", "wspd10m", "wgst10m", "spres"}
    np.testing.assert_allclose(frame.values_by_var["spres"], np.array([[1000.0, 1001.0], [1002.0, 1003.0]], dtype=np.float32))
    np.testing.assert_allclose(
        frame.values_by_var["wspd10m"],
        np.array([[5.0, 5.0], [0.0, 5.0]], dtype=np.float32) * np.float32(2.23694),
    )
    assert frame.transform is warped_transform
    assert frame.source_metadata_by_var["spres"]["inventory_line"] == ":PRES:surface:anl:"
    assert frame.source_metadata_by_var["wspd10m"]["inventory_lines"] == [
        ":UGRD:10 m above ground:anl:",
        ":VGRD:10 m above ground:anl:",
    ]
    assert ":PRES:surface:" in captured_patterns
