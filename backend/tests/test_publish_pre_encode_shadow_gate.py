"""Pre-encode gate for the standalone publishers (NDFD, WPC) — dual mode.

Mirrors pipeline.py::build_frame's gate exactly:

- The ``check_pre_encode_value_sanity`` call runs UNCONDITIONALLY on every
  frame write (the Phase 2 fix — the allowlist never decides whether the
  gate runs).
- What the allowlist decides is what a failure means. Model NOT in
  ``CARTOSKY_BINARY_SAMPLING_MODELS`` (the default): shadow mode — failure
  logs the Phase C warning and the frame publishes in full, value COG
  included. Model IN the allowlist: enforced mode — failure (or a gate
  error) REJECTS the frame before any artifact is written, and even
  passing frames skip the value COG write (the grid binary + sidecar are
  the complete artifact set), matching pipeline.py's binary_only branch,
  which rejects before the sidecar and grid writes.
- Rejection propagates the way build_frame signals a failed frame to the
  scheduler: a status result the caller skips (here ``_write_*_frame``
  returns False and the bundle loop drops the frame), not an exception.

Variable choice mirrors the pipeline test's per-branch fixtures:
- NDFD ``mint`` uses the real "tmp2m" colormap spec (continuous, no
  allow_dry_frame): a flat constant field fails min == max.
- WPC ``precip_total`` uses the real "precip_total" spec, which carries
  allow_dry_frame=True with no discrete levels — flat fields PASS as dry
  frames (pinned below), so its genuinely-bad input is the nodata-ratio
  check: >95% nodata with finite pixels present.
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

from app.models.ndfd import NDFD_MODEL
from app.models.wpc import WPC_MODEL
from app.services import ndfd_publish, wpc_publish
from app.services.ndfd_source import NDFDSourceField
from app.services.wpc_source import WPCSourceField


def _set_allowlist(monkeypatch: pytest.MonkeyPatch, allowlist: str) -> None:
    if allowlist:
        monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", allowlist)
    else:
        monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS", raising=False)


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
    calls: list[str] = []
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
        "write_value_cog",
        lambda values, output_path, **kwargs: (calls.append("write_value_cog"), Path(output_path).write_bytes(b"value"))[1],
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
    return calls


def _wpc_harness(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """WPC twin of `_ndfd_harness` (colorize entrypoint is float_to_rgba)."""
    calls: list[str] = []
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
        "write_value_cog",
        lambda values, output_path, **kwargs: (calls.append("write_value_cog"), Path(output_path).write_bytes(b"value"))[1],
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
    return calls


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
        for suffix in (".json", ".val.cog.tif")
    )


# ── Shadow mode (model not allowlisted — the default) ────────────────


def test_ndfd_shadow_gate_flags_bad_frame_but_still_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A flat constant field is genuinely bad input the REAL pre-encode gate
    # rejects for mint's "tmp2m" spec (min == max, no dry-frame allowance).
    # The gate runs on every frame write; with the allowlist empty it is in
    # shadow mode, so the frame still publishes in full: value COG written,
    # sidecar written, publish returns normally.
    assert (
        ndfd_publish.check_pre_encode_value_sanity(
            _FLAT_TEMPS,
            ndfd_publish.get_color_map_spec("tmp2m"),
            var_spec_model=NDFD_MODEL.get_var("mint"),
            var_capability=NDFD_MODEL.get_var_capability("mint"),
            label="ndfd/mint flat-field pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "")
    calls = _ndfd_harness(monkeypatch)

    with caplog.at_level("WARNING"):
        result = _publish_ndfd(tmp_path, [_ndfd_frame(_FLAT_TEMPS)])

    assert result.frame_count == 1
    assert calls == ["write_value_cog"]
    staging_var = tmp_path / "staging" / "ndfd" / result.run_id / "mint"
    published_var = tmp_path / "published" / "ndfd" / result.run_id / "mint"
    assert (published_var / "fh000.val.cog.tif").is_file() or (staging_var / "fh000.val.cog.tif").is_file()
    assert any(
        "Phase C shadow gate failed" in record.getMessage() and "model=ndfd var=mint" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize("allowlist", ["", "ndfd"])
def test_ndfd_gate_runs_regardless_of_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    # The gate call is unconditional — the allowlist decides only what a
    # failure means (and whether the value COG is written), never whether
    # the check runs.
    _set_allowlist(monkeypatch, allowlist)
    calls = _ndfd_harness(monkeypatch)
    gate_calls: list[str] = []
    monkeypatch.setattr(
        ndfd_publish,
        "check_pre_encode_value_sanity",
        lambda *a, **k: (gate_calls.append(k.get("label", "")), True)[1],
    )

    result = _publish_ndfd(tmp_path, [_ndfd_frame(_GOOD_TEMPS)])

    assert result.frame_count == 1
    assert gate_calls == ["ndfd/mint/fh000"]
    # Binary-only mode skips the value COG even for a passing frame.
    assert calls == ([] if allowlist else ["write_value_cog"])


def test_ndfd_shadow_gate_failure_never_rejects_frame_when_not_allowlisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Shadow mode only: a forced gate failure must not reject the frame —
    # the shadow warning is emitted and the publish completes in full.
    # (When the model IS allowlisted, the enforcement tests below assert
    # the opposite.)
    _set_allowlist(monkeypatch, "")
    calls = _ndfd_harness(monkeypatch)
    monkeypatch.setattr(ndfd_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_ndfd(tmp_path, [_ndfd_frame(_GOOD_TEMPS)])

    assert result.frame_count == 1
    assert calls == ["write_value_cog"]
    staging_var = tmp_path / "staging" / "ndfd" / result.run_id / "mint"
    published_var = tmp_path / "published" / "ndfd" / result.run_id / "mint"
    assert (published_var / "fh000.val.cog.tif").is_file() or (staging_var / "fh000.val.cog.tif").is_file()
    assert any("Phase C shadow gate failed" in record.getMessage() for record in caplog.records)


def test_wpc_shadow_gate_flags_bad_frame_but_still_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Flatness is NOT bad input for precip_total (allow_dry_frame=True, no
    # discrete levels), so the fixture-sharpness pins below prove the branch
    # actually in play before the bad-array assertion: (a) a flat wet field
    # PASSES as a dry frame; (b) a sparse-but-valid scene at 0.90 nodata
    # PASSES the generic 0.95 threshold. The genuinely-bad array is >95%
    # nodata WITH finite pixels present — no dry carve-out applies on the
    # continuous branch.
    real_spec = wpc_publish.get_color_map_spec("precip_total")
    var_spec_model = WPC_MODEL.get_var("precip_total")
    var_capability = WPC_MODEL.get_var_capability("precip_total")

    flat_wet = np.full((40, 50), 0.4, dtype=np.float32)
    assert (
        wpc_publish.check_pre_encode_value_sanity(
            flat_wet,
            real_spec,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            label="wpc/precip_total dry-frame pin",
        )
        is True
    )
    sparse_valid = np.full((40, 50), np.nan, dtype=np.float32)
    sparse_valid.flat[:200] = np.linspace(0.1, 2.0, 200)  # 0.90 nodata, varied
    assert (
        wpc_publish.check_pre_encode_value_sanity(
            sparse_valid,
            real_spec,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            label="wpc/precip_total generic-threshold pin",
        )
        is True
    )
    # 2 finite pixels out of 2000 -> nodata ratio 0.999 > 0.95, finite pixels
    # present (not fully dry), two distinct values (not flat).
    assert (
        wpc_publish.check_pre_encode_value_sanity(
            _SPARSE_PRECIP,
            real_spec,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            label="wpc/precip_total bad-array pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "")
    calls = _wpc_harness(monkeypatch)

    with caplog.at_level("WARNING"):
        result = _publish_wpc(tmp_path, [_wpc_frame(_SPARSE_PRECIP)])

    assert result.frame_count == 1
    assert calls == ["write_value_cog"]
    staging_var = tmp_path / "staging" / "wpc" / result.run_id / "precip_total"
    published_var = tmp_path / "published" / "wpc" / result.run_id / "precip_total"
    assert (published_var / "fh006.val.cog.tif").is_file() or (staging_var / "fh006.val.cog.tif").is_file()
    assert any(
        "Phase C shadow gate failed" in record.getMessage() and "model=wpc var=precip_total" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize("allowlist", ["", "wpc"])
def test_wpc_gate_runs_regardless_of_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    _set_allowlist(monkeypatch, allowlist)
    calls = _wpc_harness(monkeypatch)
    gate_calls: list[str] = []
    monkeypatch.setattr(
        wpc_publish,
        "check_pre_encode_value_sanity",
        lambda *a, **k: (gate_calls.append(k.get("label", "")), True)[1],
    )

    result = _publish_wpc(tmp_path, [_wpc_frame(_GOOD_PRECIP)])

    assert result.frame_count == 1
    assert gate_calls == ["wpc/precip_total/fh006"]
    assert calls == ([] if allowlist else ["write_value_cog"])


def test_wpc_shadow_gate_failure_never_rejects_frame_when_not_allowlisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "")
    calls = _wpc_harness(monkeypatch)
    monkeypatch.setattr(wpc_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_wpc(tmp_path, [_wpc_frame(_GOOD_PRECIP)])

    assert result.frame_count == 1
    assert calls == ["write_value_cog"]
    staging_var = tmp_path / "staging" / "wpc" / result.run_id / "precip_total"
    published_var = tmp_path / "published" / "wpc" / result.run_id / "precip_total"
    assert (published_var / "fh006.val.cog.tif").is_file() or (staging_var / "fh006.val.cog.tif").is_file()
    assert any("Phase C shadow gate failed" in record.getMessage() for record in caplog.records)


# ── Enforced mode (model allowlisted) ────────────────────────────────
#
# Mirrors test_binary_only_frame_builds.py's GFS/HRRR/NBM guarantee for the
# publishers: a bad frame is REJECTED with write_value_cog never reached, and
# — matching pipeline.py's binary_only branch, which rejects at the gate
# BEFORE the sidecar and grid writes — the grid binary write never happens
# for the rejected frame either.


def test_ndfd_enforced_gate_rejects_bad_frame_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "ndfd")
    _ndfd_harness(monkeypatch)
    monkeypatch.setattr(ndfd_publish, "write_value_cog", _fail_if_called("write_value_cog"))
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
    assert not (published_var / "fh000.val.cog.tif").exists()


def test_ndfd_binary_only_good_frame_skips_value_cog_but_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "ndfd")
    _ndfd_harness(monkeypatch)
    monkeypatch.setattr(ndfd_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, ndfd_publish)

    with caplog.at_level("INFO"):
        result = _publish_ndfd(tmp_path, [_ndfd_frame(_GOOD_TEMPS)])

    assert result.frame_count == 1
    assert grid_calls == [("mint", 0)]
    published_var = tmp_path / "published" / "ndfd" / result.run_id / "mint"
    assert (published_var / "fh000.json").is_file()
    assert not (published_var / "fh000.val.cog.tif").exists()
    assert any(
        "Value COG write skipped (model=ndfd is binary-only)" in record.getMessage()
        for record in caplog.records
    )


def test_ndfd_enforced_gate_error_blocks_publish_instead_of_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A gate EXCEPTION gets the same treatment as a False result once
    # enforcement is live — reject, don't silently swallow.
    _set_allowlist(monkeypatch, "ndfd")
    _ndfd_harness(monkeypatch)
    monkeypatch.setattr(ndfd_publish, "write_value_cog", _fail_if_called("write_value_cog"))
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
    _set_allowlist(monkeypatch, "wpc")
    _wpc_harness(monkeypatch)
    monkeypatch.setattr(wpc_publish, "write_value_cog", _fail_if_called("write_value_cog"))
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
    assert not (published_var / "fh006.val.cog.tif").exists()


def test_wpc_binary_only_good_frame_skips_value_cog_but_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "wpc")
    _wpc_harness(monkeypatch)
    monkeypatch.setattr(wpc_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, wpc_publish)

    with caplog.at_level("INFO"):
        result = _publish_wpc(tmp_path, [_wpc_frame(_GOOD_PRECIP)])

    assert result.frame_count == 1
    assert grid_calls == [("precip_total", 6)]
    published_var = tmp_path / "published" / "wpc" / result.run_id / "precip_total"
    assert (published_var / "fh006.json").is_file()
    assert not (published_var / "fh006.val.cog.tif").exists()
    assert any(
        "Value COG write skipped (model=wpc is binary-only)" in record.getMessage()
        for record in caplog.records
    )


def test_wpc_enforced_gate_error_blocks_publish_instead_of_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "wpc")
    _wpc_harness(monkeypatch)
    monkeypatch.setattr(wpc_publish, "write_value_cog", _fail_if_called("write_value_cog"))
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
