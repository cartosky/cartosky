from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.goes_fetch import (
    GOESFetchError,
    GOESScanRef,
    discover_recent_scans_s3,
    download_scan,
    freeze_bundle_scans,
    parse_goes_filename,
)


class _Paginator:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages


class _S3Client:
    def __init__(self, pages):
        self.paginator = _Paginator(pages)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self.paginator


def test_parse_goes_filename_extracts_scan_times() -> None:
    parsed = parse_goes_filename("OR_ABI-L2-CMIPC-M6C13_G19_s20261411201175_e20261411203560_c20261411204046.nc")
    assert parsed is not None
    assert parsed["product"] == "ABI-L2-CMIPC"
    assert parsed["sector"] == "C"
    assert parsed["band"] == 13
    assert parsed["satellite"] == "goes19"
    assert parsed["scan_start_time"] == datetime(2026, 5, 21, 12, 1, 17, 500000, tzinfo=timezone.utc)
    assert parsed["scan_end_time"] == datetime(2026, 5, 21, 12, 3, 56, tzinfo=timezone.utc)


def test_discover_recent_scans_s3_applies_age_and_size_gates() -> None:
    now = datetime(2026, 5, 21, 12, 10, tzinfo=timezone.utc)
    pages = [
        {
            "Contents": [
                {
                    "Key": "ABI-L2-CMIPC/2026/141/12/OR_ABI-L2-CMIPC-M6C13_G19_s20261411201175_e20261411203560_c20261411204046.nc",
                    "Size": 4_000_000,
                    "LastModified": now - timedelta(minutes=3),
                    "ETag": '"abc"',
                },
                {
                    "Key": "ABI-L2-CMIPC/2026/141/12/OR_ABI-L2-CMIPC-M6C13_G19_s20261411216175_e20261411218560_c20261411219046.nc",
                    "Size": 4_000_000,
                    "LastModified": now - timedelta(seconds=30),
                },
                {
                    "Key": "ABI-L2-CMIPC/2026/141/12/OR_ABI-L2-CMIPC-M6C13_G19_s20261411231175_e20261411233560_c20261411234046.nc",
                    "Size": 10,
                    "LastModified": now - timedelta(minutes=3),
                },
            ]
        }
    ]
    client = _S3Client(pages)
    refs = discover_recent_scans_s3(
        s3_client=client,
        bucket="noaa-goes19",
        product="ABI-L2-CMIPC",
        sector="C",
        band=13,
        satellite="goes19",
        now_utc=now,
        lookback_hours=1,
        object_min_age_seconds=120,
        min_object_bytes=1_000_000,
    )
    assert len(refs) == 1
    assert refs[0].size_bytes == 4_000_000
    assert refs[0].slot_time == datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)


def test_discover_recent_scans_s3_respects_slot_cadence_minutes() -> None:
    now = datetime(2026, 5, 21, 12, 10, tzinfo=timezone.utc)
    pages = [
        {
            "Contents": [
                {
                    "Key": "ABI-L2-CMIPC/2026/141/12/OR_ABI-L2-CMIPC-M6C13_G19_s20261411206175_e20261411208560_c20261411209046.nc",
                    "Size": 4_000_000,
                    "LastModified": now - timedelta(minutes=3),
                    "ETag": '"abc"',
                },
            ]
        }
    ]
    client = _S3Client(pages)
    refs = discover_recent_scans_s3(
        s3_client=client,
        bucket="noaa-goes19",
        product="ABI-L2-CMIPC",
        sector="C",
        band=13,
        satellite="goes19",
        now_utc=now,
        lookback_hours=1,
        object_min_age_seconds=120,
        min_object_bytes=1_000_000,
        slot_cadence_minutes=5,
    )
    assert len(refs) == 1
    assert refs[0].scan_start_time == datetime(2026, 5, 21, 12, 6, 17, 500000, tzinfo=timezone.utc)
    assert refs[0].slot_time == datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc)


def test_freeze_bundle_scans_selects_latest_per_5_minute_slot() -> None:
    base = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

    def ref(scan_minute: int, created_offset: int, suffix: str) -> GOESScanRef:
        start = base + timedelta(minutes=scan_minute, seconds=75)
        slot = start.replace(minute=(start.minute // 5) * 5, second=0, microsecond=0)
        return GOESScanRef(
            bucket="b",
            key=f"k-{suffix}",
            filename=f"f-{suffix}.nc",
            product="ABI-L2-CMIPC",
            sector="C",
            band=13,
            satellite="goes19",
            scan_start_time=start,
            scan_end_time=start + timedelta(minutes=2),
            created_time=start + timedelta(seconds=created_offset),
            slot_time=slot,
            size_bytes=1,
            last_modified=start + timedelta(seconds=created_offset),
        )

    frozen = freeze_bundle_scans(
        [ref(0, 10, "old"), ref(1, 20, "new"), ref(5, 10, "next"), ref(10, 10, "last")],
        max_frames=2,
        frame_cadence_minutes=5,
    )
    assert [item.key for item in frozen] == ["k-next", "k-last"]


def test_freeze_bundle_scans_selects_latest_per_15_minute_slot() -> None:
    base = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

    def ref(slot_minute: int, created_offset: int, suffix: str) -> GOESScanRef:
        start = base + timedelta(minutes=slot_minute, seconds=75)
        return GOESScanRef(
            bucket="b",
            key=f"k-{suffix}",
            filename=f"f-{suffix}.nc",
            product="ABI-L2-CMIPC",
            sector="C",
            band=13,
            satellite="goes19",
            scan_start_time=start,
            scan_end_time=start + timedelta(minutes=2),
            created_time=start + timedelta(seconds=created_offset),
            slot_time=base + timedelta(minutes=slot_minute),
            size_bytes=1,
            last_modified=start + timedelta(seconds=created_offset),
        )

    frozen = freeze_bundle_scans(
        [ref(0, 10, "old"), ref(0, 20, "new"), ref(15, 10, "next"), ref(30, 10, "last")],
        max_frames=2,
        frame_cadence_minutes=15,
    )
    assert [item.key for item in frozen] == ["k-next", "k-last"]


def test_download_scan_rejects_size_mismatch(tmp_path: Path) -> None:
    class Client:
        def download_file(self, bucket, key, filename):
            Path(filename).write_bytes(b"short")

    scan = GOESScanRef(
        bucket="b",
        key="k",
        filename="scan.nc",
        product="ABI-L2-CMIPC",
        sector="C",
        band=13,
        satellite="goes19",
        scan_start_time=datetime(2026, 5, 21, 12, tzinfo=timezone.utc),
        scan_end_time=datetime(2026, 5, 21, 12, 3, tzinfo=timezone.utc),
        created_time=datetime(2026, 5, 21, 12, 4, tzinfo=timezone.utc),
        slot_time=datetime(2026, 5, 21, 12, tzinfo=timezone.utc),
        size_bytes=10,
        last_modified=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
    )
    with pytest.raises(GOESFetchError, match="size mismatch"):
        download_scan(scan, dest_dir=tmp_path, s3_client=Client())
