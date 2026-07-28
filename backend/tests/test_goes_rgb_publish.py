from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from rasterio.transform import from_origin
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import goes_publish, goes_rgb_publish


def _configure_band_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercises the retained legacy COG publish flow for the band publisher.
    monkeypatch.setattr(goes_publish, "grid_build_enabled", lambda: True)
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
        # Non-constant: the enforced pre-encode gate rejects flat frames.
        values=250.0 + np.arange(4, dtype=np.float32).reshape(2, 2),
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
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
    assert (second_result.published_run_dir / "ir13" / "grid" / "fh000.l0.meta.json").exists()
    assert (second_result.published_run_dir / "true_color" / "fh000.webp").exists()

    manifest = json.loads(second_result.manifest_path.read_text())
    assert set(manifest["variables"]) == {"ir13", "true_color"}
