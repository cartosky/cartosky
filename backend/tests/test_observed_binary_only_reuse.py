"""Binary-only rolling-window reuse for the observed publishers.

Regression tests for the 2026-07-14 production incident: after the value-COG
cutover, `load_latest_published_*_frames` still required `fh*.val.cog.tif`,
so every binary-only frame silently vanished from the next cycle's
`previous_frames` — current_analysis's 45-frame window collapsed to the
poller's fetch window, GOES was degrading toward the same cliff, and MRMS's
finalize-path supplementals published sidecar-only (no substrate at all,
"missing artifacts" in the admin checker).

The contract pinned here: a frame whose only substrate is the grid binary is
(a) admitted by the loader, (b) reused into the next run with its grid
artifacts hardlinked and NO value COG fabricated, and (c) for the MRMS
finalize path, written with inline grid artifacts instead of the retired
COG-read rebuild.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import goes_publish, mrms_publish, rtma_ru_publish
from app.services.grid import resolved_grid_dir_for_run_root


def _fail_if_called(name: str):
    def _spy(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")

    return _spy


def _grid_meta(run_dir: Path, var_id: str, fh: int) -> Path:
    return resolved_grid_dir_for_run_root(run_dir, var_id) / f"fh{fh:03d}.l0.meta.json"


# ── current_analysis ─────────────────────────────────────────────────

_CA_TIME = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _ca_binary_only_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTOSKY_COG_SAMPLING_MODELS", raising=False)
    monkeypatch.setattr(rtma_ru_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(
        rtma_ru_publish,
        "float_to_rgba",
        lambda values, color_map_id, meta_var_key=None: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "min": 0.0, "max": 100.0},
        ),
    )


def test_current_analysis_binary_only_frames_survive_load_and_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ca_binary_only_harness(monkeypatch)
    values = np.array([[70.0, 71.0], [72.0, 73.0]], dtype=np.float32)
    frame = rtma_ru_publish.CurrentAnalysisBundleFrame(
        valid_time=_CA_TIME,
        values_by_var={"tmp2m": values},
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
    )

    first = rtma_ru_publish.publish_current_analysis_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=_CA_TIME + timedelta(minutes=5),
    )
    assert first.frame_count == 1
    assert not (first.published_run_dir / "tmp2m" / "fh000.val.cog.tif").exists()
    assert _grid_meta(first.published_run_dir, "tmp2m", 0).is_file()

    # The loader must admit the binary-only frame (the incident's failure
    # point: COG-gated admission returned an empty window here).
    run_id, previous_frames = rtma_ru_publish.load_latest_published_current_analysis_frames(tmp_path)
    assert run_id == first.run_id
    assert len(previous_frames) == 1
    assert previous_frames[0].value_paths == {}
    assert set(previous_frames[0].sidecar_paths) == {"tmp2m"}

    # Reuse into the next run: grid artifacts hardlinked, still no COG.
    second = rtma_ru_publish.publish_current_analysis_bundle(
        data_root=tmp_path,
        frames=[],
        previous_frames=previous_frames,
        publish_time=_CA_TIME + timedelta(minutes=20),
    )
    assert second.frame_count == 1
    assert (second.published_run_dir / "tmp2m" / "fh000.json").is_file()
    assert _grid_meta(second.published_run_dir, "tmp2m", 0).is_file()
    assert not (second.published_run_dir / "tmp2m" / "fh000.val.cog.tif").exists()
    manifest = json.loads(second.manifest_path.read_text())
    assert manifest["variables"]["tmp2m"]["available_frames"] == 1


# ── GOES-East ────────────────────────────────────────────────────────

_GOES_TIME = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)


def test_goes_binary_only_frames_survive_load_and_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CARTOSKY_COG_SAMPLING_MODELS", raising=False)
    monkeypatch.setattr(goes_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(
        goes_publish,
        "float_to_rgba",
        lambda values, color_map_id, meta_var_key=None: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "min": 0.0, "max": 1.0},
        ),
    )
    frame = goes_publish.GOESBundleFrame(
        valid_time=_GOES_TIME,
        slot_time=_GOES_TIME,
        values=np.array([[220.0, 240.0], [260.0, 280.0]], dtype=np.float32),
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
    )

    first = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=_GOES_TIME + timedelta(minutes=5),
    )
    assert first.frame_count == 1
    assert not (first.published_run_dir / "ir13" / "fh000.val.cog.tif").exists()
    assert _grid_meta(first.published_run_dir, "ir13", 0).is_file()

    run_id, previous_frames = goes_publish.load_latest_published_goes_frames(tmp_path)
    assert run_id == first.run_id
    assert len(previous_frames) == 1
    assert previous_frames[0].value_path is None
    assert previous_frames[0].sidecar_path is not None

    second = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[],
        previous_frames=previous_frames,
        publish_time=_GOES_TIME + timedelta(minutes=15),
    )
    assert second.frame_count == 1
    assert (second.published_run_dir / "ir13" / "fh000.json").is_file()
    assert _grid_meta(second.published_run_dir, "ir13", 0).is_file()
    assert not (second.published_run_dir / "ir13" / "fh000.val.cog.tif").exists()
    manifest = json.loads(second.manifest_path.read_text())
    assert manifest["variables"]["ir13"]["available_frames"] == 1


# ── MRMS ─────────────────────────────────────────────────────────────

_MRMS_TIME = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)


def _mrms_binary_only_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTOSKY_COG_SAMPLING_MODELS", raising=False)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(
        mrms_publish,
        "_warp_frame_to_target_grid",
        lambda values, *, frame, **kwargs: np.asarray(values, dtype=np.float32),
    )
    monkeypatch.setattr(mrms_publish, "_target_grid_transform", lambda: from_origin(0.0, 2.0, 1.0, 1.0))
    monkeypatch.setattr(
        mrms_publish,
        "colorize_metadata",
        lambda values, color_map_id, meta_var_key=None: {"kind": "discrete", "min": 0.0, "max": 70.0},
    )


def test_mrms_binary_only_frames_survive_load_and_reuse_including_ptype(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mrms_binary_only_harness(monkeypatch)
    refl = np.array([[5.0, 15.0], [30.0, 50.0]], dtype=np.float32)
    flags = np.ones_like(refl)  # rain everywhere -> ptype composite passes
    frame = mrms_publish.MRMSBundleFrame(
        valid_time=_MRMS_TIME,
        values=refl,
        precip_flag_values=flags,
    )

    first = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[frame],
        publish_time=_MRMS_TIME + timedelta(minutes=4),
    )
    assert first.frame_count == 1
    for var in ("reflectivity", "mrms_radar_ptype"):
        assert not (first.published_run_dir / var / "fh000.val.cog.tif").exists()
        assert _grid_meta(first.published_run_dir, var, 0).is_file()

    run_id, previous_frames = mrms_publish.load_latest_published_mrms_frames(tmp_path)
    assert run_id == first.run_id
    assert len(previous_frames) == 1
    assert previous_frames[0].value_path is None
    assert previous_frames[0].sidecar_path is not None
    assert previous_frames[0].ptype_value_path is None
    assert previous_frames[0].ptype_sidecar is not None

    second = mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=[],
        previous_frames=previous_frames,
        publish_time=_MRMS_TIME + timedelta(minutes=10),
    )
    assert second.frame_count == 1
    for var in ("reflectivity", "mrms_radar_ptype"):
        assert (second.published_run_dir / var / "fh000.json").is_file()
        assert _grid_meta(second.published_run_dir, var, 0).is_file()
        assert not (second.published_run_dir / var / "fh000.val.cog.tif").exists()
    manifest = json.loads(second.manifest_path.read_text())
    assert manifest["variables"]["reflectivity"]["available_frames"] == 1
    assert manifest["variables"]["mrms_radar_ptype"]["available_frames"] == 1


def test_mrms_finalize_supplementals_write_inline_grid_artifacts_binary_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The incident's second failure: finalize wrote supplementals with
    # build_grid_artifacts=False and relied on the COG-read rebuild — which
    # cannot run once the model is binary-only, publishing sidecar-only
    # (unsampleable) frames. Grid artifacts must now be written inline.
    _mrms_binary_only_harness(monkeypatch)

    run_id = "20260714_1804z"
    (tmp_path / "published" / "mrms" / run_id / "reflectivity").mkdir(parents=True)
    manifest_path = tmp_path / "manifests" / "mrms" / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"last_updated": "2026-07-14T18:04:00Z", "variables": {}}))

    good = np.array([[0.1, 0.2], [0.4, 0.6]], dtype=np.float32)
    frame = mrms_publish.MRMSSupplementalFrame(valid_time=_MRMS_TIME, values=good)

    mrms_publish.finalize_mrms_published_run(
        data_root=tmp_path,
        run_id=run_id,
        supplemental_variable_frames={"mrms_recent_precip_6h": [frame]},
    )

    var_dir = tmp_path / "published" / "mrms" / run_id / "mrms_recent_precip_6h"
    run_dir = tmp_path / "published" / "mrms" / run_id
    assert (var_dir / "fh000.json").is_file()
    assert not (var_dir / "fh000.val.cog.tif").exists()
    # Grid artifacts written inline — the frame is samplable.
    assert _grid_meta(run_dir, "mrms_recent_precip_6h", 0).is_file()
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["variables"]["mrms_recent_precip_6h"]
    assert entry["available_frames"] == 1


def test_mrms_copy_published_variable_artifacts_carries_grid_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Reused supplementals (finalize's reused_supplemental_from_run_id path)
    # must carry their grid artifacts: the COG-read rebuild that used to
    # recreate them is gone, and binary-only frames have no COG anyway.
    _mrms_binary_only_harness(monkeypatch)

    source_run = "20260714_1704z"
    good = np.array([[0.1, 0.2], [0.4, 0.6]], dtype=np.float32)
    mrms_publish._write_mrms_supplemental_frame_to_run_root(
        run_root=tmp_path / "published" / "mrms" / source_run,
        run_id=source_run,
        var_id="mrms_recent_precip_6h",
        forecast_hour=0,
        frame=mrms_publish.MRMSSupplementalFrame(valid_time=_MRMS_TIME, values=good),
        build_grid_artifacts=True,
    )
    assert _grid_meta(tmp_path / "published" / "mrms" / source_run, "mrms_recent_precip_6h", 0).is_file()

    target_run = "20260714_1804z"
    (tmp_path / "published" / "mrms" / target_run).mkdir(parents=True)
    mrms_publish._copy_published_variable_artifacts(
        data_root=tmp_path,
        source_run_id=source_run,
        target_run_id=target_run,
        var_id="mrms_recent_precip_6h",
    )

    target_run_dir = tmp_path / "published" / "mrms" / target_run
    assert (target_run_dir / "mrms_recent_precip_6h" / "fh000.json").is_file()
    assert _grid_meta(target_run_dir, "mrms_recent_precip_6h", 0).is_file()
