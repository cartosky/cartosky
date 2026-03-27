from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.observed_bundle_health import build_observed_bundle_health


def test_observed_bundle_health_reports_live_complete_bundle() -> None:
    now_utc = datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc)
    payload = build_observed_bundle_health(
        latest_run="20260327_1206z",
        manifest={
            "last_updated": "2026-03-27T12:06:00Z",
            "variables": {
                "reflectivity": {
                    "expected_frames": 2,
                    "available_frames": 2,
                    "frames": [
                        {"fh": 0, "valid_time": "2026-03-27T12:02:00Z"},
                        {"fh": 1, "valid_time": "2026-03-27T12:04:00Z"},
                    ],
                }
            },
        },
        source="mrms",
        now_utc=now_utc,
    )

    assert payload["freshness_state"] == "live"
    assert payload["usable"] is True
    assert payload["degraded_reason"] is None
    assert payload["latest_scan_age_minutes"] == 2
    assert payload["available_frame_count"] == 2
    assert payload["target_frame_count"] == 2


def test_observed_bundle_health_reports_delayed_incomplete_bundle() -> None:
    now_utc = datetime(2026, 3, 27, 12, 12, tzinfo=timezone.utc)
    payload = build_observed_bundle_health(
        latest_run="20260327_1212z",
        manifest={
            "last_updated": "2026-03-27T12:12:00Z",
            "variables": {
                "reflectivity": {
                    "expected_frames": 3,
                    "available_frames": 2,
                    "frames": [
                        {"fh": 0, "valid_time": "2026-03-27T12:00:00Z"},
                        {"fh": 1, "valid_time": "2026-03-27T12:04:00Z"},
                    ],
                }
            },
        },
        source="mrms",
        now_utc=now_utc,
    )

    assert payload["freshness_state"] == "delayed"
    assert payload["usable"] is True
    assert payload["degraded_reason"] == "incomplete_bundle"
    assert payload["latest_scan_age_minutes"] == 8


def test_observed_bundle_health_reports_unavailable_without_scan_time() -> None:
    now_utc = datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc)
    payload = build_observed_bundle_health(
        latest_run="20260327_1206z",
        manifest={
            "last_updated": "2026-03-27T12:06:00Z",
            "variables": {
                "reflectivity": {
                    "expected_frames": 1,
                    "available_frames": 1,
                    "frames": [{"fh": 0}],
                }
            },
        },
        source="mrms",
        now_utc=now_utc,
    )

    assert payload["freshness_state"] == "unavailable"
    assert payload["usable"] is False
    assert payload["degraded_reason"] == "missing_latest_scan_time"
