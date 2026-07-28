"""Pre-encode gate for the standalone publishers (NDFD, WPC).

Mirrors pipeline.py::build_frame's gate exactly: the
``check_pre_encode_value_sanity`` call runs unconditionally on every frame
write and is ENFORCED — failure (or a gate error) REJECTS the frame before
any artifact is written. The gate itself and get_color_map_spec stay REAL in
these tests; everything around them is mocked.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import ndfd_publish, wpc_publish
from app.services.ndfd_source import NDFDSourceField
from app.services.wpc_source import WPCSourceField


def _fail_if_called(name: str):
    def _spy(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")

    return _spy


_NDFD_ISSUE_TIME = datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc)
_WPC_ISSUE_TIME = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

# Fixture arrays the REAL gate classifies (real colormap specs in play):
# varied in-band values pass; a flat field fails mint's "tmp2m" spec
# (min == max); >95%-nodata-with-finite-pixels fails wpc's "precip_total"
# spec (whose allow_dry_frame lets flat fields through — pinned below).
_GOOD_TEMPS = np.array([[32.0, 33.0], [34.0, 35.0]], dtype=np.float32)
_FLAT_TEMPS = np.full((2, 2), 32.0, dtype=np.float32)
_GOOD_PRECIP = np.array([[0.1, 0.2], [0.4, 0.6]], dtype=np.float32)
_SPARSE_PRECIP = np.full((40, 50), np.nan, dtype=np.float32)
_SPARSE_PRECIP.flat[0] = 0.2
_SPARSE_PRECIP.flat[1] = 0.6


def _ndfd_frame(values: np.ndarray, *, valid_offset_hours: int = 7) -> NDFDSourceField:
    return NDFDSourceField(
        valid_time=_NDFD_ISSUE_TIME + timedelta(hours=valid_offset_hours),
        issue_time=_NDFD_ISSUE_TIME,
        values=values,
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/ds.mint.bin",
        source_filename="ds.mint.bin",
        source_units="[C]",
    )


def _wpc_frame(values: np.ndarray, *, forecast_hour: int = 6) -> WPCSourceField:
    return WPCSourceField(
        forecast_hour=forecast_hour,
        valid_time=_WPC_ISSUE_TIME + timedelta(hours=forecast_hour),
        issue_time=_WPC_ISSUE_TIME,
        values=values,
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/p06m_2026070612f006.grb",
        source_filename="p06m_2026070612f006.grb",
        source_units="[kg/(m^2)]",
    )


def _ndfd_harness(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Mock everything around the gate the same way test_ndfd_publish.py does.
    Warp passes each frame's own values through unchanged, so per-frame
    fixtures reach the gate and the writers intact. The gate itself and
    get_color_map_spec stay REAL. Grid writes are disabled; enforcement tests
    re-enable them with recorders via `_grid_spies`."""
    monkeypatch.setattr(ndfd_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        ndfd_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (
            np.asarray(values, dtype=np.float32),
            from_origin(-101.0, 46.0, 1.0, 1.0),
        ),
    )
    monkeypatch.setattr(
        ndfd_publish,
        "colorize_metadata",
        lambda values, color_map_id, meta_var_key=None: {"kind": "continuous", "min": 0.0, "max": 100.0},
    )
    monkeypatch.setattr(
        ndfd_publish,
        "_build_sidecar_json",
        lambda **kwargs: {"model": kwargs["model"], "run": kwargs["run_id"], "var": kwargs["var_id"], "fh": kwargs["fh"]},
    )


def _wpc_harness(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """WPC twin of `_ndfd_harness` (colorize entrypoint is float_to_rgba)."""
    monkeypatch.setattr(wpc_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        wpc_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (
            np.asarray(values, dtype=np.float32),
            from_origin(-101.0, 46.0, 1.0, 1.0),
        ),
    )
    monkeypatch.setattr(
        wpc_publish,
        "float_to_rgba",
        lambda values, color_map_id, meta_var_key=None: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "min": 0.0, "max": 1.0},
        ),
    )
    monkeypatch.setattr(
        wpc_publish,
        "_build_sidecar_json",
        lambda **kwargs: {"model": kwargs["model"], "run": kwargs["run_id"], "var": kwargs["var_id"], "fh": kwargs["fh"]},
    )


def _grid_spies(monkeypatch: pytest.MonkeyPatch, module) -> list[tuple[str, int]]:
    """Enable grid builds with recorders so tests can assert which frames got
    a grid binary write (and that manifest building still runs)."""
    grid_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(
        module,
        "write_grid_frames_for_run_root",
        lambda **kwargs: grid_calls.append((kwargs["var"], int(kwargs["fh"]))),
    )
    monkeypatch.setattr(module, "build_grid_manifests_for_run_root", lambda **kwargs: None)
    return grid_calls


def _publish_ndfd(tmp_path: Path, frames: list[NDFDSourceField]):
    return ndfd_publish.publish_ndfd_bundle(
        data_root=tmp_path,
        issue_time=_NDFD_ISSUE_TIME,
        frames_by_var={"mint": frames},
    )


def _publish_wpc(tmp_path: Path, frames: list[WPCSourceField]):
    return wpc_publish.publish_wpc_bundle(
        data_root=tmp_path,
        issue_time=_WPC_ISSUE_TIME,
        frames_by_var={"precip_total": frames},
    )


def _no_frame_artifacts(tmp_path: Path, model: str, run_id: str, var: str, fh: int) -> bool:
    fh_str = f"fh{fh:03d}"
    return not any(
        (tmp_path / root / model / run_id / var / f"{fh_str}{suffix}").exists()
        for root in ("staging", "published")
        for suffix in (".json",)
    )


# ── Enforced gate ─────────────────────────────────────────────────────
#
# Mirrors test_binary_only_frame_builds.py's GFS/HRRR/NBM guarantee for the
# publishers: a bad frame is REJECTED at the gate BEFORE the sidecar and grid
# writes, so no artifact of any kind exists for it.


def test_ndfd_enforced_gate_rejects_bad_frame_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _ndfd_harness(monkeypatch)
    grid_calls = _grid_spies(monkeypatch, ndfd_publish)

    # Two frames of the same variable: the earlier valid time (fh000) passes
    # the REAL gate, the later one (fh001) is a flat field that fails it. The
    # bundle publishes the good frame and drops the bad one.
    good = _ndfd_frame(_GOOD_TEMPS, valid_offset_hours=7)
    bad = _ndfd_frame(_FLAT_TEMPS, valid_offset_hours=19)

    with caplog.at_level("INFO"):
        result = _publish_ndfd(tmp_path, [good, bad])

    assert result.frame_count == 1
    # Grid binary written for the good frame only — the rejection happens
    # before the grid write, never after.
    assert grid_calls == [("mint", 0)]
    assert any(
        "Pre-encode sanity gate rejected frame" in record.getMessage()
        and "model=ndfd var=mint fh001" in record.getMessage()
        and record.levelname == "ERROR"
        for record in caplog.records
    )
    # No artifact of any kind for the rejected frame; sidecar exists for the
    # good one.
    assert _no_frame_artifacts(tmp_path, "ndfd", result.run_id, "mint", 1)
    published_var = tmp_path / "published" / "ndfd" / result.run_id / "mint"
    assert (published_var / "fh000.json").is_file()


def test_ndfd_good_frame_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _ndfd_harness(monkeypatch)
    grid_calls = _grid_spies(monkeypatch, ndfd_publish)

    with caplog.at_level("INFO"):
        result = _publish_ndfd(tmp_path, [_ndfd_frame(_GOOD_TEMPS)])

    assert result.frame_count == 1
    assert grid_calls == [("mint", 0)]
    published_var = tmp_path / "published" / "ndfd" / result.run_id / "mint"
    assert (published_var / "fh000.json").is_file()


def test_ndfd_enforced_gate_error_blocks_publish_instead_of_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A gate EXCEPTION gets the same treatment as a False result once
    # enforcement is live — reject, don't silently swallow.
    _ndfd_harness(monkeypatch)
    grid_calls = _grid_spies(monkeypatch, ndfd_publish)
    monkeypatch.setattr(
        ndfd_publish,
        "check_pre_encode_value_sanity",
        _fail_if_called("check_pre_encode_value_sanity (simulated gate crash)"),
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(ValueError, match="NDFD publish requires at least one frame"):
            _publish_ndfd(tmp_path, [_ndfd_frame(_GOOD_TEMPS)])

    assert grid_calls == []
    assert any(
        "Pre-encode sanity gate errored" in record.getMessage() for record in caplog.records
    )
    assert _no_frame_artifacts(tmp_path, "ndfd", "20260706_1700z", "mint", 0)


def test_wpc_enforced_gate_rejects_bad_frame_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _wpc_harness(monkeypatch)
    grid_calls = _grid_spies(monkeypatch, wpc_publish)

    good = _wpc_frame(_GOOD_PRECIP, forecast_hour=6)
    bad = _wpc_frame(_SPARSE_PRECIP, forecast_hour=12)

    with caplog.at_level("INFO"):
        result = _publish_wpc(tmp_path, [good, bad])

    assert result.frame_count == 1
    assert grid_calls == [("precip_total", 6)]
    assert any(
        "Pre-encode sanity gate rejected frame" in record.getMessage()
        and "model=wpc var=precip_total fh012" in record.getMessage()
        and record.levelname == "ERROR"
        for record in caplog.records
    )
    assert _no_frame_artifacts(tmp_path, "wpc", result.run_id, "precip_total", 12)
    published_var = tmp_path / "published" / "wpc" / result.run_id / "precip_total"
    assert (published_var / "fh006.json").is_file()


def test_wpc_good_frame_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _wpc_harness(monkeypatch)
    grid_calls = _grid_spies(monkeypatch, wpc_publish)

    with caplog.at_level("INFO"):
        result = _publish_wpc(tmp_path, [_wpc_frame(_GOOD_PRECIP)])

    assert result.frame_count == 1
    assert grid_calls == [("precip_total", 6)]
    published_var = tmp_path / "published" / "wpc" / result.run_id / "precip_total"
    assert (published_var / "fh006.json").is_file()


def test_wpc_enforced_gate_error_blocks_publish_instead_of_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _wpc_harness(monkeypatch)
    grid_calls = _grid_spies(monkeypatch, wpc_publish)
    monkeypatch.setattr(
        wpc_publish,
        "check_pre_encode_value_sanity",
        _fail_if_called("check_pre_encode_value_sanity (simulated gate crash)"),
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(ValueError, match="WPC publish requires at least one frame"):
            _publish_wpc(tmp_path, [_wpc_frame(_GOOD_PRECIP)])

    assert grid_calls == []
    assert any(
        "Pre-encode sanity gate errored" in record.getMessage() for record in caplog.records
    )
    assert _no_frame_artifacts(tmp_path, "wpc", "20260706_1200z", "precip_total", 6)
