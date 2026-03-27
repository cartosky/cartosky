from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.mrms_fetch import discover_recent_scans_from_listing_html, freeze_bundle_scans


def test_discover_recent_scans_parses_official_listing_filenames() -> None:
    html = """
    <html><body>
      <a href="MRMS_MergedBaseReflectivityQC_00.50_20260327-120000.grib2.gz">old</a>
      <a href="MRMS_MergedBaseReflectivityQC_00.50_20260327-120200.grib2.gz">new</a>
    </body></html>
    """

    scans = discover_recent_scans_from_listing_html(html, base_url="https://mrms.ncep.noaa.gov/2D/MergedBaseReflectivityQC/")
    assert [scan.valid_time.isoformat() for scan in scans] == [
        "2026-03-27T12:02:00+00:00",
        "2026-03-27T12:00:00+00:00",
    ]
    assert scans[0].url.endswith("MRMS_MergedBaseReflectivityQC_00.50_20260327-120200.grib2.gz")


def test_freeze_bundle_scans_returns_oldest_to_newest_window() -> None:
    scans = discover_recent_scans_from_listing_html(
        """
        <a href="MRMS_MergedBaseReflectivityQC_00.50_20260327-115800.grib2.gz">1</a>
        <a href="MRMS_MergedBaseReflectivityQC_00.50_20260327-120000.grib2.gz">2</a>
        <a href="MRMS_MergedBaseReflectivityQC_00.50_20260327-120200.grib2.gz">3</a>
        """,
        base_url="https://mrms.ncep.noaa.gov/2D/MergedBaseReflectivityQC/",
    )

    frozen = freeze_bundle_scans(
        scans,
        max_frames=2,
        newest_valid_time=datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc),
    )
    assert [scan.valid_time.isoformat() for scan in frozen] == [
        "2026-03-27T12:00:00+00:00",
        "2026-03-27T12:02:00+00:00",
    ]
