from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import mrms_poller
from app.services.mrms_fetch import MRMSScanRef
from app.services.mrms_publish import MRMSPublishResult


def _config(tmp_path: Path) -> mrms_poller.MRMSPollerConfig:
    return mrms_poller.MRMSPollerConfig(
        data_root=tmp_path,
        listing_url="https://example.test/mrms/",
        poll_seconds=120,
        keep_runs=4,
        window_minutes=120,
        frame_cadence_minutes=2,
        listing_timeout_seconds=15.0,
        download_timeout_seconds=15.0,
        preferred_decoder="wgrib2",
        fallback_decoder="pygrib",
        frame_write_workers=1,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop_cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=1600,
        loop_tier0_fixed_w=1600,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )


def test_run_once_publishes_when_new_scan_exists(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    scans = [
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
            url="https://example.test/a.grib2.gz",
            filename="a.grib2.gz",
        ),
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc),
            url="https://example.test/b.grib2.gz",
            filename="b.grib2.gz",
        ),
    ]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: scans)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(
        mrms_poller,
        "download_scan",
        lambda scan, **_: tmp_path / scan.filename,
    )

    class _Decoded:
        def __init__(self, valid_time):
            self.valid_time = valid_time
            self.values = np.ones((2, 2), dtype=np.float32)
            self.decoder = "wgrib2"
            self.metadata = {}

    monkeypatch.setattr(
        mrms_poller,
        "decode_scan",
        lambda path, valid_time, **_: _Decoded(valid_time),
    )
    monkeypatch.setattr(
        mrms_poller,
        "publish_mrms_bundle",
        lambda **_: MRMSPublishResult(
            run_id="20260327_1204z",
            published_run_dir=tmp_path / "published" / "mrms" / "20260327_1204z",
            manifest_path=tmp_path / "manifests" / "mrms" / "20260327_1204z.json",
            frame_count=2,
        ),
    )
    monkeypatch.setattr(mrms_poller, "_enforce_retention", lambda _: None)

    result = mrms_poller.run_once(config)
    assert result.action == "published"
    assert result.published_run_id == "20260327_1204z"
    assert result.expected_frame_count == 2
    assert result.decoded_frame_count == 2
    assert result.failed_scan_count == 0


def test_run_once_skips_when_latest_scan_already_published(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    newest = datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc)
    scans = [
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
            url="https://example.test/a.grib2.gz",
            filename="a.grib2.gz",
        ),
        MRMSScanRef(
            valid_time=newest,
            url="https://example.test/b.grib2.gz",
            filename="b.grib2.gz",
        ),
    ]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: scans)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (newest, True))

    result = mrms_poller.run_once(config)
    assert result.action == "noop"
    assert result.published_run_id is None
    assert "No new MRMS scan" in result.message


def test_run_once_publishes_partial_bundle_when_one_scan_fails(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    scans = [
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
            url="https://example.test/a.grib2.gz",
            filename="a.grib2.gz",
        ),
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc),
            url="https://example.test/b.grib2.gz",
            filename="b.grib2.gz",
        ),
    ]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: scans)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(mrms_poller, "download_scan", lambda scan, **_: tmp_path / scan.filename)

    class _Decoded:
        def __init__(self, valid_time):
            self.valid_time = valid_time
            self.values = np.ones((2, 2), dtype=np.float32)
            self.decoder = "wgrib2"
            self.metadata = {}

    def _decode(path, valid_time, **_):
        if str(path).endswith("b.grib2.gz"):
            raise RuntimeError("boom")
        return _Decoded(valid_time)

    monkeypatch.setattr(mrms_poller, "decode_scan", _decode)
    monkeypatch.setattr(
        mrms_poller,
        "publish_mrms_bundle",
        lambda **_: MRMSPublishResult(
            run_id="20260327_1204z",
            published_run_dir=tmp_path / "published" / "mrms" / "20260327_1204z",
            manifest_path=tmp_path / "manifests" / "mrms" / "20260327_1204z.json",
            frame_count=1,
        ),
    )
    monkeypatch.setattr(mrms_poller, "_enforce_retention", lambda _: None)

    result = mrms_poller.run_once(config)
    assert result.action == "published"
    assert result.decoded_frame_count == 1
    assert result.failed_scan_count == 1


def test_run_once_retries_same_latest_scan_when_existing_bundle_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    newest = datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc)
    scans = [
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
            url="https://example.test/a.grib2.gz",
            filename="a.grib2.gz",
        ),
        MRMSScanRef(
            valid_time=newest,
            url="https://example.test/b.grib2.gz",
            filename="b.grib2.gz",
        ),
    ]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: scans)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (newest, False))
    monkeypatch.setattr(mrms_poller, "download_scan", lambda scan, **_: tmp_path / scan.filename)

    class _Decoded:
        def __init__(self, valid_time):
            self.valid_time = valid_time
            self.values = np.ones((2, 2), dtype=np.float32)
            self.decoder = "wgrib2"
            self.metadata = {}

    monkeypatch.setattr(mrms_poller, "decode_scan", lambda path, valid_time, **_: _Decoded(valid_time))
    monkeypatch.setattr(
        mrms_poller,
        "publish_mrms_bundle",
        lambda **_: MRMSPublishResult(
            run_id="20260327_1204z",
            published_run_dir=tmp_path / "published" / "mrms" / "20260327_1204z",
            manifest_path=tmp_path / "manifests" / "mrms" / "20260327_1204z.json",
            frame_count=2,
        ),
    )
    monkeypatch.setattr(mrms_poller, "_enforce_retention", lambda _: None)

    result = mrms_poller.run_once(config)
    assert result.action == "published"
