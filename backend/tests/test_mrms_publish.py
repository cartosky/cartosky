from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import mrms_publish


def _write_test_value_raster(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def _configure_small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mrms_publish, "_expected_target_shape", lambda: (2, 3))
    monkeypatch.setattr(
        mrms_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)),
    )
    monkeypatch.setattr(
        mrms_publish,
        "write_value_cog",
        lambda values, output_path, **_: _write_test_value_raster(Path(output_path), np.asarray(values, dtype=np.float32))
        or Path(output_path),
    )


def test_publish_mrms_bundle_writes_manifest_and_latest_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    frames = [
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time,
            values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
            source_url="https://example.com/scan0.grib2.gz",
            source_filename="scan0.grib2.gz",
        ),
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time + timedelta(minutes=2),
            values=np.array([[21.0, 23.0, 25.0], [27.0, 29.0, 31.0]], dtype=np.float32),
            source_url="https://example.com/scan1.grib2.gz",
            source_filename="scan1.grib2.gz",
        ),
    ]

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    assert result.run_id == "20260327_1206z"
    assert result.frame_count == 2
    assert result.published_run_dir.is_dir()
    latest_payload = json.loads((tmp_path / "published" / "mrms" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260327_1206z"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["source"] == "mrms"
    assert manifest["metadata"]["time_axis_mode"] == "observed"
    assert manifest["metadata"]["latest_scan_valid_time"] == "2026-03-27T12:02:00Z"
    assert manifest["metadata"]["target_frame_count"] == 2
    assert manifest["metadata"]["available_frame_count"] == 2
    reflectivity = manifest["variables"]["reflectivity"]
    assert reflectivity["expected_frames"] == 2
    assert reflectivity["available_frames"] == 2
    assert reflectivity["frames"] == [
        {"fh": 0, "valid_time": "2026-03-27T12:00:00Z"},
        {"fh": 1, "valid_time": "2026-03-27T12:02:00Z"},
    ]

    sidecar = json.loads((result.published_run_dir / "reflectivity" / "fh001.json").read_text())
    assert sidecar["valid_time"] == "2026-03-27T12:02:00Z"
    assert sidecar["source_filename"] == "scan1.grib2.gz"


def test_publish_mrms_bundle_warps_native_grid_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)
    captured: dict[str, tuple[int, int]] = {}

    def _warp(values, *args, **kwargs):
        captured["input_shape"] = np.asarray(values).shape
        return np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)

    monkeypatch.setattr(mrms_publish, "warp_to_target_grid", _warp)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.ones((4, 5), dtype=np.float32),
        source_crs="EPSG:4326",
        source_transform=from_origin(-130.0, 55.0, 0.01, 0.01),
    )

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    assert result.frame_count == 1
    assert captured["input_shape"] == (4, 5)


def test_publish_mrms_bundle_smooths_display_only_not_value_grid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)
    captured: dict[str, np.ndarray] = {}

    monkeypatch.setattr(
        mrms_publish,
        "_display_values_for_colorize",
        lambda values, **_: np.asarray(values, dtype=np.float32) + np.float32(1.5),
    )

    def _float_to_rgba(values, *_args, **_kwargs):
        captured["rgba_input"] = np.asarray(values, dtype=np.float32).copy()
        rgba = np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8)
        return rgba, {"legend_title": "MRMS Reflectivity (dBZ)"}

    def _write_value(values, output_path, **_kwargs):
        captured["value_input"] = np.asarray(values, dtype=np.float32).copy()
        return _write_test_value_raster(Path(output_path), captured["value_input"]) or Path(output_path)

    monkeypatch.setattr(mrms_publish, "float_to_rgba", _float_to_rgba)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _write_value)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )

    mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    np.testing.assert_allclose(
        captured["rgba_input"],
        np.array([[11.5, 13.5, 15.5], [17.5, 19.5, 21.5]], dtype=np.float32),
    )
    np.testing.assert_allclose(
        captured["value_input"],
        np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )


def test_failed_publish_preserves_previous_latest_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )
    first = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )
    assert first.run_id == "20260327_1206z"

    def _fail_colorize(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mrms_publish, "float_to_rgba", _fail_colorize)

    with pytest.raises(RuntimeError, match="boom"):
        mrms_publish.publish_mrms_bundle(
            data_root=tmp_path,
            frames=[frame],
            publish_time=datetime(2026, 3, 27, 12, 8, tzinfo=timezone.utc),
        )

    latest_payload = json.loads((tmp_path / "published" / "mrms" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == "20260327_1206z"
    assert not (tmp_path / "published" / "mrms" / "20260327_1208z").exists()


def test_publish_mrms_bundle_reuses_prior_frames_and_trims_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    initial_frames = [
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time + timedelta(minutes=offset),
            values=np.full((2, 3), 10.0 + offset, dtype=np.float32),
            source_filename=f"scan-{offset}.grib2.gz",
        )
        for offset in (0, 5, 10)
    ]
    first = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=initial_frames,
        publish_time=datetime(2026, 3, 27, 12, 12, tzinfo=timezone.utc),
        target_frame_count=3,
        expected_frame_count=3,
    )

    previous_run_id, previous_frames = mrms_publish.load_latest_published_mrms_frames(tmp_path)
    assert previous_run_id == first.run_id
    assert len(previous_frames) == 3

    second = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[
            mrms_publish.MRMSBundleFrame(
                valid_time=base_time + timedelta(minutes=15),
                values=np.full((2, 3), 25.0, dtype=np.float32),
                source_filename="scan-15.grib2.gz",
            )
        ],
        previous_frames=previous_frames,
        publish_time=datetime(2026, 3, 27, 12, 16, tzinfo=timezone.utc),
        target_frame_count=3,
        expected_frame_count=3,
    )

    manifest = json.loads(second.manifest_path.read_text())
    reflectivity = manifest["variables"]["reflectivity"]
    assert reflectivity["frames"] == [
        {"fh": 0, "valid_time": "2026-03-27T12:05:00Z"},
        {"fh": 1, "valid_time": "2026-03-27T12:10:00Z"},
        {"fh": 2, "valid_time": "2026-03-27T12:15:00Z"},
    ]

    reused_sidecar = json.loads((second.published_run_dir / "reflectivity" / "fh000.json").read_text())
    assert reused_sidecar["run"] == second.run_id
    assert reused_sidecar["fh"] == 0
    assert reused_sidecar["valid_time"] == "2026-03-27T12:05:00Z"

    new_sidecar = json.loads((second.published_run_dir / "reflectivity" / "fh002.json").read_text())
    assert new_sidecar["valid_time"] == "2026-03-27T12:15:00Z"


def test_publish_mrms_bundle_publishes_grid_artifacts_from_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)
    monkeypatch.setattr(mrms_publish, "grid_build_enabled", lambda: True)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    assert result.run_id == "20260327_1206z"
    grid_dir = result.published_run_dir / "reflectivity" / "grid"
    assert (grid_dir / "fh000.l0.u16.bin").is_file()
    assert (grid_dir / "fh000.l0.meta.json").is_file()
    manifest = json.loads((grid_dir / "manifest.json").read_text())
    assert manifest["subtype"] == "grid"
    assert manifest["lods"][0]["frames"] == [
        {"fh": 0, "file": "fh000.l0.u16.bin", "valid_time": "2026-03-27T12:00:00Z"}
    ]


def test_publish_mrms_bundle_does_not_write_rgba_cogs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    frame = mrms_publish.MRMSBundleFrame(
        valid_time=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        values=np.array([[10.0, 12.0, 14.0], [16.0, 18.0, 20.0]], dtype=np.float32),
    )

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    assert not any(result.published_run_dir.rglob("*.rgba.cog.tif"))


# ---------------------------------------------------------------------------
# compose_mrms_radar_ptype tests
# ---------------------------------------------------------------------------

def test_compose_mrms_radar_ptype_rain_produces_correct_indices() -> None:
    """Rain (flag=1) at various reflectivities maps to rain palette offsets."""
    refl = np.array([[10.0, 35.0, 70.0]], dtype=np.float32)
    flags = np.array([[1.0, 1.0, 1.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    # rain: offset=0, count=20
    # 10/70 * 19 ≈ 2.71 → round(2.71) = 3 → 0 + 3 = 3
    assert indexed[0, 0] == 3.0
    # 35/70 * 19 = 9.5 → round(9.5) = 10 → 0 + 10 = 10 (python rounds to even, but np.rint(9.5)=10.0)
    assert indexed[0, 1] == 10.0
    # 70/70 * 19 = 19.0 → 0 + 19 = 19
    assert indexed[0, 2] == 19.0


def test_compose_mrms_radar_ptype_snow_produces_correct_indices() -> None:
    """Snow (flag=3) maps to snow palette offsets."""
    refl = np.array([[15.0, 50.0]], dtype=np.float32)
    flags = np.array([[3.0, 3.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    # snow: offset=20, count=16
    # 15/70 * 15 ≈ 3.21 → round = 3 → 20 + 3 = 23
    assert indexed[0, 0] == 23.0
    # 50/70 * 15 ≈ 10.71 → round = 11 → 20 + 11 = 31
    assert indexed[0, 1] == 31.0


def test_compose_mrms_radar_ptype_frzr_produces_correct_indices() -> None:
    """Freezing rain (flag=7) maps to frzr palette offsets."""
    refl = np.array([[20.0]], dtype=np.float32)
    flags = np.array([[7.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    # frzr: offset=52, count=16
    # 20/70 * 15 ≈ 4.29 → round = 4 → 52 + 4 = 56
    assert indexed[0, 0] == 56.0


def test_compose_mrms_radar_ptype_convective_rain_maps_to_rain() -> None:
    """Convective rain (flag=6) maps to rain palette like warm stratiform."""
    refl = np.array([[35.0]], dtype=np.float32)
    flags_warm = np.array([[1.0]], dtype=np.float32)
    flags_conv = np.array([[6.0]], dtype=np.float32)

    indexed_warm = mrms_publish.compose_mrms_radar_ptype(refl, flags_warm)
    indexed_conv = mrms_publish.compose_mrms_radar_ptype(refl, flags_conv)

    # Both should produce the same rain index
    assert indexed_warm[0, 0] == indexed_conv[0, 0]


def test_compose_mrms_radar_ptype_dry_snow_maps_to_snow() -> None:
    """Dry/cold snow (flag=10) maps to snow palette like regular snow."""
    refl = np.array([[25.0]], dtype=np.float32)
    flags_snow = np.array([[3.0]], dtype=np.float32)
    flags_dry = np.array([[10.0]], dtype=np.float32)

    indexed_snow = mrms_publish.compose_mrms_radar_ptype(refl, flags_snow)
    indexed_dry = mrms_publish.compose_mrms_radar_ptype(refl, flags_dry)

    assert indexed_snow[0, 0] == indexed_dry[0, 0]


def test_compose_mrms_radar_ptype_no_precip_is_nan() -> None:
    """No-precipitation flag (0) produces NaN regardless of reflectivity."""
    refl = np.array([[40.0, 60.0]], dtype=np.float32)
    flags = np.array([[0.0, 0.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    assert np.isnan(indexed[0, 0])
    assert np.isnan(indexed[0, 1])


def test_compose_mrms_radar_ptype_no_coverage_is_nan() -> None:
    """No-coverage flag (-3) produces NaN."""
    refl = np.array([[40.0]], dtype=np.float32)
    flags = np.array([[-3.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    assert np.isnan(indexed[0, 0])


def test_compose_mrms_radar_ptype_low_reflectivity_is_nan() -> None:
    """Reflectivity below min_visible_dbz (10) produces NaN even with valid ptype."""
    refl = np.array([[5.0, 9.9]], dtype=np.float32)
    flags = np.array([[1.0, 3.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    assert np.isnan(indexed[0, 0])
    assert np.isnan(indexed[0, 1])


def test_compose_mrms_radar_ptype_nan_reflectivity_is_nan() -> None:
    """NaN reflectivity produces NaN output."""
    refl = np.array([[np.nan]], dtype=np.float32)
    flags = np.array([[1.0]], dtype=np.float32)

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    assert np.isnan(indexed[0, 0])


def test_compose_mrms_radar_ptype_shape_mismatch_raises() -> None:
    """Mismatched reflectivity and PrecipFlag shapes raise ValueError."""
    refl = np.array([[10.0, 20.0]], dtype=np.float32)
    flags = np.array([[1.0]], dtype=np.float32)

    with pytest.raises(ValueError, match="shape mismatch"):
        mrms_publish.compose_mrms_radar_ptype(refl, flags)


def test_compose_mrms_radar_ptype_unknown_flag_is_nan() -> None:
    """Unknown/unmapped PrecipFlag values produce NaN."""
    refl = np.array([[40.0]], dtype=np.float32)
    flags = np.array([[99.0]], dtype=np.float32)  # not in the mapping

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    assert np.isnan(indexed[0, 0])


def test_compose_mrms_radar_ptype_mixed_ptypes() -> None:
    """Mixed ptype flags in one grid produce correct per-cell indices."""
    refl = np.array([[30.0, 30.0, 30.0, 30.0]], dtype=np.float32)
    flags = np.array([[1.0, 3.0, 7.0, 0.0]], dtype=np.float32)  # rain, snow, frzr, no-precip

    indexed = mrms_publish.compose_mrms_radar_ptype(refl, flags)

    # rain offset=0: 30/70*19 ≈ 8.14 → 8 → 0+8 = 8
    assert indexed[0, 0] == 8.0
    # snow offset=20: 30/70*15 ≈ 6.43 → 6 → 20+6 = 26
    assert indexed[0, 1] == 26.0
    # frzr offset=52: 30/70*15 ≈ 6.43 → 6 → 52+6 = 58
    assert indexed[0, 2] == 58.0
    # no-precip → NaN
    assert np.isnan(indexed[0, 3])


# ---------------------------------------------------------------------------
# Dual-variable publish test (reflectivity + mrms_radar_ptype)
# ---------------------------------------------------------------------------

def test_publish_mrms_bundle_writes_mrms_radar_ptype_when_precip_flag_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    frames = [
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time,
            values=np.array([[30.0, 40.0, 50.0], [60.0, 20.0, 15.0]], dtype=np.float32),
            source_url="https://example.com/scan0.grib2.gz",
            source_filename="scan0.grib2.gz",
            precip_flag_values=np.array([[1.0, 3.0, 7.0], [6.0, 10.0, 0.0]], dtype=np.float32),
        ),
    ]

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    # Reflectivity artifacts exist
    assert (result.published_run_dir / "reflectivity" / "fh000.val.cog.tif").is_file()
    assert (result.published_run_dir / "reflectivity" / "fh000.json").is_file()

    # mrms_radar_ptype artifacts exist
    assert (result.published_run_dir / "mrms_radar_ptype" / "fh000.val.cog.tif").is_file()
    assert (result.published_run_dir / "mrms_radar_ptype" / "fh000.json").is_file()

    # Check manifest includes both variables
    manifest = json.loads(result.manifest_path.read_text())
    assert "reflectivity" in manifest["variables"]
    assert "mrms_radar_ptype" in manifest["variables"]

    ptype_var = manifest["variables"]["mrms_radar_ptype"]
    assert ptype_var["available_frames"] == 1
    assert ptype_var["frames"] == [
        {"fh": 0, "valid_time": "2026-03-27T12:00:00Z"},
    ]

    # Check mrms_radar_ptype sidecar
    ptype_sidecar = json.loads((result.published_run_dir / "mrms_radar_ptype" / "fh000.json").read_text())
    assert ptype_sidecar["var"] == "mrms_radar_ptype"
    assert ptype_sidecar["valid_time"] == "2026-03-27T12:00:00Z"


def test_publish_mrms_bundle_omits_mrms_radar_ptype_when_no_precip_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_small_grid(monkeypatch)

    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    frames = [
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time,
            values=np.array([[30.0, 40.0, 50.0], [60.0, 20.0, 15.0]], dtype=np.float32),
            # No precip_flag_values
        ),
    ]

    result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
    )

    # Reflectivity artifacts exist
    assert (result.published_run_dir / "reflectivity" / "fh000.val.cog.tif").is_file()

    # No mrms_radar_ptype directory
    assert not (result.published_run_dir / "mrms_radar_ptype").exists()

    # Manifest should NOT include mrms_radar_ptype
    manifest = json.loads(result.manifest_path.read_text())
    assert "reflectivity" in manifest["variables"]
    assert "mrms_radar_ptype" not in manifest["variables"]


def test_publish_mrms_bundle_reuse_only_cycle_preserves_mrms_radar_ptype(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the bug: when all frames are reused (no new scans decoded),
    mrms_radar_ptype must still appear in the new bundle's manifest."""
    _configure_small_grid(monkeypatch)

    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)

    # First publish: fresh frame with precip_flag_values → both variables written
    first_frames = [
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time,
            values=np.array([[30.0, 40.0, 50.0], [60.0, 20.0, 15.0]], dtype=np.float32),
            source_filename="scan0.grib2.gz",
            precip_flag_values=np.array([[1.0, 3.0, 7.0], [6.0, 10.0, 0.0]], dtype=np.float32),
        ),
        mrms_publish.MRMSBundleFrame(
            valid_time=base_time + timedelta(minutes=5),
            values=np.array([[25.0, 35.0, 45.0], [55.0, 15.0, 10.0]], dtype=np.float32),
            source_filename="scan1.grib2.gz",
            precip_flag_values=np.array([[3.0, 1.0, 7.0], [10.0, 6.0, 0.0]], dtype=np.float32),
        ),
    ]

    first_result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=first_frames,
        publish_time=datetime(2026, 3, 27, 12, 6, tzinfo=timezone.utc),
        target_frame_count=2,
        expected_frame_count=2,
    )

    # Verify first publish has both variables
    first_manifest = json.loads(first_result.manifest_path.read_text())
    assert "mrms_radar_ptype" in first_manifest["variables"]
    assert first_manifest["variables"]["mrms_radar_ptype"]["available_frames"] == 2

    # Load previous frames (simulates what the poller does)
    previous_run_id, previous_frames = mrms_publish.load_latest_published_mrms_frames(tmp_path)
    assert previous_run_id == first_result.run_id
    assert len(previous_frames) == 2
    # Verify ptype paths were loaded
    assert all(f.ptype_value_path is not None for f in previous_frames)
    assert all(f.ptype_sidecar is not None for f in previous_frames)

    # Second publish: ALL frames reused (no new MRMSBundleFrame), zero fresh decodes
    second_result = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[],  # No new frames!
        previous_frames=previous_frames,
        publish_time=datetime(2026, 3, 27, 12, 8, tzinfo=timezone.utc),
        target_frame_count=2,
        expected_frame_count=2,
    )

    # THE BUG: mrms_radar_ptype must still be in the new manifest
    second_manifest = json.loads(second_result.manifest_path.read_text())
    assert "reflectivity" in second_manifest["variables"]
    assert "mrms_radar_ptype" in second_manifest["variables"], (
        "mrms_radar_ptype disappeared from manifest on reuse-only cycle"
    )
    assert second_manifest["variables"]["mrms_radar_ptype"]["available_frames"] == 2

    # Verify the actual artifacts exist
    assert (second_result.published_run_dir / "mrms_radar_ptype" / "fh000.val.cog.tif").is_file()
    assert (second_result.published_run_dir / "mrms_radar_ptype" / "fh001.val.cog.tif").is_file()
    assert (second_result.published_run_dir / "mrms_radar_ptype" / "fh000.json").is_file()
    assert (second_result.published_run_dir / "mrms_radar_ptype" / "fh001.json").is_file()
