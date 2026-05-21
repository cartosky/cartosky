from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import goes_poller
from app.services.goes_fetch import GOESScanRef
from app.services.goes_publish import GOESPublishResult


def _config(tmp_path: Path) -> goes_poller.GOESPollerConfig:
    return goes_poller.GOESPollerConfig(
        data_root=tmp_path,
        cache_dir=tmp_path / "cache",
        provider="noaa",
        satellite="goes19",
        bucket="noaa-goes19",
        product="ABI-L2-CMIPC",
        sector="C",
        bands=(13,),
        poll_seconds=300,
        keep_runs=4,
        window_minutes=180,
        frame_cadence_minutes=15,
        listing_lookback_hours=5,
        object_min_age_seconds=120,
        min_object_bytes=1_000_000,
    )


def test_compute_target_frame_count_uses_inclusive_window() -> None:
    assert goes_poller.compute_target_frame_count(window_minutes=180, frame_cadence_minutes=15) == 13


def test_run_once_publishes_new_goes_scan(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    scan = GOESScanRef(
        bucket="noaa-goes19",
        key="ABI-L2-CMIPC/2026/141/12/file.nc",
        filename="file.nc",
        product="ABI-L2-CMIPC",
        sector="C",
        band=13,
        satellite="goes19",
        scan_start_time=datetime(2026, 5, 21, 12, 1, tzinfo=timezone.utc),
        scan_end_time=datetime(2026, 5, 21, 12, 3, tzinfo=timezone.utc),
        created_time=datetime(2026, 5, 21, 12, 4, tzinfo=timezone.utc),
        slot_time=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        size_bytes=4_000_000,
        last_modified=datetime(2026, 5, 21, 12, 4, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(goes_poller.boto3, "client", lambda *_, **__: object())
    monkeypatch.setattr(goes_poller, "discover_recent_scans_s3", lambda **_: [scan])
    monkeypatch.setattr(goes_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(goes_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(goes_poller, "load_latest_published_goes_frames", lambda _: (None, []))
    monkeypatch.setattr(goes_poller, "download_scan", lambda scan, **_: tmp_path / scan.filename)

    class Decoded:
        valid_time = datetime(2026, 5, 21, 12, 2, 30, tzinfo=timezone.utc)
        values = np.ones((2, 2), dtype=np.float32)
        transform = object()
        projection = "EPSG:3857"
        source_metadata = {
            "time_coverage_start": "2026-05-21T12:01:00Z",
            "time_coverage_end": "2026-05-21T12:03:00Z",
        }

    monkeypatch.setattr(goes_poller, "decode_goes_scan", lambda *_: Decoded())
    monkeypatch.setattr(
        goes_poller,
        "publish_goes_bundle",
        lambda **_: GOESPublishResult(
            run_id="20260521_1205z",
            published_run_dir=tmp_path / "published" / "goes-east" / "20260521_1205z",
            manifest_path=tmp_path / "manifests" / "goes-east" / "20260521_1205z.json",
            frame_count=1,
        ),
    )
    monkeypatch.setattr(goes_poller, "_enforce_retention", lambda _: None)

    result = goes_poller.run_once(config)
    assert result.action == "published"
    assert result.published_run_id == "20260521_1205z"
    assert result.decoded_frame_count == 1
