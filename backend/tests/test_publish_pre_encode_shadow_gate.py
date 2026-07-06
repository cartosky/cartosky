"""Phase C shadow gate for the standalone publishers (NDFD, WPC).

Mirrors test_binary_only_frame_builds.py's guarantee at the SHADOW stage,
matching pipeline.py::build_frame's actual Phase C pattern: shadow logging
happens for EVERY frame write regardless of the binary-sampling allowlist —
enforcement is the separate, later, allowlist-gated step (Phase 4). So
``check_pre_encode_value_sanity`` runs unconditionally on the same array
passed to ``write_value_cog``/``write_grid_frames_for_run_root``; a
genuinely bad synthetic array FAILS the gate and logs the shadow warning,
but nothing rejects the frame yet: the value COG and sidecar still publish.

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
from datetime import datetime, timezone
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


def _ndfd_frame(values: np.ndarray) -> NDFDSourceField:
    issue_time = datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc)
    return NDFDSourceField(
        valid_time=datetime(2026, 7, 7, 0, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=values,
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/ds.mint.bin",
        source_filename="ds.mint.bin",
        source_units="[C]",
    )


def _wpc_frame(values: np.ndarray) -> WPCSourceField:
    issue_time = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    return WPCSourceField(
        forecast_hour=6,
        valid_time=datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=values,
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/p06m_2026070612f006.grb",
        source_filename="p06m_2026070612f006.grb",
        source_units="[kg/(m^2)]",
    )


def _ndfd_harness(monkeypatch: pytest.MonkeyPatch, *, warped: np.ndarray) -> list[str]:
    """Mock everything around the gate the same way test_ndfd_publish.py does,
    with `warped` as the array the gate and the COG write both receive. The
    gate itself and get_color_map_spec stay REAL."""
    calls: list[str] = []
    monkeypatch.setattr(ndfd_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        ndfd_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (warped, from_origin(-101.0, 46.0, 1.0, 1.0)),
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


def _wpc_harness(monkeypatch: pytest.MonkeyPatch, *, warped: np.ndarray) -> list[str]:
    """WPC twin of `_ndfd_harness` (colorize entrypoint is float_to_rgba)."""
    calls: list[str] = []
    monkeypatch.setattr(wpc_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        wpc_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (warped, from_origin(-101.0, 46.0, 1.0, 1.0)),
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


def _publish_ndfd(tmp_path: Path, frame: NDFDSourceField):
    return ndfd_publish.publish_ndfd_bundle(
        data_root=tmp_path,
        issue_time=frame.issue_time,
        frames_by_var={"mint": [frame]},
    )


def _publish_wpc(tmp_path: Path, frame: WPCSourceField):
    return wpc_publish.publish_wpc_bundle(
        data_root=tmp_path,
        issue_time=frame.issue_time,
        frames_by_var={"precip_total": [frame]},
    )


def test_ndfd_shadow_gate_flags_bad_frame_but_still_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A flat constant field is genuinely bad input the REAL pre-encode gate
    # rejects for mint's "tmp2m" spec (min == max, no dry-frame allowance).
    # The gate runs on every frame write (allowlist left empty here) and must
    # FAIL — but in Phase C shadow mode the frame still publishes in full:
    # value COG written, sidecar written, publish returns normally.
    bad = np.full((2, 3), 32.0, dtype=np.float32)
    assert (
        ndfd_publish.check_pre_encode_value_sanity(
            bad,
            ndfd_publish.get_color_map_spec("tmp2m"),
            var_spec_model=NDFD_MODEL.get_var("mint"),
            var_capability=NDFD_MODEL.get_var_capability("mint"),
            label="ndfd/mint flat-field pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "")
    calls = _ndfd_harness(monkeypatch, warped=bad)

    with caplog.at_level("WARNING"):
        result = _publish_ndfd(tmp_path, _ndfd_frame(np.ones((4, 5), dtype=np.float32)))

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
def test_ndfd_shadow_gate_runs_regardless_of_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    # The gate is unconditional Phase C shadow instrumentation — the
    # binary-sampling allowlist gates the LATER enforcement step (Phase 4),
    # not whether the check runs.
    _set_allowlist(monkeypatch, allowlist)
    calls = _ndfd_harness(monkeypatch, warped=np.array([[32.0, 33.0, 34.0], [35.0, 36.0, 37.0]], dtype=np.float32))
    gate_calls: list[str] = []
    monkeypatch.setattr(
        ndfd_publish,
        "check_pre_encode_value_sanity",
        lambda *a, **k: (gate_calls.append(k.get("label", "")), True)[1],
    )

    result = _publish_ndfd(tmp_path, _ndfd_frame(np.ones((4, 5), dtype=np.float32)))

    assert result.frame_count == 1
    assert gate_calls == ["ndfd/mint/fh000"]
    assert calls == ["write_value_cog"]


@pytest.mark.parametrize("allowlist", ["", "ndfd"])
def test_ndfd_shadow_gate_failure_never_rejects_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, allowlist: str
) -> None:
    # Enforcement is Phase 4's job: even a forced gate failure must not
    # reject the frame, whatever the allowlist says — only the shadow
    # warning is emitted and the publish completes in full.
    _set_allowlist(monkeypatch, allowlist)
    calls = _ndfd_harness(monkeypatch, warped=np.array([[32.0, 33.0, 34.0], [35.0, 36.0, 37.0]], dtype=np.float32))
    monkeypatch.setattr(ndfd_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_ndfd(tmp_path, _ndfd_frame(np.ones((4, 5), dtype=np.float32)))

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
    bad = np.full((40, 50), np.nan, dtype=np.float32)
    bad.flat[0] = 0.2
    bad.flat[1] = 0.6
    assert (
        wpc_publish.check_pre_encode_value_sanity(
            bad,
            real_spec,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            label="wpc/precip_total bad-array pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "")
    calls = _wpc_harness(monkeypatch, warped=bad)

    with caplog.at_level("WARNING"):
        result = _publish_wpc(tmp_path, _wpc_frame(np.ones((4, 5), dtype=np.float32)))

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
def test_wpc_shadow_gate_runs_regardless_of_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    _set_allowlist(monkeypatch, allowlist)
    calls = _wpc_harness(monkeypatch, warped=np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32))
    gate_calls: list[str] = []
    monkeypatch.setattr(
        wpc_publish,
        "check_pre_encode_value_sanity",
        lambda *a, **k: (gate_calls.append(k.get("label", "")), True)[1],
    )

    result = _publish_wpc(tmp_path, _wpc_frame(np.ones((4, 5), dtype=np.float32)))

    assert result.frame_count == 1
    assert gate_calls == ["wpc/precip_total/fh006"]
    assert calls == ["write_value_cog"]


@pytest.mark.parametrize("allowlist", ["", "wpc"])
def test_wpc_shadow_gate_failure_never_rejects_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, allowlist: str
) -> None:
    _set_allowlist(monkeypatch, allowlist)
    calls = _wpc_harness(monkeypatch, warped=np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32))
    monkeypatch.setattr(wpc_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_wpc(tmp_path, _wpc_frame(np.ones((4, 5), dtype=np.float32)))

    assert result.frame_count == 1
    assert calls == ["write_value_cog"]
    staging_var = tmp_path / "staging" / "wpc" / result.run_id / "precip_total"
    published_var = tmp_path / "published" / "wpc" / result.run_id / "precip_total"
    assert (published_var / "fh006.val.cog.tif").is_file() or (staging_var / "fh006.val.cog.tif").is_file()
    assert any("Phase C shadow gate failed" in record.getMessage() for record in caplog.records)
