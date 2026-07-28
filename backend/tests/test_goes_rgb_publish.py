from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import goes_publish, goes_rgb_publish


def _configure_band_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercises the retained legacy COG publish flow for the band publisher.
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


def test_publish_goes_rgb_bundle_seeds_new_run_with_previous_latest_sibling_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_band_publish(monkeypatch)
    monkeypatch.setattr(
        goes_rgb_publish,
        "encode_rgba_webp",
        lambda rgba, **_kwargs: b"webp",
    )

    slot = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    ir_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 250.0,
        transform=None,
        source_metadata={"slot_time": "2026-05-21T12:00:00Z"},
    )
    first_result = goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[ir_frame],
        publish_time=datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc),
    )
    assert first_result.run_id == "20260521_1205z"

    rgb_frame = goes_rgb_publish.GOESRGBBundleFrame(
        valid_time=slot + timedelta(minutes=4),
        slot_time=slot,
        rgba=np.zeros((2, 2, 4), dtype=np.uint8),
        source_metadata={"slot_time": "2026-05-21T12:00:00Z"},
    )
    second_result = goes_rgb_publish.publish_goes_rgb_bundle(
        data_root=tmp_path,
        frames=[rgb_frame],
        publish_time=datetime(2026, 5, 21, 12, 10, tzinfo=timezone.utc),
    )

    assert second_result.run_id == "20260521_1210z"
    assert (second_result.published_run_dir / "ir13" / "fh000.val.cog.tif").exists()
    assert (second_result.published_run_dir / "true_color" / "fh000.webp").exists()

    manifest = json.loads(second_result.manifest_path.read_text())
    assert set(manifest["variables"]) == {"ir13", "true_color"}


def test_concurrent_band_and_rgb_same_run_id_preserve_both_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Band and RGB full-replace the same goes-east run; overlapping publishes must not drop siblings."""
    from concurrent.futures import ThreadPoolExecutor

    _configure_band_publish(monkeypatch)
    monkeypatch.setattr(
        goes_rgb_publish,
        "encode_rgba_webp",
        lambda rgba, **_kwargs: b"webp-new",
    )

    seed_slot = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    seed_ir = goes_publish.GOESBundleFrame(
        valid_time=seed_slot + timedelta(minutes=2),
        slot_time=seed_slot,
        values=np.ones((2, 2), dtype=np.float32) * 240.0,
        transform=None,
        source_metadata={"slot_time": "2026-07-28T14:00:00Z"},
    )
    seed_rgb = goes_rgb_publish.GOESRGBBundleFrame(
        valid_time=seed_slot + timedelta(minutes=4),
        slot_time=seed_slot,
        rgba=np.zeros((2, 2, 4), dtype=np.uint8),
        source_metadata={"slot_time": "2026-07-28T14:00:00Z"},
    )
    goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=[seed_ir],
        publish_time=datetime(2026, 7, 28, 14, 15, tzinfo=timezone.utc),
    )
    goes_rgb_publish.publish_goes_rgb_bundle(
        data_root=tmp_path,
        frames=[seed_rgb],
        publish_time=datetime(2026, 7, 28, 14, 15, tzinfo=timezone.utc),
    )
    # Mark seed artifacts so we can detect stale carry-forward vs fresh writes.
    seed_run = tmp_path / "published" / "goes-east" / "20260728_1415z"
    (seed_run / "ir13" / "fh000.val.cog.tif").write_bytes(b"seed-ir")
    (seed_run / "true_color" / "fh000.webp").write_bytes(b"seed-rgb")

    publish_time = datetime(2026, 7, 28, 14, 31, tzinfo=timezone.utc)
    slot = datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc)
    ir_frame = goes_publish.GOESBundleFrame(
        valid_time=slot + timedelta(minutes=2),
        slot_time=slot,
        values=np.ones((2, 2), dtype=np.float32) * 255.0,
        transform=None,
        source_metadata={"slot_time": "2026-07-28T14:30:00Z"},
    )
    rgb_frame = goes_rgb_publish.GOESRGBBundleFrame(
        valid_time=slot + timedelta(minutes=4),
        slot_time=slot,
        rgba=np.full((2, 2, 4), 7, dtype=np.uint8),
        source_metadata={"slot_time": "2026-07-28T14:30:00Z"},
    )

    errors: list[BaseException] = []

    def _publish_band() -> str:
        try:
            result = goes_publish.publish_goes_bundle(
                data_root=tmp_path,
                frames=[ir_frame],
                publish_time=publish_time,
            )
            return result.run_id
        except BaseException as exc:  # noqa: BLE001 - surface in parent thread
            errors.append(exc)
            raise

    def _publish_rgb() -> str:
        try:
            result = goes_rgb_publish.publish_goes_rgb_bundle(
                data_root=tmp_path,
                frames=[rgb_frame],
                publish_time=publish_time,
            )
            return result.run_id
        except BaseException as exc:  # noqa: BLE001 - surface in parent thread
            errors.append(exc)
            raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        band_future = pool.submit(_publish_band)
        rgb_future = pool.submit(_publish_rgb)
        run_ids = {band_future.result(timeout=30), rgb_future.result(timeout=30)}

    assert not errors
    assert run_ids == {"20260728_1431z"}
    published = tmp_path / "published" / "goes-east" / "20260728_1431z"
    ir_bytes = (published / "ir13" / "fh000.val.cog.tif").read_bytes()
    rgb_bytes = (published / "true_color" / "fh000.webp").read_bytes()
    assert ir_bytes == b"cog"
    assert rgb_bytes == b"webp-new"
    assert ir_bytes != b"seed-ir"
    assert rgb_bytes != b"seed-rgb"

    manifest = json.loads((tmp_path / "manifests" / "goes-east" / "20260728_1431z.json").read_text())
    assert set(manifest["variables"]) == {"ir13", "true_color"}
    assert (tmp_path / ".locks" / "goes-east.publish.lock").is_file()


def test_observed_model_publish_lock_serializes_cross_thread_holders(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    from app.services.publish_utils import observed_model_publish_lock

    hold = threading.Event()
    second_started = threading.Event()
    order: list[str] = []

    def first() -> None:
        with observed_model_publish_lock(tmp_path, "goes-east"):
            order.append("first-enter")
            hold.set()
            time.sleep(0.15)
            order.append("first-exit")

    def second() -> None:
        hold.wait(timeout=2)
        second_started.set()
        with observed_model_publish_lock(tmp_path, "goes-east"):
            order.append("second-enter")
            order.append("second-exit")

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(first)
        f2 = pool.submit(second)
        f1.result(timeout=5)
        f2.result(timeout=5)

    assert second_started.is_set()
    assert order == ["first-enter", "first-exit", "second-enter", "second-exit"]
