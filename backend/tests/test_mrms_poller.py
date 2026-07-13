from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import mrms_poller
from app.services.mrms_fetch import MRMSScanRef
from app.services.mrms_publish import MRMSPublishResult, MRMSPublishedFrame


def _config(tmp_path: Path) -> mrms_poller.MRMSPollerConfig:
    return mrms_poller.MRMSPollerConfig(
        data_root=tmp_path,
        listing_url="https://example.test/mrms/",
        precip_flag_listing_url="",
        poll_seconds=120,
        keep_runs=4,
        window_minutes=120,
        frame_cadence_minutes=2,
        listing_timeout_seconds=15.0,
        download_timeout_seconds=15.0,
        preferred_decoder="wgrib2",
        fallback_decoder="pygrib",
        frame_write_workers=1,
        qpe_06h_listing_url="",
        qpe_24h_listing_url="",
        qpe_72h_listing_url="",
    )


def _config_with_recent_precip(tmp_path: Path) -> mrms_poller.MRMSPollerConfig:
    base = _config(tmp_path)
    return mrms_poller.MRMSPollerConfig(
        data_root=base.data_root,
        listing_url=base.listing_url,
        precip_flag_listing_url=base.precip_flag_listing_url,
        poll_seconds=base.poll_seconds,
        keep_runs=base.keep_runs,
        window_minutes=base.window_minutes,
        frame_cadence_minutes=base.frame_cadence_minutes,
        listing_timeout_seconds=base.listing_timeout_seconds,
        download_timeout_seconds=base.download_timeout_seconds,
        preferred_decoder=base.preferred_decoder,
        fallback_decoder=base.fallback_decoder,
        frame_write_workers=base.frame_write_workers,
        qpe_06h_listing_url="https://example.test/qpe/06h/",
        qpe_24h_listing_url="",
        qpe_72h_listing_url="",
    )


def _disable_postprocess(monkeypatch) -> None:
    monkeypatch.setattr(mrms_poller, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(mrms_poller, "_schedule_postprocess", lambda *_args, **_kwargs: None)


def test_run_once_publishes_when_new_scan_exists(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _disable_postprocess(monkeypatch)
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
    _disable_postprocess(monkeypatch)
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
    _disable_postprocess(monkeypatch)
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
    _disable_postprocess(monkeypatch)
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


# ---------------------------------------------------------------------------
# _find_closest_precip_flag_scan tests
# ---------------------------------------------------------------------------

def test_find_closest_precip_flag_scan_exact_match() -> None:
    t = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    pf_scan = MRMSScanRef(valid_time=t, url="https://example.test/pf.grib2.gz", filename="pf.grib2.gz")
    result = mrms_poller._find_closest_precip_flag_scan(t, {t: pf_scan})
    assert result is pf_scan


def test_find_closest_precip_flag_scan_within_tolerance() -> None:
    from datetime import timedelta

    t = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    pf_time = t + timedelta(minutes=2)
    pf_scan = MRMSScanRef(valid_time=pf_time, url="https://example.test/pf.grib2.gz", filename="pf.grib2.gz")
    result = mrms_poller._find_closest_precip_flag_scan(t, {pf_time: pf_scan})
    assert result is pf_scan


def test_find_closest_precip_flag_scan_beyond_tolerance_returns_none() -> None:
    from datetime import timedelta

    t = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    pf_time = t + timedelta(minutes=5)  # beyond 4-minute tolerance
    pf_scan = MRMSScanRef(valid_time=pf_time, url="https://example.test/pf.grib2.gz", filename="pf.grib2.gz")
    result = mrms_poller._find_closest_precip_flag_scan(t, {pf_time: pf_scan})
    assert result is None


def test_find_closest_precip_flag_scan_picks_nearest_of_multiple() -> None:
    from datetime import timedelta

    t = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    t_minus1 = t - timedelta(minutes=1)
    t_plus3 = t + timedelta(minutes=3)
    scan_near = MRMSScanRef(valid_time=t_minus1, url="https://example.test/near.grib2.gz", filename="near.grib2.gz")
    scan_far = MRMSScanRef(valid_time=t_plus3, url="https://example.test/far.grib2.gz", filename="far.grib2.gz")
    result = mrms_poller._find_closest_precip_flag_scan(
        t, {t_minus1: scan_near, t_plus3: scan_far},
    )
    assert result is scan_near


def test_find_closest_precip_flag_scan_empty_dict_returns_none() -> None:
    t = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    result = mrms_poller._find_closest_precip_flag_scan(t, {})
    assert result is None


def test_run_once_decodes_only_new_scans_when_previous_window_exists(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _disable_postprocess(monkeypatch)
    older = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc)
    scans = [
        MRMSScanRef(valid_time=older, url="https://example.test/a.grib2.gz", filename="a.grib2.gz"),
        MRMSScanRef(valid_time=newer, url="https://example.test/b.grib2.gz", filename="b.grib2.gz"),
    ]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: scans)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (older, True))
    monkeypatch.setattr(mrms_poller, "download_scan", lambda scan, **_: tmp_path / scan.filename)

    previous_sidecar = {
        "contract_version": "3.0",
        "model": "mrms",
        "run": "20260327_1200z",
        "var": "reflectivity",
        "fh": 0,
        "valid_time": "2026-03-27T12:00:00Z",
        "units": "dBZ",
        "kind": "discrete",
        "quality": "full",
        "quality_flags": [],
    }
    monkeypatch.setattr(
        mrms_poller,
        "load_latest_published_mrms_frames",
        lambda _data_root: (
            "20260327_1200z",
            [
                MRMSPublishedFrame(
                    valid_time=older,
                    source_valid_time=older,
                    value_path=tmp_path / "old.val.cog.tif",
                    sidecar=json.loads(json.dumps(previous_sidecar)),
                )
            ],
        ),
    )

    decoded_times: list[datetime] = []

    class _Decoded:
        def __init__(self, valid_time):
            self.valid_time = valid_time
            self.values = np.ones((2, 2), dtype=np.float32)
            self.decoder = "pygrib"
            self.metadata = {}

    def _decode(path, valid_time, **_):
        decoded_times.append(valid_time)
        return _Decoded(valid_time)

    monkeypatch.setattr(mrms_poller, "decode_scan", _decode)

    captured: dict[str, object] = {}

    def _publish(**kwargs):
        captured["previous_frames"] = kwargs.get("previous_frames")
        captured["frames"] = kwargs.get("frames")
        captured["target_frame_count"] = kwargs.get("target_frame_count")
        return MRMSPublishResult(
            run_id="20260327_1204z",
            published_run_dir=tmp_path / "published" / "mrms" / "20260327_1204z",
            manifest_path=tmp_path / "manifests" / "mrms" / "20260327_1204z.json",
            frame_count=2,
        )

    monkeypatch.setattr(mrms_poller, "publish_mrms_bundle", _publish)
    monkeypatch.setattr(mrms_poller, "_enforce_retention", lambda _: None)

    result = mrms_poller.run_once(config)
    assert result.action == "published"
    assert decoded_times == [newer]
    assert len(captured["previous_frames"]) == 1
    assert len(captured["frames"]) == 1
    assert captured["target_frame_count"] == 2


def test_run_once_limits_decode_backlog_to_newest_slice(tmp_path: Path, monkeypatch) -> None:
    config = replace(_config(tmp_path), max_decode_frames_per_cycle=2)
    _disable_postprocess(monkeypatch)
    scans = [
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, minute, tzinfo=timezone.utc),
            url=f"https://example.test/{minute}.grib2.gz",
            filename=f"{minute}.grib2.gz",
        )
        for minute in range(0, 10, 2)
    ]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: scans)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(mrms_poller, "download_scan", lambda scan, **_: tmp_path / scan.filename)

    decoded_times: list[datetime] = []

    class _Decoded:
        def __init__(self, valid_time):
            self.valid_time = valid_time
            self.values = np.ones((2, 2), dtype=np.float32)
            self.decoder = "pygrib"
            self.metadata = {}

    def _decode(path, valid_time, **_):
        decoded_times.append(valid_time)
        return _Decoded(valid_time)

    captured: dict[str, object] = {}
    monkeypatch.setattr(mrms_poller, "decode_scan", _decode)
    monkeypatch.setattr(
        mrms_poller,
        "publish_mrms_bundle",
        lambda **kwargs: captured.update(kwargs) or MRMSPublishResult(
            run_id="20260327_1208z",
            published_run_dir=tmp_path / "published" / "mrms" / "20260327_1208z",
            manifest_path=tmp_path / "manifests" / "mrms" / "20260327_1208z.json",
            frame_count=2,
        ),
    )
    monkeypatch.setattr(mrms_poller, "_enforce_retention", lambda _: None)

    result = mrms_poller.run_once(config)

    assert result.action == "published"
    assert result.expected_frame_count == 5
    assert result.decoded_frame_count == 2
    assert decoded_times == [scans[-2].valid_time, scans[-1].valid_time]
    assert captured["target_frame_count"] == 5


def test_plan_recent_precip_postprocess_reuses_unchanged_upstream_timestamps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config_with_recent_precip(tmp_path)
    valid_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    qpe_scan = MRMSScanRef(
        valid_time=valid_time,
        source_valid_time=valid_time,
        url="https://example.test/qpe/06h/a.grib2.gz",
        filename="a.grib2.gz",
    )

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", lambda **_: [qpe_scan])
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)

    plans = mrms_poller._plan_recent_precip_postprocess(
        config=config,
        previous_run_id="20260327_1158z",
        previous_manifest={
            "variables": {
                "mrms_recent_precip_6h": {
                    "available_frames": 1,
                    "frames": [
                        {"fh": 0, "valid_time": "2026-03-27T12:00:00Z"},
                    ],
                },
            },
        },
    )

    assert len(plans) == 1
    assert plans[0].var_id == "mrms_recent_precip_6h"
    assert plans[0].mode == "reuse"
    assert plans[0].expected_frame_count == 3


def test_run_once_fast_publishes_and_queues_postprocess(tmp_path: Path, monkeypatch) -> None:
    config = _config_with_recent_precip(tmp_path)
    radar_scans = [
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
            url="https://example.test/mrms/a.grib2.gz",
            filename="a.grib2.gz",
        ),
        MRMSScanRef(
            valid_time=datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc),
            url="https://example.test/mrms/b.grib2.gz",
            filename="b.grib2.gz",
        ),
    ]
    qpe_scan = MRMSScanRef(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        source_valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        url="https://example.test/qpe/06h/a.grib2.gz",
        filename="qpe06.grib2.gz",
    )

    def _discover(**kwargs):
        if kwargs.get("listing_url") == config.listing_url:
            return radar_scans
        return [qpe_scan]

    monkeypatch.setattr(mrms_poller, "discover_recent_scans_http", _discover)
    monkeypatch.setattr(mrms_poller, "freeze_bundle_scans", lambda items, **_: items)
    monkeypatch.setattr(mrms_poller, "_latest_published_bundle_state", lambda _: (None, False))
    monkeypatch.setattr(mrms_poller, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(mrms_poller, "download_scan", lambda scan, **_: tmp_path / scan.filename)

    class _Decoded:
        def __init__(self, valid_time):
            self.valid_time = valid_time
            self.values = np.ones((2, 2), dtype=np.float32)
            self.decoder = "wgrib2"
            self.metadata = {}

    monkeypatch.setattr(mrms_poller, "decode_scan", lambda path, valid_time, **_: _Decoded(valid_time))

    published: dict[str, object] = {}
    queued: list[mrms_poller.MRMSPostprocessRequest] = []

    def _publish(**kwargs):
        published.update(kwargs)
        return MRMSPublishResult(
            run_id="20260327_1204z",
            published_run_dir=tmp_path / "published" / "mrms" / "20260327_1204z",
            manifest_path=tmp_path / "manifests" / "mrms" / "20260327_1204z.json",
            frame_count=2,
        )

    monkeypatch.setattr(mrms_poller, "publish_mrms_bundle", _publish)
    monkeypatch.setattr(mrms_poller, "_schedule_postprocess", lambda request: queued.append(request))
    monkeypatch.setattr(mrms_poller, "_enforce_retention", lambda _: None)

    result = mrms_poller.run_once(config)

    assert result.action == "published"
    assert published["build_grid_artifacts"] is False
    assert published.get("supplemental_variable_frames") is None
    assert len(queued) == 1
    assert queued[0].run_id == "20260327_1204z"
    assert len(queued[0].supplemental_plans) == 1
    assert queued[0].supplemental_plans[0].var_id == "mrms_recent_precip_6h"
    assert queued[0].supplemental_plans[0].mode == "build"


def test_run_postprocess_request_reuses_existing_supplemental_artifacts(tmp_path: Path, monkeypatch) -> None:
    config = _config_with_recent_precip(tmp_path)
    previous_run_id = "20260327_1200z"
    current_run_id = "20260327_1204z"

    source_dir = tmp_path / "published" / "mrms" / previous_run_id / "mrms_recent_precip_6h"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "fh000.json").write_text("{}")
    (source_dir / "fh000.val.cog.tif").write_bytes(b"cog")

    current_run_dir = tmp_path / "published" / "mrms" / current_run_id
    current_run_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = tmp_path / "manifests" / "mrms"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{current_run_id}.json").write_text(
        json.dumps(
            {
                "run": current_run_id,
                "last_updated": "2026-03-27T12:04:00Z",
                "variables": {
                    "reflectivity": {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-03-27T12:00:00Z"}],
                    },
                },
                "metadata": {},
            }
        )
    )

    captured: dict[str, object] = {}

    def _finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mrms_poller, "finalize_mrms_published_run", _finalize)
    monkeypatch.setattr(mrms_poller, "grid_build_enabled", lambda: False)

    plan = mrms_poller.MRMSSupplementalPlan(
        var_id="mrms_recent_precip_6h",
        expected_frame_count=1,
        mode="reuse",
        frozen_scans=(),
        file_re=object(),
        previous_manifest_entry={
            "expected_frames": 1,
            "available_frames": 1,
            "frames": [{"fh": 0, "valid_time": "2026-03-27T12:00:00Z"}],
        },
    )

    mrms_poller._run_postprocess_request(
        mrms_poller.MRMSPostprocessRequest(
            data_root=tmp_path,
            run_id=current_run_id,
            previous_run_id=previous_run_id,
            config=config,
            supplemental_plans=(plan,),
        )
    )

    assert captured["run_id"] == current_run_id
    assert captured["reused_supplemental_from_run_id"] == previous_run_id
    assert captured["reused_supplemental_manifest_entries"] == {
        "mrms_recent_precip_6h": {
            "expected_frames": 1,
            "available_frames": 1,
            "frames": [{"fh": 0, "valid_time": "2026-03-27T12:00:00Z"}],
        }
    }
    assert captured.get("supplemental_variable_frames") is None


def test_run_once_defers_while_postprocess_is_active(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    class _ActiveFuture:
        def done(self) -> bool:
            return False

    monkeypatch.setattr(mrms_poller, "_POSTPROCESS_FUTURE", _ActiveFuture())

    result = mrms_poller.run_once(config)

    assert result.action == "noop"
    assert "postprocess is still running" in result.message


def test_run_postprocess_worker_drains_all_queued_requests(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    processed: list[str] = []

    monkeypatch.setattr(
        mrms_poller,
        "_run_postprocess_request",
        lambda request: processed.append(request.run_id),
    )

    first = mrms_poller.MRMSPostprocessRequest(
        data_root=tmp_path,
        run_id="20260327_1204z",
        previous_run_id=None,
        config=config,
    )
    second = mrms_poller.MRMSPostprocessRequest(
        data_root=tmp_path,
        run_id="20260327_1206z",
        previous_run_id="20260327_1204z",
        config=config,
    )
    third = mrms_poller.MRMSPostprocessRequest(
        data_root=tmp_path,
        run_id="20260327_1208z",
        previous_run_id="20260327_1206z",
        config=config,
    )

    mrms_poller._PENDING_POSTPROCESS.clear()
    mrms_poller._POSTPROCESS_FUTURE = object()  # non-None sentinel while worker is active
    mrms_poller._PENDING_POSTPROCESS.extend((second, third))

    mrms_poller._run_postprocess_worker(first)

    assert processed == ["20260327_1204z", "20260327_1206z", "20260327_1208z"]
    assert list(mrms_poller._PENDING_POSTPROCESS) == []
    assert mrms_poller._POSTPROCESS_FUTURE is None


def test_mask_mrms_sentinels_masks_reflectivity_codes_only() -> None:
    values = np.array([[-999.0, -99.0], [-18.0, 40.0]], dtype=np.float32)

    masked = mrms_poller._mask_mrms_sentinels(values, mrms_poller.MRMS_REFLECTIVITY_SENTINELS)

    assert np.isnan(masked[0, 0])
    assert np.isnan(masked[0, 1])
    assert masked[1, 0] == np.float32(-18.0)
    assert masked[1, 1] == np.float32(40.0)
    # Input untouched (masking copies before writing)
    assert values[0, 0] == np.float32(-999.0)


def test_mask_mrms_sentinels_without_sentinels_is_passthrough() -> None:
    values = np.array([[0.0, 25.5]], dtype=np.float32)

    masked = mrms_poller._mask_mrms_sentinels(values, mrms_poller.MRMS_REFLECTIVITY_SENTINELS)

    assert masked is values or np.array_equal(masked, values)


def test_precip_values_to_inches_masks_qpe_sentinels_before_conversion() -> None:
    # NSSL QPE sentinels are defined on the raw mm values: -1 missing,
    # -3 no coverage. They must become NaN, not tiny negatives clamped to 0.
    raw_mm = np.array([[-3.0, -1.0], [0.0, 25.4]], dtype=np.float32)

    converted = mrms_poller._precip_values_to_inches(raw_mm)

    assert np.isnan(converted[0, 0])
    assert np.isnan(converted[0, 1])
    assert converted[1, 0] == np.float32(0.0)
    assert converted[1, 1] == pytest.approx(1.0)


def test_precip_values_to_inches_floors_non_sentinel_negatives() -> None:
    raw_mm = np.array([[-0.5, 12.7]], dtype=np.float32)

    converted = mrms_poller._precip_values_to_inches(raw_mm)

    assert converted[0, 0] == np.float32(0.0)
    assert converted[0, 1] == pytest.approx(0.5)
