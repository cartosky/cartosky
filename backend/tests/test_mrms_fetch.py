from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.mrms_fetch import (
    WGRIB2_UNDEFINED_SENTINEL,
    _decode_with_wgrib2,
    _pygrib_source_transform,
    discover_recent_scans_from_listing_html,
    freeze_bundle_scans,
)


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


def test_wgrib2_decoder_extracts_binary_grid_without_netcdf(monkeypatch, tmp_path: Path) -> None:
    scan_path = tmp_path / "MRMS_MergedBaseReflectivityQC_00.50_20260327-120200.grib2"
    scan_path.write_bytes(b"fake-grib")

    monkeypatch.setattr("app.services.mrms_fetch.shutil.which", lambda name: "/usr/local/bin/wgrib2")

    written_values = np.array([1.0, 2.0, 3.0, 4.0, WGRIB2_UNDEFINED_SENTINEL, 6.0], dtype=np.float32)

    def _run(cmd: list[str], *, check: bool, capture_output: bool, text: bool):
        assert check is True
        assert capture_output is True
        assert text is True
        if "-grid" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Lambert grid: (3 x 2) points", stderr="")
        if "-bin" in cmd:
            output_path = Path(cmd[-1])
            written_values.tofile(output_path)
            assert "-order" in cmd
            assert "we:ns" in cmd
            assert "-no_header" in cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected wgrib2 invocation: {cmd}")

    monkeypatch.setattr("app.services.mrms_fetch.subprocess.run", _run)

    decoded = _decode_with_wgrib2(
        scan_path,
        valid_time=datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc),
    )

    assert decoded.decoder == "wgrib2"
    assert decoded.metadata["grid_shape"] == [2, 3]
    assert decoded.metadata["grid_order"] == "we:ns"
    np.testing.assert_allclose(decoded.values[0, :], np.array([1.0, 2.0, 3.0], dtype=np.float32))
    assert np.isnan(decoded.values[1, 1])
    assert decoded.values[1, 2] == np.float32(6.0)


def test_wgrib2_decoder_falls_back_when_order_option_fails(monkeypatch, tmp_path: Path) -> None:
    scan_path = tmp_path / "MRMS_MergedBaseReflectivityQC_00.50_20260327-120200.grib2"
    scan_path.write_bytes(b"fake-grib")

    monkeypatch.setattr("app.services.mrms_fetch.shutil.which", lambda name: "/usr/local/bin/wgrib2")

    written_values = np.array([10.0, 11.0, 12.0, 13.0], dtype=np.float32)

    def _run(cmd: list[str], *, check: bool, capture_output: bool, text: bool):
        if "-grid" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="grid info (2 x 2)", stderr="")
        if "-bin" in cmd and "-order" in cmd:
            raise subprocess.CalledProcessError(8, cmd, output="", stderr="unknown option -order")
        if "-bin" in cmd:
            written_values.tofile(Path(cmd[-1]))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected wgrib2 invocation: {cmd}")

    monkeypatch.setattr("app.services.mrms_fetch.subprocess.run", _run)

    decoded = _decode_with_wgrib2(
        scan_path,
        valid_time=datetime(2026, 3, 27, 12, 2, tzinfo=timezone.utc),
    )

    assert decoded.metadata["grid_order"] == "we:sn"
    np.testing.assert_allclose(decoded.values, np.array([[10.0, 11.0], [12.0, 13.0]], dtype=np.float32))


def test_pygrib_source_transform_normalizes_to_north_up_west_east() -> None:
    class _Message:
        def latlons(self):
            lats = np.array([[30.0, 30.0, 30.0], [31.0, 31.0, 31.0]], dtype=np.float64)
            lons = np.array([[-97.0, -98.0, -99.0], [-97.0, -98.0, -99.0]], dtype=np.float64)
            return lats, lons

    values = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
    transform, oriented = _pygrib_source_transform(_Message(), values)

    np.testing.assert_allclose(oriented, np.array([[6.0, 5.0, 4.0], [3.0, 2.0, 1.0]], dtype=np.float32))
    assert round(transform.c, 6) == -99.5
    assert round(transform.f, 6) == 31.5
    assert round(transform.a, 6) == 1.0
    assert round(abs(transform.e), 6) == 1.0
