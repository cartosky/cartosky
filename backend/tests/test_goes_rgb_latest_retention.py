from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.models.goes_east import GOES_EAST_MODEL_ID, GOES_EAST_RGB_LATEST_FILENAME
from app.services import goes_poller, goes_publish, goes_rgb_publish
from app.services.publish_utils import enforce_run_artifact_retention, write_json_atomic


def _configure_band_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "goes-east")
    monkeypatch.setattr(goes_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        goes_publish,
        "write_value_cog",
        lambda values, output_path, **_: Path(output_path).write_bytes(b"cog") or Path(output_path),
    )
    monkeypatch.setattr(
        goes_publish,
        "float_to_rgba",
        lambda values, *_args, **_kwargs: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {
                "kind": "continuous",
                "units": "K",
                "min": float(np.nanmin(values)),
                "max": float(np.nanmax(values)),
                "display_name": "Clean IR",
            },
        ),
    )


def _publish_ir_and_rgb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    _configure_band_publish(monkeypatch)
    monkeypatch.setattr(
        goes_rgb_publish,
        "encode_rgba_webp",
        lambda rgba, **_kwargs: b"webp",
    )

    slot = datetime(2026, 5, 21, 18, 0, tzinfo=timezone.utc)
    ir_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 250.0,
        transform=None,
        source_metadata={"slot_time": "2026-05-21T18:00:00Z"},
    )
    goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[ir_frame],
        publish_time=datetime(2026, 5, 21, 18, 5, tzinfo=timezone.utc),
    )

    rgb_frame = goes_rgb_publish.GOESRGBBundleFrame(
        valid_time=slot + timedelta(minutes=4),
        slot_time=slot,
        rgba=np.zeros((2, 2, 4), dtype=np.uint8),
        source_metadata={"slot_time": "2026-05-21T18:00:00Z"},
    )
    rgb_result = goes_rgb_publish.publish_goes_rgb_bundle(
        data_root=tmp_path,
        frames=[rgb_frame],
        publish_time=datetime(2026, 5, 21, 18, 10, tzinfo=timezone.utc),
    )
    return rgb_result.run_id


def test_band_retention_protects_latest_rgb_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb_run_id = _publish_ir_and_rgb(tmp_path, monkeypatch)

    # Overnight band publishes create newer runs that push the dusk RGB run
    # outside keep_runs=2 unless LATEST_RGB is protected.
    for minute in (20, 35, 50):
        slot = datetime(2026, 5, 21, 18, minute - 5, tzinfo=timezone.utc)
        ir_frame = goes_publish.GOESBundleFrame(
            valid_time=slot + timedelta(minutes=2),
            slot_time=slot,
            values=np.ones((2, 2), dtype=np.float32) * 250.0,
            transform=None,
            source_metadata={"slot_time": slot.strftime("%Y-%m-%dT%H:%M:%SZ")},
        )
        goes_publish.publish_goes_bundle(
            data_root=tmp_path,
            frames=[ir_frame],
            publish_time=datetime(2026, 5, 21, 18, minute, tzinfo=timezone.utc),
        )

    config = goes_poller.GOESPollerConfig(
        data_root=tmp_path,
        cache_dir=tmp_path / "cache",
        provider="noaa",
        satellite="goes19",
        bucket="noaa-goes19",
        product="ABI-L2-CMIPC",
        sector="C",
        bands=(13,),
        poll_seconds=300,
        keep_runs=2,
        window_minutes=180,
        frame_cadence_minutes=15,
        listing_lookback_hours=5,
        object_min_age_seconds=120,
        min_object_bytes=1_000_000,
    )
    goes_poller._enforce_retention(config)

    published_rgb = tmp_path / "published" / "goes-east" / rgb_run_id
    manifest_rgb = tmp_path / "manifests" / "goes-east" / f"{rgb_run_id}.json"
    assert published_rgb.is_dir()
    assert manifest_rgb.is_file()
    assert (published_rgb / "true_color" / "fh000.webp").is_file()


def test_load_latest_rgb_falls_back_when_pointer_run_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb_run_id = _publish_ir_and_rgb(tmp_path, monkeypatch)

    # Seed true_color into a newer band-only run, then delete the dusk RGB run
    # while leaving a stale LATEST_RGB pointer (pre-fix overnight failure mode).
    slot = datetime(2026, 5, 21, 18, 20, tzinfo=timezone.utc)
    ir_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 250.0,
        transform=None,
        source_metadata={"slot_time": "2026-05-21T18:20:00Z"},
    )
    newer = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[ir_frame],
        publish_time=datetime(2026, 5, 21, 18, 25, tzinfo=timezone.utc),
    )
    assert (newer.published_run_dir / "true_color" / "fh000.webp").is_file()

    shutil.rmtree(tmp_path / "published" / "goes-east" / rgb_run_id, ignore_errors=True)
    (tmp_path / "manifests" / "goes-east" / f"{rgb_run_id}.json").unlink(missing_ok=True)

    pointer = json.loads(
        (tmp_path / "published" / "goes-east" / GOES_EAST_RGB_LATEST_FILENAME).read_text()
    )
    assert pointer["run_id"] == rgb_run_id

    loaded_run_id, frames = goes_rgb_publish.load_latest_published_rgb_frames(tmp_path)
    assert loaded_run_id == newer.run_id
    assert frames
    assert frames[0].webp_path.is_file()


def test_resolve_true_color_latest_falls_back_when_pointer_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb_run_id = _publish_ir_and_rgb(tmp_path, monkeypatch)

    slot = datetime(2026, 5, 21, 18, 20, tzinfo=timezone.utc)
    ir_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 250.0,
        transform=None,
        source_metadata={"slot_time": "2026-05-21T18:20:00Z"},
    )
    newer = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[ir_frame],
        publish_time=datetime(2026, 5, 21, 18, 25, tzinfo=timezone.utc),
    )

    shutil.rmtree(tmp_path / "published" / "goes-east" / rgb_run_id, ignore_errors=True)
    (tmp_path / "manifests" / "goes-east" / f"{rgb_run_id}.json").unlink(missing_ok=True)

    import app.main as main_module

    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", tmp_path / "manifests")

    assert main_module._latest_rgb_run_from_pointer(GOES_EAST_MODEL_ID) is None
    assert main_module._resolve_true_color_run(GOES_EAST_MODEL_ID, "latest") == newer.run_id


def test_enforce_retention_skips_protected_run_ids(tmp_path: Path) -> None:
    root = tmp_path / "published" / "goes-east"
    root.mkdir(parents=True)
    for run_id in ("20260521_1800z", "20260521_1815z", "20260521_1830z"):
        (root / run_id).mkdir()
        write_json_atomic(root / run_id / "marker.json", {"run": run_id})

    enforce_run_artifact_retention(root, keep_runs=1, protect_run_ids={"20260521_1800z"})

    assert (root / "20260521_1800z").is_dir()
    assert (root / "20260521_1830z").is_dir()
    assert not (root / "20260521_1815z").exists()
